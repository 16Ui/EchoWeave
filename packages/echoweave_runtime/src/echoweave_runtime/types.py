from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]
ToolExecutionMode = Literal["sequential", "parallel", "streaming"]
EventType = Literal[
    "session",
    "turn_start",
    "turn_end",
    "message_start",
    "message_update",
    "message_end",
    "message",
    "history_reset",
    "turn.recovery_started",
    "turn.recovery_finished",
    "tool_execution_start",
    "tool_execution_update",
    "tool_execution_end",
    "tool_call",
    "tool_result",
    "tool_error",
    "tool.invocation_started",
    "tool.invocation_completed",
    "tool.invocation_reused",
    "tool.invocation_blocked",
    "streaming.tool_ready",
    "retrieval_start",
    "retrieval_end",
    "retrieval",
    "retrieval_error",
    "memory_start",
    "memory_end",
    "memory",
    "memory_error",
    "memory_write",
    "memory_write_error",
    "extension_hook",
    "extension_error",
    "summary",
    "branch",
    "compaction_start",
    "compaction_end",
    "compaction",
    "policy.decision",
    "checkpoint.created",
    "checkpoint.replay_started",
    "checkpoint.replay_finished",
    "eval.case_started",
    "eval.case_finished",
    "eval.run_finished",
]


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class AgentResponse:
    text: str = ""
    tool_calls: list[ToolCall] | None = None
    content: list[dict[str, Any]] | None = None


@dataclass
class SessionEvent:
    type: EventType
    payload: dict[str, Any]
