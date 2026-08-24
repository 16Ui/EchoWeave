from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4


@dataclass(frozen=True)
class AuditEvent:
    category: str
    action: str
    status: str = "ok"
    subject: str | None = None
    trace_id: str | None = None
    conversation_id: str | None = None
    session_id: str | None = None
    actor_id: str | None = None
    workspace: str | None = None
    latency_ms: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: uuid4().hex)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


class AuditSink(Protocol):
    def record(self, event: AuditEvent) -> None:
        ...


class NullAuditSink:
    def record(self, event: AuditEvent) -> None:
        return


class JsonlAuditSink:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        self._lock = threading.Lock()

    def record(self, event: AuditEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(encoded + "\n")


_audit_sink: AuditSink = NullAuditSink()


def configure_audit(path: str | Path | None) -> AuditSink:
    global _audit_sink
    if path is None or str(path).strip() == "":
        _audit_sink = NullAuditSink()
    else:
        _audit_sink = JsonlAuditSink(Path(path))
    return _audit_sink


def get_audit_sink() -> AuditSink:
    return _audit_sink


def record_audit(
    category: str,
    action: str,
    *,
    status: str = "ok",
    subject: str | None = None,
    trace_id: str | None = None,
    conversation_id: str | None = None,
    session_id: str | None = None,
    actor_id: str | None = None,
    workspace: str | Path | None = None,
    latency_ms: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    event = AuditEvent(
        category=category,
        action=action,
        status=status,
        subject=subject,
        trace_id=trace_id,
        conversation_id=conversation_id,
        session_id=session_id,
        actor_id=actor_id,
        workspace=str(workspace) if workspace is not None else None,
        latency_ms=latency_ms,
        metadata=metadata or {},
    )
    get_audit_sink().record(event)


def read_audit_events(path: str | Path) -> list[AuditEvent]:
    audit_path = Path(path)
    if not audit_path.exists():
        return []
    events: list[AuditEvent] = []
    for line in audit_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        data = json.loads(line)
        if isinstance(data, dict):
            events.append(
                AuditEvent(
                    category=str(data.get("category") or ""),
                    action=str(data.get("action") or ""),
                    status=str(data.get("status") or "ok"),
                    subject=_optional_str(data.get("subject")),
                    trace_id=_optional_str(data.get("trace_id")),
                    conversation_id=_optional_str(data.get("conversation_id")),
                    session_id=_optional_str(data.get("session_id")),
                    actor_id=_optional_str(data.get("actor_id")),
                    workspace=_optional_str(data.get("workspace")),
                    latency_ms=float(data["latency_ms"]) if data.get("latency_ms") is not None else None,
                    metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else {},
                    event_id=str(data.get("event_id") or uuid4().hex),
                    ts=float(data.get("ts") or 0.0),
                )
            )
    return events


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
