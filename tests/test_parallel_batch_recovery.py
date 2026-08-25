from __future__ import annotations

import shutil
import threading
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest

from echoweave_agent_core import (
    AgentCore,
    AgentCoreConfig,
    RecoverTurnRequest,
    TurnFailureKind,
    TurnRequest,
    TurnState,
)
from echoweave_runtime.models.base import ModelClient
from echoweave_runtime.session.store import SessionStore
from echoweave_runtime.tool_batches import ToolBatchLedger
from echoweave_runtime.tool_invocations import ToolEffect, ToolInvocationLedger
from echoweave_runtime.tools_base import ToolRegistry
from echoweave_runtime.types import AgentResponse, ToolCall


@contextmanager
def _local_tmp():
    path = Path.cwd() / ".test-data" / uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


class _RecoveringModel(ModelClient):
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.index = 0

    def generate(self, messages, tools, options=None):
        response = self.responses[min(self.index, len(self.responses) - 1)]
        self.index += 1
        if isinstance(response, BaseException):
            raise response
        return response


class _ReadOnlyCountingTool:
    effect = "read_only"
    description = "Read-only counter for batch recovery tests"
    input_schema = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
        "additionalProperties": False,
    }

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls = 0

    def execute(self, arguments):
        self.calls += 1
        return f"{self.name}:{arguments['value']}:call={self.calls}"


class _FailOneBatchCompletionStore(SessionStore):
    def __init__(self, sessions_dir: Path, failed_tool_call_id: str) -> None:
        super().__init__(sessions_dir)
        self.failed_tool_call_id = failed_tool_call_id
        self.failed = False
        self._failure_lock = threading.Lock()

    def append(self, session_path, event_type, payload):
        if event_type == "tool.invocation_completed" and payload.get("tool_call_id") == self.failed_tool_call_id:
            with self._failure_lock:
                if not self.failed:
                    self.failed = True
                    raise OSError("injected batch completion persistence failure")
        return super().append(session_path, event_type, payload)


def _registry(*tools: _ReadOnlyCountingTool) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return registry


def _batch_response(first_value: str = "A") -> AgentResponse:
    return AgentResponse(
        tool_calls=[
            ToolCall(id="batch-call-1", name="batch_read_one", input={"value": first_value}),
            ToolCall(id="batch-call-2", name="batch_read_two", input={"value": "B"}),
        ]
    )


def test_batch_ledger_uses_stable_position_and_rejects_changed_members() -> None:
    with _local_tmp() as tmp_path:
        store = SessionStore(tmp_path / "sessions")
        session_path = store.create()
        ledger = ToolBatchLedger(store)
        members = [
            {"index": 1, "id": "call-1", "name": "read", "input": {"path": "a.txt"}},
            {"index": 2, "id": "call-2", "name": "read", "input": {"path": "b.txt"}},
        ]

        started = ledger.prepare(
            session_path,
            turn_id="stable-turn",
            trace_id="trace-1",
            sequence=1,
            mode="parallel",
            members=members,
        )
        resumed = ledger.prepare(
            session_path,
            turn_id="stable-turn",
            trace_id="trace-2",
            sequence=1,
            mode="parallel",
            members=members,
        )
        changed = [*members]
        changed[0] = {**changed[0], "input": {"path": "changed.txt"}}
        conflict = ledger.prepare(
            session_path,
            turn_id="stable-turn",
            trace_id="trace-3",
            sequence=1,
            mode="parallel",
            members=changed,
        )

        assert started.action == "start"
        assert resumed.action == "resume"
        assert resumed.batch_key == started.batch_key
        assert resumed.attempt == 2
        assert conflict.action == "conflict"
        assert conflict.batch_key == started.batch_key
        assert [
            event.type
            for event in store.read_events(session_path)
            if event.type.startswith("tool.batch_")
        ] == ["tool.batch_started", "tool.batch_resumed", "tool.batch_conflict"]


