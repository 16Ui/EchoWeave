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
from echoweave_harness.feedback import suggest_harness_improvements, write_feedback_backlog
from echoweave_harness.metrics import compute_harness_metrics
from echoweave_harness.policy import configure_harness_policy
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


def test_cli_once_json() -> None:
    with _local_tmp() as tmp_path:
        result = CliRunner().invoke(
            app,
            [
                "once",
                "--cwd",
                str(tmp_path),
                "--state-path",
                str(tmp_path / "state.json"),
                "--text",
                "hello cli",
                "--json",
            ],
        )

        assert result.exit_code == 0
        assert '"adapter": "generic"' in result.stdout
        assert '"text": "echo: hello cli"' in result.stdout
def test_cli_init_creates_local_config() -> None:
    with _local_tmp() as tmp_path:
        output = tmp_path / "config.local.json"
        result = CliRunner().invoke(
            app,
            [
                "init",
                "--output",
                str(output),
                "--workspace",
                str(tmp_path / "repo"),
            ],
        )

        assert result.exit_code == 0
        data = json.loads(output.read_text(encoding="utf-8"))
        assert data["adapter"] == "onebot-v11"
        assert data["workspace"] == str((tmp_path / "repo").resolve())
        assert data["webhook_token"]
        assert data["web_allow_url_token"] is False
        assert data["web_session_ttl_seconds"] == 28800
        assert data["orphan_recovery_enabled"] is False
        assert data["orphan_recovery_scan_interval_seconds"] == 30.0
        assert data["orphan_recovery_max_per_scan"] == 4
        assert data["orphan_recovery_max_attempts_per_turn"] == 3
        assert data["default_model_profile"] == "demo-echo"
        assert data["model_profiles"]["demo-echo"]["label"] == "Demo / 本地 Echo"
        assert data["model_profiles"]["deepseek-chat"]["provider"] == "deepseek"
        assert "Webhook URL for NapCat" in result.stdout
