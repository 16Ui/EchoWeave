from __future__ import annotations

from echoweave_runtime.observability import build_trace_timeline
from echoweave_runtime.session.schema import StoredEvent


def test_trace_timeline_groups_attempts_and_projects_reliability_signals() -> None:
    events = [
        StoredEvent(
            "turn.state_changed",
            {
                "turn_id": "turn-1",
                "trace_id": "trace-1",
                "state": "created",
                "attempt": 1,
            },
            timestamp="2026-01-01T00:00:00+00:00",
            session_id="session-1",
        ),
        StoredEvent(
            "provider.retry_scheduled",
            {
                "turn_id": "turn-1",
                "trace_id": "trace-1",
                "provider_key": "demo",
                "attempt": 1,
                "next_attempt": 2,
                "delay_seconds": 0.25,
            },
            timestamp="2026-01-01T00:00:01+00:00",
            session_id="session-1",
        ),
        StoredEvent(
            "tool_execution_end",
            {
                "turn_id": "turn-1",
                "name": "read",
                "fencing_token": 7,
                "api_key": "must-not-leak",
                "reason": "Authorization: Bearer inline-secret-value",
            },
            timestamp="2026-01-01T00:00:01.500000+00:00",
            session_id="session-1",
        ),
        StoredEvent(
            "turn.state_changed",
            {
                "turn_id": "turn-1",
                "trace_id": "trace-1",
                "from": "running",
                "state": "completed",
                "attempt": 1,
            },
            timestamp="2026-01-01T00:00:02+00:00",
            session_id="session-1",
        ),
    ]

    projection = build_trace_timeline(events, session_id="session-1")

    assert projection["trace_count"] == 1
    assert projection["status_counts"] == {"completed": 1}
    trace = projection["traces"][0]
    assert trace["turn_id"] == "turn-1"
    assert trace["status"] == "completed"
    assert trace["duration_ms"] == 2000.0
    assert trace["signal_count"] == 1
    assert trace["categories"]["provider"] == 1
    tool_event = next(item for item in trace["events"] if item["type"] == "tool_execution_end")
    assert tool_event["payload"]["api_key"] == "[redacted]"
    assert tool_event["payload"]["fencing_token"] == 7
    assert "inline-secret-value" not in tool_event["detail"]
    assert "inline-secret-value" not in tool_event["payload"]["reason"]


def test_trace_timeline_is_bounded_and_ignores_unscoped_session_events() -> None:
    events = [
        StoredEvent("session", {"id": "session-1"}),
        StoredEvent(
            "turn.state_changed",
            {"turn_id": "turn-1", "trace_id": "trace-1", "state": "running"},
        ),
        StoredEvent("provider.retry_scheduled", {"turn_id": "turn-1"}),
        StoredEvent("provider.retry_exhausted", {"turn_id": "turn-1"}),
    ]

    projection = build_trace_timeline(events, event_limit_per_trace=2)

    assert projection["scoped_event_count"] == 3
    trace = projection["traces"][0]
    assert trace["event_count"] == 3
    assert [item["type"] for item in trace["events"]] == [
        "provider.retry_scheduled",
        "provider.retry_exhausted",
    ]
    assert trace["status"] == "warning"


def test_trace_timeline_tolerates_mixed_timezone_timestamp_formats() -> None:
    events = [
        StoredEvent(
            "turn.state_changed",
            {"turn_id": "turn-1", "trace_id": "trace-1", "state": "running"},
            timestamp="2026-01-01T00:00:00",
        ),
        StoredEvent(
            "turn.state_changed",
            {"turn_id": "turn-1", "trace_id": "trace-1", "state": "completed"},
            timestamp="2026-01-01T00:00:01+00:00",
        ),
    ]

    projection = build_trace_timeline(events)

    assert projection["traces"][0]["status"] == "completed"
    assert projection["traces"][0]["duration_ms"] is None