def test_completion_persistence_failure_releases_same_runtime_in_flight_guard() -> None:
    with _local_tmp() as tmp_path:
        store = _FailOneBatchCompletionStore(tmp_path / "sessions", "batch-call-1")
        session_path = store.create()
        ledger = ToolInvocationLedger(store)
        batch = {"id": "batch-key", "sequence": 1, "size": 1, "index": 1}
        first = ledger.prepare(
            session_path,
            turn_id="turn",
            trace_id="trace-1",
            tool_call_id="batch-call-1",
            tool_name="read",
            tool_input={"path": "a.txt"},
            effect=ToolEffect.READ_ONLY,
            batch=batch,
        )

        with pytest.raises(OSError, match="injected batch completion"):
            ledger.complete(
                session_path,
                first,
                turn_id="turn",
                trace_id="trace-1",
                tool_call_id="batch-call-1",
                tool_name="read",
                outcome={"status": "ok", "content": "A"},
                batch=batch,
            )

        retry = ledger.prepare(
            session_path,
            turn_id="turn",
            trace_id="trace-2",
            tool_call_id="batch-call-1",
            tool_name="read",
            tool_input={"path": "a.txt"},
            effect=ToolEffect.READ_ONLY,
            batch=batch,
        )
        assert retry.action == "execute"
        assert retry.attempt == 2


def test_partial_parallel_batch_resumes_safe_member_and_reuses_completed_member() -> None:
    with _local_tmp() as tmp_path:
        store = _FailOneBatchCompletionStore(tmp_path / "sessions", "batch-call-1")
        first_tool = _ReadOnlyCountingTool("batch_read_one")
        second_tool = _ReadOnlyCountingTool("batch_read_two")
        registry = _registry(first_tool, second_tool)
        batch = _batch_response()
        first_core = AgentCore.from_config(
            AgentCoreConfig(
                model_client=_RecoveringModel([batch]),
                tool_registry=registry,
                session_store=store,
                tool_execution_mode="parallel",
            )
        )

        suspended = first_core.execute_turn(TurnRequest(prompt="run recoverable batch", resume=False))

        assert suspended.state is TurnState.SUSPENDED
        assert suspended.failure is not None
        assert suspended.failure.kind is TurnFailureKind.INDETERMINATE_TOOL
        assert suspended.checkpoint is not None
        assert first_tool.calls == 1
        assert second_tool.calls == 1
        first_events = store.read_events(suspended.session_path)
        suspended_batch = next(event for event in first_events if event.type == "tool.batch_suspended")
        assert suspended_batch.payload["recovery_summary"]["counts"] == {
            "retryable": 1,
            "completed": 1,
        }

        recovered_core = AgentCore.from_config(
            AgentCoreConfig(
                model_client=_RecoveringModel([batch, AgentResponse(text="batch recovered")]),
                tool_registry=registry,
                session_store=store,
                tool_execution_mode="parallel",
            )
        )
        recovered = recovered_core.recover_turn(
            RecoverTurnRequest(
                session_path=suspended.session_path,
                checkpoint_id=str(suspended.checkpoint["id"]),
            )
        )

        assert recovered.succeeded is True
        assert recovered.require_result().text == "batch recovered"
        assert first_tool.calls == 2
        assert second_tool.calls == 1
        events = store.read_events(suspended.session_path)
        batch_events = [event for event in events if event.type.startswith("tool.batch_")]
        assert [event.type for event in batch_events] == [
            "tool.batch_started",
            "tool.batch_suspended",
            "tool.batch_resumed",
            "tool.batch_completed",
        ]
        assert len({event.payload["batch_id"] for event in batch_events}) == 1
        completed = batch_events[-1].payload
        assert completed["recovery_summary"]["counts"] == {"completed": 2}
        assert completed["recovery_summary"]["attempt_activity"] == {
            "executed": 1,
            "reused": 1,
            "blocked": 0,
        }
        invocation_reuse = next(event for event in events if event.type == "tool.invocation_reused")
        assert invocation_reuse.payload["batch_id"] == completed["batch_id"]
        assert invocation_reuse.payload["batch_index"] == 2
        stats = store.build_task_graph(events)["stats"]
        assert stats["tool_batch_resumed_count"] == 1
        assert stats["tool_batch_suspended_count"] == 1
        assert stats["tool_batch_completed_count"] == 1