def test_config_loads_mapping_paths_and_tokens() -> None:
    with _local_tmp() as tmp_path:
        cfg = EchoWeaveConfig.from_mapping(
            {
                "adapter": "onebot-v11",
                "workspace": str(tmp_path / "repo"),
                "state_path": str(tmp_path / "state.json"),
                "sandbox_root": str(tmp_path / "sandboxes"),
                "model_profiles": {
                    "fast": {"provider": "demo", "model": None},
                    "deepseek": {"provider": "deepseek", "model": "deepseek-chat"},
                },
                "ai_providers": {
                    "local-llm": {
                        "type": "openai-compatible",
                        "base_url": "http://127.0.0.1:1234/v1",
                        "api_key_env": "LOCAL_LLM_API_KEY",
                        "default_model": "local-model",
                    }
                },
                "default_model_profile": "deepseek",
                "rag_enabled": True,
                "rag_backend": "pgvector_hybrid_bgem3",
                "rag_pgvector_dsn": "postgresql://user:pass@localhost:5432/echoweave",
                "rag_pgvector_table": "rag_chunks",
                "rag_embedding_model": "BAAI/bge-m3",
                "rag_auto_index": True,
                "rag_vector_weight": 0.7,
                "rag_bm25_weight": 0.3,
                "rag_query_rewrite_enabled": True,
                "rag_query_rewrite_strategy": "local_multi_query",
                "rag_query_rewrite_max_queries": 4,
                "rag_rerank_enabled": True,
                "rag_rerank_strategy": "bm25",
                "rag_rerank_candidate_multiplier": 5,
                "rag_rerank_original_score_weight": 0.6,
                "rag_rerank_bm25_weight": 0.4,
                "global_enabled_skills": ["search_workspace"],
                "session_enabled_skills": ["run_pytest_smoke"],
                "sse_enabled": False,
                "webhook_token": "secret",
                "web_allow_url_token": True,
                "web_session_ttl_seconds": 120,
                "onebot_api_url": "http://127.0.0.1:3000",
                "admins": ["42"],
                "allowed_users": ["42", "43"],
                "allowed_groups": ["123"],
                "blocked_users": ["99"],
                "require_mention_in_group": True,
                "bot_ids": ["777"],
                "admin_only_commands": ["approve", "deny", "bind", "rag:index"],
                "approval_timeout_seconds": 120,
                "orphan_recovery_enabled": True,
                "orphan_recovery_scan_interval_seconds": 12.5,
                "orphan_recovery_max_per_scan": 6,
                "orphan_recovery_max_attempts_per_turn": 4,
            }
        )

        assert cfg.adapter == "onebot-v11"
        assert cfg.workspace == (tmp_path / "repo").resolve()
        assert cfg.state_path == (tmp_path / "state.json").resolve()
        assert cfg.sandbox_root == (tmp_path / "sandboxes").resolve()
        assert cfg.model_profiles
        assert cfg.model_profiles["deepseek"]["model"] == "deepseek-chat"
        assert cfg.ai_providers
        assert cfg.ai_providers["local-llm"]["default_model"] == "local-model"
        assert cfg.default_model_profile == "deepseek"
        assert cfg.rag_enabled is True
        assert cfg.rag_backend == "pgvector_hybrid_bgem3"
        assert cfg.rag_pgvector_dsn == "postgresql://user:pass@localhost:5432/echoweave"
        assert cfg.rag_pgvector_table == "rag_chunks"
        assert cfg.rag_embedding_model == "BAAI/bge-m3"
        assert cfg.rag_auto_index is True
        assert cfg.rag_vector_weight == 0.7
        assert cfg.rag_bm25_weight == 0.3
        assert cfg.rag_query_rewrite_enabled is True
        assert cfg.rag_query_rewrite_strategy == "local_multi_query"
        assert cfg.rag_query_rewrite_max_queries == 4
        assert cfg.rag_rerank_enabled is True
        assert cfg.rag_rerank_strategy == "bm25"
        assert cfg.rag_rerank_candidate_multiplier == 5
        assert cfg.rag_rerank_original_score_weight == 0.6
        assert cfg.rag_rerank_bm25_weight == 0.4
        assert cfg.global_enabled_skills == ("search_workspace",)
        assert cfg.session_enabled_skills == ("run_pytest_smoke",)
        assert cfg.sse_enabled is False
        assert cfg.webhook_token == "secret"
        assert cfg.web_allow_url_token is True
        assert cfg.web_session_ttl_seconds == 120
        assert cfg.onebot_api_url == "http://127.0.0.1:3000"
        assert cfg.admins == ("42",)
        assert cfg.allowed_users == ("42", "43")
        assert cfg.allowed_groups == ("123",)
        assert cfg.blocked_users == ("99",)
        assert cfg.require_mention_in_group is True
        assert cfg.bot_ids == ("777",)
        assert cfg.admin_only_commands == ("approve", "deny", "bind", "rag:index")
        assert cfg.approval_timeout_seconds == 120
        assert cfg.orphan_recovery_enabled is True
        assert cfg.orphan_recovery_scan_interval_seconds == 12.5
        assert cfg.orphan_recovery_max_per_scan == 6
        assert cfg.orphan_recovery_max_attempts_per_turn == 4


def test_reliability_demo_command_writes_eval_and_registers_trace() -> None:
    with _local_tmp() as tmp_path:
        state_path = tmp_path / "state.json"
        result = CliRunner().invoke(
            app,
            [
                "demo",
                "--cwd",
                str(tmp_path / "workspace"),
                "--output-root",
                str(tmp_path / "artifacts"),
                "--state-path",
                str(state_path),
                "--json",
            ],
        )

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["ok"] is True
        assert payload["report"]["passed_count"] == 4
        assert Path(payload["report"]["report_path"]).is_file()
        state = json.loads(state_path.read_text(encoding="utf-8"))
        record = state["sessions"][payload["conversation_key"]]
        assert record["demo"] is True
        assert record["runtime_session"] == payload["report"]["session_path"]


def test_model_factory_supports_openai_compatible_provider(monkeypatch) -> None:
    from echoweave_runtime.models.factory import create_model_client, get_provider_capabilities

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    client = create_model_client("deepseek", None)
    capabilities = get_provider_capabilities("deepseek")

    assert capabilities.supports_stream is True
    assert getattr(client, "_client").model == "deepseek-chat"


def test_ai_provider_registry_can_register_custom_provider() -> None:
    from echoweave_ai import AIProviderRegistration, ProviderCapabilities, create_ai_model_from_profile, list_ai_providers, register_ai_provider

    def factory(profile, model):
        return SequenceModelClient([AgentResponse(text=f"custom:{model}")]), ProviderCapabilities()

    register_ai_provider(
        AIProviderRegistration(
            name="unit-ai",
            aliases=("unit-alias",),
            default_model="unit-model",
            factory=factory,
            capabilities=ProviderCapabilities(),
            description="unit test provider",
        )
    )

    client, capabilities = create_ai_model_from_profile({"provider": "unit-alias"})
    response = client.generate([], [])

    assert response.text == "custom:unit-model"
    assert capabilities is not None
    assert any(item["name"] == "unit-ai" for item in list_ai_providers())


