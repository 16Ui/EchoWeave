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

from echoweave_agent_core import (
    AgentCore,
    AgentCoreConfig,
    AgentCoreHookBase,
    CoreTurnContext,
    InvalidTurnTransition,
    SessionRuntimeFacade,
    TurnFailureKind,
    TurnRequest,
    TurnResult,
    TurnState,
    TurnStateMachine,
)
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


def test_agent_core_facade_runs_turn_and_session_ops() -> None:
    from echoweave_runtime.runtime.session_runtime import SessionRuntimeFacade as RuntimeCompatSessionRuntimeFacade

    with _local_tmp() as tmp_path:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        session_store = SessionStore(workspace / "echoweave-data" / "sessions")
        core = AgentCore.from_config(
            AgentCoreConfig(
                model_client=SequenceModelClient([AgentResponse(text="core ok")]),
                tool_registry=build_registry(workspace),
                session_store=session_store,
                metadata={"workspace": str(workspace), "provider": "demo"},
            )
        )

        result = core.run_turn(TurnRequest(prompt="hello core", resume=False))
        checkpoint = core.create_checkpoint(result.session_path, label="before-change")
        sessions = core.list_sessions()
        checkpoints = core.list_checkpoints(result.session_path)
        replay = core.replay_from_checkpoint(result.session_path, checkpoint["id"])

        assert result.text == "core ok"
        assert RuntimeCompatSessionRuntimeFacade is SessionRuntimeFacade
        assert result.session_id
        assert result.session_path.exists()
        assert sessions[0]["session_id"] == result.session_id
        assert checkpoints[0]["id"] == checkpoint["id"]
        assert replay["checkpoint_id"] == checkpoint["id"]
def test_agent_core_hooks_can_shape_turns() -> None:
    class PrefixHook(AgentCoreHookBase):
        def __init__(self) -> None:
            self.before_seen: list[str] = []
            self.after_seen: list[str] = []

        def before_turn(self, context: CoreTurnContext, request: TurnRequest) -> TurnRequest:
            self.before_seen.append(context.session_id)
            return TurnRequest(
                prompt=f"[hooked] {request.prompt}",
                session_path=request.session_path,
                resume=request.resume,
                history=request.history,
                summary=request.summary,
                metadata={**request.metadata, "hooked": True},
            )

        def after_turn(self, context: CoreTurnContext, request: TurnRequest, result: TurnResult) -> TurnResult:
            self.after_seen.append(request.prompt)
            return TurnResult(
                text=f"{result.text}!",
                session_path=result.session_path,
                session_id=result.session_id,
                history=result.history,
                summary=result.summary,
                metadata={**result.metadata, "after_hook": True},
            )

    with _local_tmp() as tmp_path:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        hook = PrefixHook()
        core = AgentCore.from_config(
            AgentCoreConfig(
                model_client=SequenceModelClient([AgentResponse(text="hook ok")]),
                tool_registry=build_registry(workspace),
                session_store=SessionStore(workspace / "echoweave-data" / "sessions"),
                hooks=(hook,),
            )
        )

        result = core.run_turn(TurnRequest(prompt="hello", resume=False))

        assert result.text == "hook ok!"
        assert result.metadata["after_hook"] is True
        assert hook.before_seen == [result.session_id]
        assert hook.after_seen == ["[hooked] hello"]


def test_turn_state_machine_rejects_transitions_from_terminal_state() -> None:
    machine = TurnStateMachine()

    machine.transition(TurnState.RUNNING)
    machine.transition(TurnState.COMPLETED)

    with pytest.raises(InvalidTurnTransition, match="completed -> running"):
        machine.transition(TurnState.RUNNING)


def test_execute_turn_persists_checkpoint_and_correlated_state() -> None:
    with _local_tmp() as tmp_path:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        emitted: list[str] = []
        store = SessionStore(workspace / "echoweave-data" / "sessions")
        core = AgentCore.from_config(
            AgentCoreConfig(
                model_client=SequenceModelClient([AgentResponse(text="durable ok")]),
                tool_registry=build_registry(workspace),
                session_store=store,
                event_sink=emitted.append,
            )
        )

        outcome = core.execute_turn(TurnRequest(prompt="durable", resume=False))

        assert outcome.succeeded is True
        assert outcome.require_result().text == "durable ok"
        assert outcome.checkpoint is not None
        events = store.read_events(outcome.session_path)
        states = [event.payload for event in events if event.type == "turn.state_changed"]
        assert [state["state"] for state in states] == ["created", "running", "completed"]
        assert {state["turn_id"] for state in states} == {outcome.turn_id}
        runtime_events = [json.loads(line) for line in emitted]
        turn_start = next(event for event in runtime_events if event["type"] == "turn_start")
        assert turn_start["payload"]["turn_id"] == outcome.turn_id
        assert turn_start["payload"]["trace_id"] == outcome.trace_id


def test_execute_turn_classifies_timeout_and_legacy_api_reraises_original_error() -> None:
    class TimeoutModel(SequenceModelClient):
        def generate(self, messages, tools, options=None):
            raise TimeoutError("provider deadline exceeded")

    with _local_tmp() as tmp_path:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        store = SessionStore(workspace / "echoweave-data" / "sessions")
        core = AgentCore.from_config(
            AgentCoreConfig(
                model_client=TimeoutModel([]),
                tool_registry=build_registry(workspace),
                session_store=store,
            )
        )

        outcome = core.execute_turn(TurnRequest(prompt="timeout", resume=False))

        assert outcome.state is TurnState.TIMED_OUT
        assert outcome.failure is not None
        assert outcome.failure.kind is TurnFailureKind.TIMEOUT
        assert outcome.failure.retryable is True
        assert outcome.checkpoint is not None
        states = [
            event.payload["state"]
            for event in store.read_events(outcome.session_path)
            if event.type == "turn.state_changed"
        ]
        assert states == ["created", "running", "timed_out"]

        with pytest.raises(TimeoutError, match="provider deadline exceeded"):
            core.run_turn(TurnRequest(prompt="legacy timeout", resume=False))


def test_execute_turn_stops_before_model_when_checkpoint_creation_fails() -> None:
    class BrokenCheckpointStore(SessionStore):
        def create_checkpoint(self, *args, **kwargs):
            raise OSError("checkpoint disk unavailable")

    with _local_tmp() as tmp_path:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        model = SequenceModelClient([AgentResponse(text="must not run")])
        store = BrokenCheckpointStore(workspace / "echoweave-data" / "sessions")
        core = AgentCore.from_config(
            AgentCoreConfig(
                model_client=model,
                tool_registry=build_registry(workspace),
                session_store=store,
            )
        )

        outcome = core.execute_turn(TurnRequest(prompt="checkpoint first", resume=False))

        assert outcome.state is TurnState.FAILED
        assert outcome.failure is not None
        assert outcome.failure.kind is TurnFailureKind.CHECKPOINT
        assert outcome.failure.retryable is True
        assert model._index == 0
