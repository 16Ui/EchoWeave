from __future__ import annotations

import shutil
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from echoweave_runtime.app import build_runtime
from echoweave_runtime.models.demo import SequenceModelClient
from echoweave_runtime.session.store import SessionStore
from echoweave_runtime.tool_invocations import (
    InvocationResolution,
    ToolEffect,
    ToolInvocationLedger,
    resolve_tool_effect,
)
from echoweave_runtime.tools.write import WriteTool
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


class CountingTool:
    name = "counting"
    description = "Count executions"
    input_schema = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
        "additionalProperties": False,
    }

    def __init__(self, effect: str = "non_idempotent") -> None:
        self.effect = effect
        self.calls = 0

    def execute(self, arguments: dict[str, str]) -> str:
        self.calls += 1
        return f"call={self.calls};value={arguments['value']}"


def test_completed_invocation_is_reused_without_reexecuting_tool() -> None:
    with _local_tmp() as tmp_path:
        store = SessionStore(tmp_path / "sessions")
        tool = CountingTool()
        registry = ToolRegistry()
        registry.register(tool)
        runtime = build_runtime(
            SequenceModelClient(
                [
                    AgentResponse(tool_calls=[ToolCall(id="same-call", name="counting", input={"value": "A"})]),
                    AgentResponse(tool_calls=[ToolCall(id="same-call", name="counting", input={"value": "A"})]),
                    AgentResponse(text="done"),
                ]
            ),
            registry,
            store,
        )
        session_path = store.create()

        reply, _, _ = runtime.run_turn(
            session_path,
            [],
            "run twice",
            turn_id="stable-turn",
            trace_id="trace-1",
        )

        events = store.read_events(session_path)
        assert reply == "done"
        assert tool.calls == 1
        assert sum(event.type == "tool.invocation_started" for event in events) == 1
        assert sum(event.type == "tool.invocation_completed" for event in events) == 1
        assert sum(event.type == "tool.invocation_reused" for event in events) == 1
        stats = store.build_task_graph(events)["stats"]
        assert stats["tool_invocation_count"] == 1
        assert stats["tool_invocation_reuse_count"] == 1


def test_same_call_identity_with_different_input_is_blocked() -> None:
    with _local_tmp() as tmp_path:
        store = SessionStore(tmp_path / "sessions")
        tool = CountingTool()
        registry = ToolRegistry()
        registry.register(tool)
        runtime = build_runtime(
            SequenceModelClient(
                [
                    AgentResponse(tool_calls=[ToolCall(id="same-call", name="counting", input={"value": "A"})]),
                    AgentResponse(tool_calls=[ToolCall(id="same-call", name="counting", input={"value": "B"})]),
                    AgentResponse(text="handled conflict"),
                ]
            ),
            registry,
            store,
        )
        session_path = store.create()

        reply, _, _ = runtime.run_turn(
            session_path,
            [],
            "conflicting call",
            turn_id="stable-turn",
        )

        blocked = [event for event in store.read_events(session_path) if event.type == "tool.invocation_blocked"]
        assert reply == "handled conflict"
        assert tool.calls == 1
        assert blocked[-1].payload["reason"] == "tool call identity was reused with different name or input"


def test_interrupted_unknown_tool_is_blocked_but_read_only_tool_can_retry() -> None:
    with _local_tmp() as tmp_path:
        store = SessionStore(tmp_path / "sessions")
        session_path = store.create()
        first_runtime_ledger = ToolInvocationLedger(store)
        interrupted = first_runtime_ledger.prepare(
            session_path,
            turn_id="recoverable-turn",
            trace_id="trace-before-crash",
            tool_call_id="call-1",
            tool_name="external-tool",
            tool_input={"query": "A"},
            effect=ToolEffect.UNKNOWN,
        )

        recovered_ledger = ToolInvocationLedger(store)
        blocked = recovered_ledger.prepare(
            session_path,
            turn_id="recoverable-turn",
            trace_id="trace-after-crash",
            tool_call_id="call-1",
            tool_name="external-tool",
            tool_input={"query": "A"},
            effect=ToolEffect.UNKNOWN,
        )
        safe_retry = recovered_ledger.prepare(
            session_path,
            turn_id="recoverable-turn",
            trace_id="trace-after-crash",
            tool_call_id="call-2",
            tool_name="read-only-tool",
            tool_input={"query": "B"},
            effect=ToolEffect.READ_ONLY,
        )
        second_runtime_ledger = ToolInvocationLedger(store)
        retried = second_runtime_ledger.prepare(
            session_path,
            turn_id="recoverable-turn",
            trace_id="trace-retry",
            tool_call_id="call-2",
            tool_name="read-only-tool",
            tool_input={"query": "B"},
            effect=ToolEffect.READ_ONLY,
        )

        assert interrupted.action == "execute"
        assert blocked.action == "indeterminate"
        assert safe_retry.action == "execute"
        assert retried.action == "execute"
        assert retried.attempt == 2


