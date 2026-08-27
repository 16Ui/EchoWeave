from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Iterable

from echoweave_runtime.session.schema import StoredEvent


_TERMINAL_STATES = frozenset({"completed", "failed", "timed_out", "cancelled", "suspended"})
_FAILURE_STATES = frozenset({"failed", "timed_out", "cancelled"})
_SENSITIVE_MARKERS = ("api_key", "authorization", "credential", "password", "secret")
_SENSITIVE_TOKEN_KEYS = frozenset(
    {"access_token", "bearer_token", "refresh_token", "token", "webhook_token"}
)
_INLINE_SECRET_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|authorization|password|secret|access[_-]?token|refresh[_-]?token|webhook[_-]?token)\b"
    r"(\s*(?:=|:)\s*|\s+)([^\s,;]+)"
)
_BEARER_SECRET_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_SIGNAL_EVENTS = frozenset(
    {
        "provider.retry_scheduled",
        "provider.retry_exhausted",
        "provider.circuit_opened",
        "provider.circuit_rejected",
        "provider.stream_interrupted",
        "tool.invocation_blocked",
        "tool.batch_suspended",
        "tool.batch_conflict",
        "turn.lease_lost",
        "turn.lease_rejected",
        "turn.lease_taken_over",
        "turn.recovery_started",
    }
)


def build_trace_timeline(
    events: Iterable[StoredEvent],
    *,
    session_id: str | None = None,
    event_limit_per_trace: int = 120,
) -> dict[str, Any]:
    """Project append-only runtime events into bounded, UI-safe trace timelines."""

    if event_limit_per_trace <= 0:
        raise ValueError("event_limit_per_trace must be positive")

    groups: dict[str, dict[str, Any]] = {}
    latest_trace_by_turn: dict[str, str] = {}
    scoped_event_count = 0

    for index, event in enumerate(events):
        payload = event.payload if isinstance(event.payload, dict) else {}
        turn_id = _optional_text(payload.get("turn_id"))
        trace_id = _trace_id(payload)
        if trace_id is not None and turn_id is not None:
            latest_trace_by_turn[turn_id] = trace_id
        elif trace_id is None and turn_id is not None:
            trace_id = latest_trace_by_turn.get(turn_id)
        if trace_id is None:
            continue

        scoped_event_count += 1
        trace = groups.setdefault(
            trace_id,
            {
                "trace_id": trace_id,
                "session_id": session_id or event.session_id,
                "turn_id": turn_id,
                "attempt": _positive_int(payload.get("attempt")),
                "status": "running",
                "started_at": event.timestamp,
                "finished_at": None,
                "duration_ms": None,
                "event_count": 0,
                "signal_count": 0,
                "categories": {},
                "events": [],
                "_terminal_state": None,
            },
        )
        if trace["session_id"] is None and event.session_id:
            trace["session_id"] = event.session_id
        if turn_id is not None:
            trace["turn_id"] = turn_id
        attempt = _positive_int(payload.get("attempt"))
        if attempt is not None:
            trace["attempt"] = attempt
        if trace["started_at"] is None and event.timestamp is not None:
            trace["started_at"] = event.timestamp

        category = _category(event.type)
        trace["event_count"] += 1
        trace["categories"][category] = trace["categories"].get(category, 0) + 1
        if _is_signal_event(event.type, payload):
            trace["signal_count"] += 1
        trace["events"].append(_project_event(index, event, category))

        if event.type == "turn.state_changed":
            state = _optional_text(payload.get("state"))
            if state:
                trace["status"] = state
                if state in _TERMINAL_STATES:
                    trace["_terminal_state"] = state
                    trace["finished_at"] = event.timestamp
        elif event.type == "eval.case_finished":
            passed = bool(payload.get("passed", payload.get("success", False)))
            trace["status"] = "completed" if passed else "failed"
            trace["_terminal_state"] = trace["status"]
            trace["finished_at"] = event.timestamp

    traces: list[dict[str, Any]] = []
    for trace in groups.values():
        terminal_state = trace.pop("_terminal_state")
        if terminal_state is None and trace["signal_count"] > 0:
            trace["status"] = "warning"
        trace["duration_ms"] = _duration_ms(trace["started_at"], trace["finished_at"])
        trace["events"] = trace["events"][-event_limit_per_trace:]
        traces.append(trace)

    traces.sort(key=lambda item: (item.get("started_at") or "", item["trace_id"]), reverse=True)
    status_counts: dict[str, int] = {}
    for trace in traces:
        status = str(trace["status"])
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "session_id": session_id,
        "trace_count": len(traces),
        "scoped_event_count": scoped_event_count,
        "status_counts": status_counts,
        "traces": traces,
    }


def _project_event(index: int, event: StoredEvent, category: str) -> dict[str, Any]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    return {
        "index": index,
        "type": event.type,
        "category": category,
        "status": _event_status(event.type, payload),
        "timestamp": event.timestamp,
        "title": _event_title(event.type, payload),
        "detail": _event_detail(event.type, payload),
        "payload": _safe_value(payload),
    }


