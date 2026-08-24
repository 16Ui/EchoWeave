from __future__ import annotations

from typing import Any

from echoweave_runtime.events import AgentEvent, utc_now_iso


RuntimeStreamEvent = AgentEvent


def build_runtime_event(event: str, session_id: str, payload: dict[str, Any]) -> RuntimeStreamEvent:
    return AgentEvent(
        type=event,
        source="agent-runtime",
        conversation_id=session_id,
        payload=payload,
    )


__all__ = ["RuntimeStreamEvent", "build_runtime_event", "utc_now_iso"]