def test_completed_batch_is_replayed_from_durable_results_after_model_timeout() -> None:
    with _local_tmp() as tmp_path:
        store = SessionStore(tmp_path / "sessions")
        first_tool = _ReadOnlyCountingTool("batch_read_one")
        second_tool = _ReadOnlyCountingTool("batch_read_two")
        registry = _registry(first_tool, second_tool)
        batch = _batch_response()
        first_core = AgentCore.from_config(
            AgentCoreConfig(
                model_client=_RecoveringModel([batch, TimeoutError("model failed after batch")]),
                tool_registry=registry,
                session_store=store,
                tool_execution_mode="parallel",
            )
        )

        failed = first_core.execute_turn(TurnRequest(prompt="run then time out", resume=False))

        assert failed.state is TurnState.TIMED_OUT
        assert failed.checkpoint is not None
        assert first_tool.calls == second_tool.calls == 1

        recovered_core = AgentCore.from_config(
            AgentCoreConfig(
                model_client=_RecoveringModel([batch, AgentResponse(text="durable replay")]),
                tool_registry=registry,
                session_store=store,
                tool_execution_mode="parallel",
            )
        )
        recovered = recovered_core.recover_turn(
            RecoverTurnRequest(
                session_path=failed.session_path,
                checkpoint_id=str(failed.checkpoint["id"]),
            )
        )

        assert recovered.succeeded is True
        assert first_tool.calls == second_tool.calls == 1
        events = store.read_events(failed.session_path)
        assert sum(event.type == "tool.batch_replayed" for event in events) == 1
        replay_completion = [event for event in events if event.type == "tool.batch_completed"][-1]
        assert replay_completion.payload["recovery_summary"]["attempt_activity"]["reused"] == 2


def test_recovery_blocks_when_same_batch_position_changes_fingerprint() -> None:
    with _local_tmp() as tmp_path:
        store = SessionStore(tmp_path / "sessions")
        first_tool = _ReadOnlyCountingTool("batch_read_one")
        second_tool = _ReadOnlyCountingTool("batch_read_two")
        registry = _registry(first_tool, second_tool)
        first_batch = _batch_response("A")
        first_core = AgentCore.from_config(
            AgentCoreConfig(
                model_client=_RecoveringModel([first_batch, TimeoutError("model failed")]),
                tool_registry=registry,
                session_store=store,
                tool_execution_mode="parallel",
            )
        )
        failed = first_core.execute_turn(TurnRequest(prompt="stable batch", resume=False))
        assert failed.state is TurnState.TIMED_OUT
        assert failed.checkpoint is not None

        changed_core = AgentCore.from_config(
            AgentCoreConfig(
                model_client=_RecoveringModel([_batch_response("CHANGED")]),
                tool_registry=registry,
                session_store=store,
                tool_execution_mode="parallel",
            )
        )
        blocked = changed_core.recover_turn(
            RecoverTurnRequest(
                session_path=failed.session_path,
                checkpoint_id=str(failed.checkpoint["id"]),
            )
        )

        assert blocked.state is TurnState.SUSPENDED
        assert blocked.failure is not None
        assert blocked.failure.kind is TurnFailureKind.INDETERMINATE_TOOL
        assert first_tool.calls == second_tool.calls == 1
        conflict = next(
            event
            for event in store.read_events(failed.session_path)
            if event.type == "tool.batch_conflict"
        )
        assert conflict.payload["expected_fingerprint"] != conflict.payload["actual_fingerprint"]
