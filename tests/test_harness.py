from __future__ import annotations

import http.client
import io
import json
import shutil
import threading
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from echoweave_agent_core import AgentCore, AgentCoreConfig, AgentCoreHookBase, CoreTurnContext, SessionRuntimeFacade, TurnRequest, TurnResult
from echoweave_coding_agent import CodingAgent, CodingAgentConfig
from echoweave_harness.audit import configure_audit, read_audit_events
from echoweave_harness.evaluation import score_eval_case
from echoweave_harness.feedback import (
    suggest_eval_hardening,
    suggest_harness_improvements,
    suggestions_to_eval_cases,
    write_eval_fixtures,
    write_feedback_backlog,
)
from echoweave_harness.metrics import compute_harness_metrics
from echoweave_harness.policy import HarnessPolicyEvaluator, configure_harness_policy, load_harness_policy
from echoweave_runtime.app import build_registry
from echoweave_runtime.extensions.base import RetrievalChunk
from echoweave_runtime.extensions.hybrid_rag_provider import HybridRagProviderConfig, HybridRagRetrievalProvider
from echoweave_runtime.rag.pipeline import Bm25Reranker, LocalMultiQueryRewriter
from echoweave_runtime.rag.model import RagSearchOptions
from echoweave_runtime.rag.pgvector_hybrid import PgVectorHybridConfig, PgVectorHybridRagModel, collect_chunks
from echoweave_runtime.models.demo import AgentResponse, SequenceModelClient, tool_response
from echoweave_runtime.session.store import SessionStore
from echoweave_social.adapters.astrbot_event import AstrBotEventAdapter
from echoweave_social.adapters.feishu import FeishuAdapter
from echoweave_social.adapters.generic_webhook import GenericWebhookAdapter
from echoweave_social.adapters.onebot_v11 import OneBotV11Adapter
from echoweave_social.adapters.wechat_official import WeChatOfficialAdapter
from echoweave_social.agent_runtime import SocialAgentConfig, EchoWeaveSocialAgent
from echoweave_social.agent_schema import SocialMessage
from echoweave_social.backend import EchoWeaveBackend, EchoWeaveBackendConfig
from echoweave_web.cli import app
from echoweave_social.config import EchoWeaveConfig
from echoweave_web.server import HubWebhookServer
from echoweave_web.server import _read_chunked_body
from echoweave_social.onebot_client import OneBotHttpClient
from echoweave_social.schema import EchoWeaveEvent, EchoWeaveReply


@contextmanager
def _local_tmp():
    path = Path.cwd() / ".test-data" / uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_harness_audit_records_messages_files_commands_and_metrics() -> None:
    with _local_tmp() as tmp_path:
        audit_path = tmp_path / "audit.jsonl"
        configure_audit(audit_path)
        configure_harness_policy({"command_deny_patterns": ["forbidden-command"]})
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "visible.txt").write_text("hello", encoding="utf-8")
        registry = build_registry(workspace)
        assert registry.get("read").execute({"path": "visible.txt"}) == "hello"
        with pytest.raises(PermissionError):
            registry.get("bash").execute({"command": "forbidden-command"})
        backend = EchoWeaveBackend(
            EchoWeaveBackendConfig(
                default_workspace=workspace,
                state_path=tmp_path / "state.json",
                sandbox_root=tmp_path / "sandboxes",
                harness_audit_enabled=True,
                harness_audit_path=audit_path,
            )
        )
        reply = backend.handle(EchoWeaveEvent(platform="generic", conversation_id="room", sender_id="user", text="hi"))

        events = read_audit_events(audit_path)
        metrics = compute_harness_metrics(events)

        assert reply.text == "echo: hi"
        assert any(event.category == "message" and event.action == "inbound" for event in events)
        assert any(event.category == "file" and event.action == "read" and event.status == "ok" for event in events)
        assert any(event.category == "command" and event.action == "policy" and event.status == "blocked" for event in events)
        assert metrics.tool_call_success_rate is not None
        assert metrics.policy_block_rate is not None
        assert "command" in metrics.category_status_counts
        assert metrics.total_events >= 4
        suggestions = suggest_harness_improvements(events)
        feedback_path = tmp_path / "harness-feedback.jsonl"
        assert write_feedback_backlog(feedback_path, suggestions, source_audit_log=str(audit_path)) == len(suggestions)
        assert feedback_path.exists()
        if suggestions:
            feedback = [json.loads(line) for line in feedback_path.read_text(encoding="utf-8").splitlines()]
            assert feedback[0]["status"] == "open"
            assert feedback[0]["source_audit_log"] == str(audit_path)
            assert "evidence" in feedback[0]["suggestion"]
            assert "action" in feedback[0]["suggestion"]
        configure_harness_policy(None)