def test_ai_provider_registry_reports_missing_api_key_clearly(monkeypatch) -> None:
    from echoweave_ai import create_ai_model_from_profile

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        create_ai_model_from_profile({"provider": "deepseek", "model": "deepseek-chat"})


def test_ai_provider_registry_can_register_openai_compatible_from_config(monkeypatch) -> None:
    from echoweave_ai import create_ai_model_from_profile, list_ai_providers, register_ai_providers_from_config

    created: list[dict[str, str | None]] = []

    class FakeOpenAIModelClient:
        def __init__(self, model: str, *, api_key: str | None = None, base_url: str | None = None) -> None:
            created.append({"model": model, "api_key": api_key, "base_url": base_url})

    monkeypatch.setenv("LOCAL_LLM_API_KEY", "local-secret")
    monkeypatch.setattr("echoweave_ai.providers.OpenAIModelClient", FakeOpenAIModelClient)

    register_ai_providers_from_config(
        {
            "local-web": {
                "type": "openai-compatible",
                "base_url": "http://127.0.0.1:1234/v1",
                "api_key_env": "LOCAL_LLM_API_KEY",
                "default_model": "local-model",
                "aliases": ["local-web-alias"],
            }
        }
    )
    client, capabilities = create_ai_model_from_profile({"provider": "local-web-alias"})

    assert client is not None
    assert capabilities is not None
    assert created == [{"model": "local-model", "api_key": "local-secret", "base_url": "http://127.0.0.1:1234/v1"}]
    assert any(item["name"] == "local-web" for item in list_ai_providers())


def test_openai_message_conversion_downgrades_orphaned_tool_results() -> None:
    from echoweave_runtime.models.openai import _to_openai_messages

    converted = _to_openai_messages(
        [
            {"role": "user", "content": "继续"},
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool-orphan",
                        "content": "orphaned result",
                    }
                ],
            },
        ]
    )

    assert not any(message.get("role") == "tool" for message in converted)
    assert converted[-1]["role"] == "user"
    assert "没有对应 tool_calls" in converted[-1]["content"]


def test_openai_message_conversion_keeps_valid_tool_call_pairs() -> None:
    from echoweave_runtime.models.openai import _to_openai_messages

    converted = _to_openai_messages(
        [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool-1",
                        "name": "read_file",
                        "input": {"path": "README.md"},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool-1",
                        "content": "file content",
                    }
                ],
            },
        ]
    )

    assert converted[-2]["role"] == "assistant"
    assert converted[-2]["tool_calls"][0]["id"] == "tool-1"
    assert converted[-1] == {"role": "tool", "tool_call_id": "tool-1", "content": "file content"}


def test_openai_message_conversion_downgrades_incomplete_tool_calls() -> None:
    from echoweave_runtime.models.openai import _to_openai_messages

    converted = _to_openai_messages(
        [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool-1",
                        "name": "read_file",
                        "input": {"path": "README.md"},
                    }
                ],
            },
            {"role": "user", "content": "继续"},
        ]
    )

    assert not any(message.get("tool_calls") for message in converted)
    assert not any(message.get("role") == "tool" for message in converted)
    assert converted[-2]["role"] == "user"
    assert "未完整配对的工具调用" in converted[-2]["content"]
    assert "缺失 tool_result: tool-1" in converted[-2]["content"]
    assert converted[-1] == {"role": "user", "content": "继续"}


def test_openai_message_conversion_downgrades_partially_completed_tool_calls() -> None:
    from echoweave_runtime.models.openai import _to_openai_messages

    converted = _to_openai_messages(
        [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tool-1", "name": "read_file", "input": {"path": "a.md"}},
                    {"type": "tool_use", "id": "tool-2", "name": "read_file", "input": {"path": "b.md"}},
                ],
            },
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "tool-1", "content": "a"}],
            },
            {"role": "user", "content": "继续"},
        ]
    )

    assert not any(message.get("tool_calls") for message in converted)
    assert not any(message.get("role") == "tool" for message in converted)
    assert "tool_result tool-1" in converted[-2]["content"]
    assert "缺失 tool_result: tool-2" in converted[-2]["content"]
