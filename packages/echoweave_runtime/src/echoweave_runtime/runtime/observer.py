from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from echoweave_runtime.runtime.events import RuntimeStreamEvent, utc_now_iso


class RuntimeObserver(Protocol):
    def emit(self, event: RuntimeStreamEvent) -> None:
        ...


class NullRuntimeObserver:
    def emit(self, event: RuntimeStreamEvent) -> None:
        return


class JsonLineRuntimeObserver:
    def __init__(self, sink) -> None:
        self.sink = sink

    def emit(self, event: RuntimeStreamEvent) -> None:
        self.sink(event.to_json())


@dataclass
class RuntimeEventDispatcher:
    observer: RuntimeObserver

    def emit(self, event: str, session_id: str, payload: dict[str, object]) -> None:
        self.observer.emit(
            RuntimeStreamEvent(
                event=event,
                timestamp=utc_now_iso(),
                session_id=session_id,
                payload=payload,
            )
        )


def summarize_runtime_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    step_count = sum(1 for item in events if item.get("event") == "tool_execution_end")
    retry_count = 0
    blocked_count = 0
    success = True
    error_types: dict[str, int] = {}

    for item in events:
        event_name = str(item.get("event", ""))
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        if event_name == "tool_execution_end":
            result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
            if result.get("status") == "error":
                success = False
                error = str(result.get("error") or result.get("content") or "tool_error")
                error_type = error.split(":", 1)[0].strip() or "tool_error"
                error_types[error_type] = error_types.get(error_type, 0) + 1
        elif event_name == "policy.decision":
            policy = payload.get("policy") if isinstance(payload.get("policy"), dict) else {}
            decision = str(policy.get("decision") or policy.get("status") or "allow")
            if decision == "deny":
                blocked_count += 1
                success = False
                error_types["policy_deny"] = error_types.get("policy_deny", 0) + 1
        elif event_name == "turn_end":
            turn = payload.get("turn") if isinstance(payload.get("turn"), dict) else {}
            reply = str(turn.get("reply", ""))
            if "retry" in reply.lower():
                retry_count += 1

    sorted_error_types = sorted(error_types.items(), key=lambda pair: (-pair[1], pair[0]))
    return {
        "success": success,
        "step_count": step_count,
        "retry_count": retry_count,
        "policy_block_count": blocked_count,
        "error_types": [{"type": name, "count": count} for name, count in sorted_error_types],
    }