def test_harness_eval_scorecard_scores_tools_rag_and_policy() -> None:
    runtime_events = [
        {"type": "tool_call_start", "payload": {"name": "read"}},
        {"type": "retrieval_end", "payload": {"retrieval": {"hits": [{"source": "docs/architecture.md"}]}}},
        {"type": "policy.decision", "payload": {"policy": {"decision": "deny"}}},
    ]

    scorecard = score_eval_case(
        {
            "expected_contains": ["done"],
            "expected_tools": ["read"],
            "forbidden_tools": ["bash"],
            "expected_rag_sources": ["architecture.md"],
            "expected_policy_blocks": 1,
        },
        reply="done, checked the architecture",
        runtime_events=runtime_events,
    )

    assert scorecard.passed
    assert scorecard.overall_score == 1.0
    names = {item.name for item in scorecard.criteria}
    assert {"answer_quality", "tool_call_correctness", "rag_hit_rate", "approval_or_policy_hit_rate"} <= names


def test_harness_policy_can_gate_model_skill_and_rag() -> None:
    policy = load_harness_policy(
        {
            "session_model_allowlist": ["deepseek-chat"],
            "session_skill_allowlist": ["code-review"],
            "session_rag_enabled": True,
        }
    )
    evaluator = HarnessPolicyEvaluator(policy)

    assert evaluator.evaluate_model("deepseek-chat").allowed
    assert evaluator.evaluate_skill("code-review").allowed
    assert evaluator.evaluate_rag(True).allowed
    assert evaluator.evaluate_model("claude-sonnet").reason_code == "harness.model.not_allowed"
    assert evaluator.evaluate_skill("shell-admin").reason_code == "harness.skill.not_allowed"
    assert evaluator.evaluate_rag(False).reason_code == "harness.rag.not_allowed"


def test_eval_scorecard_failures_generate_hardening_suggestions() -> None:
    scorecard = score_eval_case(
        {
            "expected_contains": ["fixed"],
            "expected_tools": ["edit"],
            "expected_rag_sources": ["design.md"],
        },
        reply="not yet",
        runtime_events=[{"type": "tool_call_start", "payload": {"name": "read"}}],
    )

    suggestions = suggest_eval_hardening("case-1", scorecard)

    assert not scorecard.passed
    assert suggestions
    assert {item.metric for item in suggestions} >= {"answer_quality", "tool_call_correctness", "rag_hit_rate"}
    assert all(item.action for item in suggestions)


def test_hardening_suggestions_can_generate_eval_fixtures() -> None:
    with _local_tmp() as tmp_path:
        scorecard = score_eval_case(
            {"expected_contains": ["fixed"], "expected_tools": ["edit"]},
            reply="not yet",
            runtime_events=[{"type": "tool_call_start", "payload": {"name": "read"}}],
        )
        suggestions = suggest_eval_hardening("case-1", scorecard)
        cases = suggestions_to_eval_cases(suggestions, prefix="regression")
        out = tmp_path / "generated-eval.json"

        written = write_eval_fixtures(out, suggestions, prefix="regression")

        assert cases
        assert written == len(cases)
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["cases"][0]["id"].startswith("regression-")
        assert "prompt" in data["cases"][0]
        assert data["cases"][0]["suggested_action"]
