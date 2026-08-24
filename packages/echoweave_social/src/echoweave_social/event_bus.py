from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EventEnvelope:
    id: int
    event: str
    data: dict[str, Any]

    def to_sse(self) -> bytes:
        payload = json.dumps(self.data, ensure_ascii=False, default=str)
        text = f"id: {self.id}\nevent: {self.event}\ndata: {payload}\n\n"
        return text.encode("utf-8")


class EventBus:
    def __init__(self, maxlen: int = 500) -> None:
        self._events: deque[EventEnvelope] = deque(maxlen=maxlen)
        self._condition = threading.Condition()
        self._next_id = 1

    def publish(self, event: str, data: dict[str, Any]) -> EventEnvelope:
        with self._condition:
            envelope = EventEnvelope(
                id=self._next_id,
                event=event,
                data={**data, "ts": time.time()},
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
