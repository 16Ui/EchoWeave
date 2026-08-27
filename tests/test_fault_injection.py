from __future__ import annotations

from pathlib import Path

from echoweave_harness.fault_injection import (
    FaultInjectionEvalRunner,
    load_latest_fault_eval,
)
from echoweave_runtime.observability import build_trace_timeline
from echoweave_runtime.session.store import SessionStore


def test_fault_injection_eval_runs_real_reliability_probes_and_writes_evidence(
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "demo-artifacts"
    report = FaultInjectionEvalRunner(
        tmp_path / "workspace",
        output_root=output_root,
    ).run()

    assert report.passed is True
    assert report.scenario_count == 4
    assert report.passed_count == 4
    assert report.pass_rate == 1.0
    assert report.overall_score == 1.0
    assert Path(report.report_path).exists()
    assert load_latest_fault_eval(output_root) == report.to_dict()

    results = {item.id: item for item in report.scenarios}
    assert results["provider-transient-retry"].observed["attempts"] == 2
    assert results["provider-circuit-breaker"].observed["circuit_state"] == "open"
    assert results["sandbox-path-escape"].observed["reason_code"] == "deny.path_traversal"
    assert results["lease-takeover-fencing"].observed["stale_owner_fenced"] is True
    assert not (tmp_path / "outside-secrets.txt").exists()

    session_path = Path(report.session_path)
    store = SessionStore(session_path.parent)
    events = store.read_events(session_path)
    event_types = [event.type for event in events]
    assert "provider.retry_scheduled" in event_types
    assert "provider.circuit_opened" in event_types
    assert "provider.circuit_rejected" in event_types
    assert "policy.command_checked" in event_types
    assert "turn.lease_taken_over" in event_types
    assert "turn.lease_lost" in event_types

    timeline = build_trace_timeline(events, session_id=report.session_id)
    assert timeline["trace_count"] == 4
    assert timeline["status_counts"] == {"completed": 4}
    assert all(trace["signal_count"] >= 1 for trace in timeline["traces"])
