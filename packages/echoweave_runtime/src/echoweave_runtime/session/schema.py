from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


SURROGATE_START = 0xD800
SURROGATE_END = 0xDFFF


def sanitize_value(value: Any) -> Any:
    if isinstance(value, str):
        return "".join(
            "\uFFFD" if SURROGATE_START <= ord(char) <= SURROGATE_END else char for char in value
        )
    if isinstance(value, list):
        return [sanitize_value(item) for item in value]
    if isinstance(value, dict):
        return {key: sanitize_value(item) for key, item in value.items()}
    return value


@dataclass
class StoredEvent:
    type: str
    payload: dict[str, Any]
    timestamp: str | None = None
    session_id: str | None = None

    def to_json(self) -> str:
        data = asdict(self)
        event_type = str(data.pop("type"))
        payload = data.pop("payload")
        timestamp = data.pop("timestamp") or utc_now_iso()
        session_id = data.pop("session_id")

        encoded: dict[str, Any] = {
            "type": event_type,
            "event": event_type,
            "payload": sanitize_value(payload),
            "timestamp": sanitize_value(timestamp),
        }
        if isinstance(session_id, str) and session_id:
            encoded["session_id"] = sanitize_value(session_id)
        return json.dumps(encoded, ensure_ascii=False)

    @classmethod
    def from_json(cls, line: str) -> "StoredEvent":
        data = json.loads(line)
        event_type = data.get("type") or data.get("event")
        if not isinstance(event_type, str) or not event_type:
            raise ValueError("event type missing")
        payload = data.get("payload")
        timestamp = data.get("timestamp")
        session_id = data.get("session_id")
        return cls(
            type=event_type,
            payload=payload if isinstance(payload, dict) else {},
            timestamp=timestamp if isinstance(timestamp, str) and timestamp else None,
            session_id=session_id if isinstance(session_id, str) and session_id else None,
        )


@dataclass
class SessionHeader:
    id: str
    parent_id: str | None = None
    branch_label: str | None = None


@dataclass
class SessionSnapshot:
    header: SessionHeader
    history: list[dict[str, Any]]
    summary: str | None = None
    compaction: dict[str, Any] | None = None
    state: dict[str, Any] | None = None
