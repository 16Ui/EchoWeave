from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any
import json


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RuntimeStreamEvent:
    event: str
    timestamp: str
    session_id: str
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


def build_runtime_event(event: str, session_id: str, payload: dict[str, Any]) -> RuntimeStreamEvent:
    return RuntimeStreamEvent(
        event=event,
        timestamp=utc_now_iso(),
        session_id=session_id,
        payload=payload,
    )
