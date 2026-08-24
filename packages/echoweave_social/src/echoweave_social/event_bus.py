from __future__ import annotations

import json
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from echoweave_runtime.events import AgentEvent


@dataclass(frozen=True)
class EventEnvelope:
    cursor: int
    value: AgentEvent

    @property
    def id(self) -> int:
        """Monotonic buffer cursor used only for SSE resume."""
        return self.cursor

    @property
    def event(self) -> str:
        return self.value.type

    @property
    def data(self) -> dict[str, Any]:
        return {
            **self.value.payload,
            "event_id": self.value.event_id,
            "timestamp": self.value.timestamp,
            "ts": datetime.fromisoformat(self.value.timestamp).timestamp(),
            "source": self.value.source,
            "conversation_id": self.value.conversation_id,
            "correlation_id": self.value.correlation_id,
            "sequence": self.value.sequence,
            "schema_version": self.value.schema_version,
        }

    def to_sse(self) -> bytes:
        payload = json.dumps(self.data, ensure_ascii=False, default=str)
        text = f"id: {self.cursor}\nevent: {self.value.type}\ndata: {payload}\n\n"
        return text.encode("utf-8")


class EventBus:
    def __init__(self, maxlen: int = 500) -> None:
        self._events: deque[EventEnvelope] = deque(maxlen=maxlen)
        self._condition = threading.Condition()
        self._next_id = 1

    def publish(
        self,
        event: str | AgentEvent,
        data: dict[str, Any] | None = None,
        *,
        source: str = "event-bus",
        conversation_id: str | None = None,
        correlation_id: str | None = None,
    ) -> EventEnvelope:
        with self._condition:
            value = event if isinstance(event, AgentEvent) else AgentEvent(
                type=event,
                source=source,
                payload=data or {},
                conversation_id=conversation_id,
                correlation_id=correlation_id,
            )
            envelope = EventEnvelope(
                cursor=self._next_id,
                value=value,
            )
            self._next_id += 1
            self._events.append(envelope)
            self._condition.notify_all()
            return envelope

    def snapshot_after(self, last_event_id: int | None) -> list[EventEnvelope]:
        with self._condition:
            return self._events_after(last_event_id)

    def wait_after(self, last_event_id: int | None, timeout: float = 15.0) -> list[EventEnvelope]:
        with self._condition:
            events = self._events_after(last_event_id)
            if events:
                return events
            self._condition.wait(timeout=timeout)
            return self._events_after(last_event_id)

    def _events_after(self, last_event_id: int | None) -> list[EventEnvelope]:
        if last_event_id is None:
            return list(self._events)
        return [event for event in self._events if event.id > last_event_id]