def _trace_id(payload: dict[str, Any]) -> str | None:
    direct = _optional_text(payload.get("trace_id"))
    if direct:
        return direct
    trace = payload.get("trace")
    if isinstance(trace, dict):
        return _optional_text(trace.get("id") or trace.get("trace_id"))
    return None


def _category(event_type: str) -> str:
    if event_type.startswith("turn.") or event_type in {"turn_start", "turn_end"}:
        return "turn"
    if event_type.startswith("provider.") or event_type.startswith("model"):
        return "provider"
    if event_type.startswith("tool"):
        return "tool"
    if event_type.startswith("retrieval") or event_type.startswith("rag"):
        return "retrieval"
    if event_type.startswith("checkpoint") or event_type == "history_reset":
        return "checkpoint"
    if event_type.startswith("policy") or event_type.startswith("approval"):
        return "policy"
    if event_type.startswith("eval."):
        return "eval"
    if event_type == "message" or event_type.startswith("stream"):
        return "message"
    return "runtime"


def _event_status(event_type: str, payload: dict[str, Any]) -> str:
    if event_type == "turn.state_changed":
        state = str(payload.get("state") or "running")
        if state in _FAILURE_STATES:
            return "error"
        if state == "suspended":
            return "blocked"
        if state == "completed":
            return "ok"
        return "running"
    if event_type.startswith("policy"):
        decision = str(payload.get("decision") or payload.get("status") or "").lower()
        if decision in {"deny", "denied", "blocked"}:
            return "blocked"
        if decision in {"require_approval", "approval"}:
            return "warning"
    if any(marker in event_type for marker in ("error", "failed", "exhausted", "interrupted", "lost")):
        return "error"
    if any(marker in event_type for marker in ("blocked", "rejected", "suspended", "conflict")):
        return "blocked"
    if any(marker in event_type for marker in ("retry", "taken_over", "recovery_started", "circuit_opened")):
        return "warning"
    return "ok"


def _is_signal_event(event_type: str, payload: dict[str, Any]) -> bool:
    if event_type in _SIGNAL_EVENTS:
        return True
    if event_type.startswith("policy"):
        decision = str(payload.get("decision") or payload.get("status") or "").lower()
        return decision in {"approval", "blocked", "denied", "deny", "require_approval"}
    return False


def _event_title(event_type: str, payload: dict[str, Any]) -> str:
    if event_type == "turn.state_changed":
        return f"Turn → {payload.get('state') or 'unknown'}"
    if event_type.startswith("tool"):
        name = payload.get("name") or payload.get("tool_name") or payload.get("tool")
        return f"{event_type}: {name}" if name else event_type
    if event_type.startswith("provider"):
        provider = payload.get("provider_key") or payload.get("provider")
        return f"{event_type}: {provider}" if provider else event_type
    if event_type.startswith("eval."):
        case_id = payload.get("id") or payload.get("case_id")
        return f"{event_type}: {case_id}" if case_id else event_type
    return event_type


def _event_detail(event_type: str, payload: dict[str, Any]) -> str:
    parts: list[str] = []
    if event_type == "turn.state_changed":
        source = payload.get("from")
        target = payload.get("state")
        parts.append(f"{source or '∅'} → {target or '?'}")
    for key in (
        "attempt",
        "next_attempt",
        "delay_seconds",
        "fencing_token",
        "previous_fencing_token",
        "owner_id",
        "previous_owner_id",
        "reason_code",
        "reason",
        "error_type",
    ):
        value = payload.get(key)
        if value is not None and value != "":
            parts.append(f"{key}={_short_text(value, 120)}")
    return " · ".join(parts)


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 4:
        return "[truncated]"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 24:
                result["…"] = "[truncated]"
                break
            text_key = str(key)
            if _sensitive_key(text_key):
                result[text_key] = "[redacted]"
            else:
                result[text_key] = _safe_value(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        items = [_safe_value(item, depth=depth + 1) for item in value[:12]]
        if len(value) > 12:
            items.append("[truncated]")
        return items
    if isinstance(value, str):
        return _short_text(value, 320)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _short_text(value, 160)


def _duration_ms(started_at: str | None, finished_at: str | None) -> float | None:
    if not started_at or not finished_at:
        return None
    try:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        finish = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    try:
        return max(0.0, (finish - start).total_seconds() * 1000)
    except TypeError:
        return None


def _sensitive_key(key: str) -> bool:
    normalized = key.strip().lower()
    return normalized in _SENSITIVE_TOKEN_KEYS or any(
        marker in normalized for marker in _SENSITIVE_MARKERS
    )


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _short_text(value: Any, limit: int) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ")
    text = _BEARER_SECRET_PATTERN.sub("Bearer [redacted]", text)
    text = _INLINE_SECRET_PATTERN.sub(r"\1\2[redacted]", text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


__all__ = ["build_trace_timeline"]
