from __future__ import annotations

from pathlib import Path

from echoweave_harness.audit import read_audit_events
from echoweave_social.backend import EchoWeaveBackend, EchoWeaveBackendConfig


def test_backend_reliability_demo_registers_trace_and_eval_evidence(
    tmp_path: Path,
) -> None:
    audit_path = tmp_path / "audit.jsonl"
    backend = EchoWeaveBackend(
        EchoWeaveBackendConfig(
            default_workspace=tmp_path / "workspace",
            state_path=tmp_path / "state.json",
            sandbox_root=tmp_path / "sandboxes",
            provider="demo",
            harness_audit_path=audit_path,
        )
    )

    before = backend.fault_eval_status()
    result = backend.run_reliability_demo()
    after = backend.fault_eval_status()
    traces = backend.trace_overview(limit=20, event_limit_per_trace=80)

    assert before["available"] is False
    assert result["ok"] is True
    assert result["conversation_key"].startswith("demo:reliability-")
    assert result["report"]["passed"] is True
    assert result["report"]["scenario_count"] == 4
    assert after["available"] is True
    assert after["report"]["run_id"] == result["report"]["run_id"]
    assert traces["stats"]["registered_sessions"] == 1
    assert traces["stats"]["trace_count"] == 4
    assert traces["stats"]["signal_count"] >= 4
    assert all(
        trace["conversation_key"] == result["conversation_key"]
        for trace in traces["traces"]
    )
    assert {trace["status"] for trace in traces["traces"]} == {"completed"}
    assert Path(result["report"]["report_path"]).is_file()

    audit_events = read_audit_events(audit_path)
    demo_audit = [
        event
        for event in audit_events
        if event.category == "eval" and event.action == "fault_injection_demo"
    ]
    assert len(demo_audit) == 1
    assert demo_audit[0].metadata["overall_score"] == 1.0