def test_tool_effect_can_be_refined_by_arguments() -> None:
    with _local_tmp() as tmp_path:
        tool = WriteTool(tmp_path)

        assert resolve_tool_effect("write", tool, {"path": "a", "content": "A"}) is ToolEffect.IDEMPOTENT_WRITE
        assert (
            resolve_tool_effect(
                "write",
                tool,
                {"path": "a", "content": "A", "overwrite": False},
            )
            is ToolEffect.NON_IDEMPOTENT
        )


def test_manual_completion_is_reused_as_a_durable_result() -> None:
    with _local_tmp() as tmp_path:
        store = SessionStore(tmp_path / "sessions")
        session_path = store.create()
        crashed = ToolInvocationLedger(store)
        started = crashed.prepare(
            session_path,
            turn_id="turn-manual",
            trace_id="trace-1",
            tool_call_id="call-1",
            tool_name="external-write",
            tool_input={"value": "A"},
            effect=ToolEffect.NON_IDEMPOTENT,
        )
        operator = ToolInvocationLedger(store)
        blocked = operator.prepare(
            session_path,
            turn_id="turn-manual",
            trace_id="trace-2",
            tool_call_id="call-1",
            tool_name="external-write",
            tool_input={"value": "A"},
            effect=ToolEffect.NON_IDEMPOTENT,
        )

        resolution = operator.resolve(
            session_path,
            invocation_key=started.invocation_key,
            resolution=InvocationResolution.CONFIRM_COMPLETED,
            outcome={"status": "ok", "content": "confirmed external result"},
            actor="tester",
        )
        replay = ToolInvocationLedger(store).prepare(
            session_path,
            turn_id="turn-manual",
            trace_id="trace-3",
            tool_call_id="call-1",
            tool_name="external-write",
            tool_input={"value": "A"},
            effect=ToolEffect.NON_IDEMPOTENT,
        )

        assert blocked.action == "indeterminate"
        assert resolution["resolution"] == "confirm_completed"
        assert replay.action == "reuse"
        assert replay.outcome == {"status": "ok", "content": "confirmed external result"}


def test_manual_retry_authorization_is_consumed_after_one_attempt() -> None:
    with _local_tmp() as tmp_path:
        store = SessionStore(tmp_path / "sessions")
        session_path = store.create()
        first = ToolInvocationLedger(store).prepare(
            session_path,
            turn_id="turn-retry",
            trace_id="trace-1",
            tool_call_id="call-1",
            tool_name="external-write",
            tool_input={"value": "A"},
            effect=ToolEffect.NON_IDEMPOTENT,
        )
        operator = ToolInvocationLedger(store)
        assert operator.prepare(
            session_path,
            turn_id="turn-retry",
            trace_id="trace-2",
            tool_call_id="call-1",
            tool_name="external-write",
            tool_input={"value": "A"},
            effect=ToolEffect.NON_IDEMPOTENT,
        ).action == "indeterminate"
        resolution = operator.resolve(
            session_path,
            invocation_key=first.invocation_key,
            resolution=InvocationResolution.ALLOW_RETRY,
            actor="tester",
        )

        authorized = ToolInvocationLedger(store).prepare(
            session_path,
            turn_id="turn-retry",
            trace_id="trace-3",
            tool_call_id="call-1",
            tool_name="external-write",
            tool_input={"value": "A"},
            effect=ToolEffect.NON_IDEMPOTENT,
        )
        blocked_again = ToolInvocationLedger(store).prepare(
            session_path,
            turn_id="turn-retry",
            trace_id="trace-4",
            tool_call_id="call-1",
            tool_name="external-write",
            tool_input={"value": "A"},
            effect=ToolEffect.NON_IDEMPOTENT,
        )

        assert resolution["authorized_attempt"] == 2
        assert authorized.action == "execute"
        assert authorized.attempt == 2
        assert blocked_again.action == "indeterminate"
