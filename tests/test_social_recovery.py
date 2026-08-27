from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from echoweave_agent_core import OrphanRecoveryConfig
from echoweave_runtime.execution_leases import (
    ExecutionLeaseConfig,
    ExecutionLeaseCoordinator,
)
from echoweave_runtime.session.store import SessionStore
from echoweave_social.agent_runtime import EchoWeaveSocialAgent, SocialAgentConfig
from echoweave_social.backend import EchoWeaveBackend, EchoWeaveBackendConfig
from echoweave_social.recovery import SocialOrphanScanner
from echoweave_social.schema import EchoWeaveEvent


def _wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not met before timeout")


def _append_expired_orphan(session_path: Path, *, turn_id: str) -> SessionStore:
    store = SessionStore(session_path.parent)
    old_owner = ExecutionLeaseCoordinator(
        store,
        ExecutionLeaseConfig(
            ttl_seconds=1.0,
            heartbeat_interval_seconds=0.1,
            lock_timeout_seconds=2.0,
            background_heartbeat=False,
        ),
        owner_id="crashed-social-worker",
        clock=lambda: 100.0,
    )
    old_owner.acquire(session_path, turn_id=turn_id, trace_id=f"trace-{turn_id}")
    store.append(
        session_path,
        "turn.state_changed",
        {
            "turn_id": turn_id,
            "trace_id": f"trace-{turn_id}",
            "from": None,
            "state": "created",
            "sequence": 0,
            "attempt": 1,
        },
    )
    store.append(
        session_path,
        "turn.state_changed",
        {
            "turn_id": turn_id,
            "trace_id": f"trace-{turn_id}",
            "from": "created",
            "state": "running",
            "sequence": 1,
            "attempt": 1,
        },
    )
    store.create_checkpoint(
        session_path,
        label=f"{turn_id}-start",
        turn_id=turn_id,
        trace_id=f"trace-{turn_id}",
    )
    store.append(
        session_path,
        "message",
        {"role": "user", "content": f"recover {turn_id}", "turn_id": turn_id},
    )
    return store


def _backend(tmp_path: Path, *, recovery_enabled: bool) -> EchoWeaveBackend:
    return EchoWeaveBackend(
        EchoWeaveBackendConfig(
            default_workspace=tmp_path / "workspace",
            state_path=tmp_path / "state.json",
            sandbox_root=tmp_path / "sandboxes",
            provider="demo",
            orphan_recovery_enabled=recovery_enabled,
            orphan_recovery_scan_interval_seconds=0.02,
            orphan_recovery_max_per_scan=2,
            orphan_recovery_max_attempts_per_turn=3,
        )
    )


def test_backend_recovers_an_owned_social_orphan_and_supports_hot_toggle(
    tmp_path: Path,
) -> None:
    backend = _backend(tmp_path, recovery_enabled=True)
    reply = backend.handle(
        EchoWeaveEvent(
            platform="web-user",
            conversation_id="recovery-room",
            sender_id="user-1",
            text="create the social session",
        )
    )
    assert reply.runtime_session_path
    session_path = Path(reply.runtime_session_path)
    store = _append_expired_orphan(session_path, turn_id="owned-orphan")

    assert backend.recovery_status()["running"] is False
    backend.start()
    try:
        _wait_until(
            lambda: backend.recovery_status().get("stats", {}).get("completed") == 1
        )
        status = backend.recovery_status()
        assert status["enabled"] is True
        assert status["backend_started"] is True
        assert status["running"] is True
        assert status["stats"]["scheduled"] == 1
        assert status["stats"]["failed"] == 0
        assert status["recent_results"][0]["conversation_key"] == "web-user:recovery-room"

        events = store.read_events(session_path)
        recovery_events = [
            event for event in events if event.type == "turn.recovery_started"
        ]
        assert len(recovery_events) == 1
        assert recovery_events[0].payload["mode"] == "automatic"
        assert recovery_events[0].payload["trigger"] == "expired_execution_lease"

        backend.update_admin_config({"orphan_recovery_enabled": False})
        assert backend.recovery_status()["running"] is False
        backend.update_admin_config({"orphan_recovery_enabled": True})
        assert backend.recovery_status()["running"] is True
    finally:
        backend.stop()

    assert backend.recovery_status()["backend_started"] is False
    assert backend.recovery_status()["running"] is False


def test_manual_scan_reports_but_does_not_run_an_unmanaged_social_orphan(
    tmp_path: Path,
) -> None:
    backend = _backend(tmp_path, recovery_enabled=False)
    reply = backend.handle(
        EchoWeaveEvent(
            platform="web-user",
            conversation_id="owned-room",
            sender_id="user-1",
            text="create an owned session",
        )
    )
    assert reply.runtime_session_path
    owned_session = Path(reply.runtime_session_path)
    store = SessionStore(owned_session.parent)
    unmanaged_session = store.create()
    _append_expired_orphan(unmanaged_session, turn_id="unmanaged-orphan")

    result = backend.scan_recovery(schedule=True)

    assert result["enabled"] is False
    assert result["scheduled_scan"] is False
    assert result["candidates"] == []
    assert any(
        issue["error_type"] == "UnmanagedRecoverySession"
        and issue["turn_id"] == "unmanaged-orphan"
        for issue in result["issues"]
    )
    events = store.read_events(unmanaged_session)
    assert not any(event.type == "turn.recovery_started" for event in events)


def test_scanner_rejects_ambiguous_social_session_ownership(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    store = SessionStore(workspace / "echoweave-data" / "sessions")
    session_path = store.create()
    _append_expired_orphan(session_path, turn_id="ambiguous-orphan")
    agent = EchoWeaveSocialAgent(
        SocialAgentConfig(
            default_workspace=workspace,
            state_path=tmp_path / "state.json",
            provider="demo",
        )
    )
    for conversation_key in ("web-user:first", "web-user:second"):
        record = agent.state.session(conversation_key)
        record["runtime_session"] = str(session_path)
        record["runtime_session_id"] = store.read_header(session_path).id
    agent.state.save()

    report = SocialOrphanScanner(agent, OrphanRecoveryConfig()).scan()

    assert report.candidates == ()
    assert any(
        issue.error_type == "AmbiguousRecoverySession"
        and "web-user:first" in issue.message
        and "web-user:second" in issue.message
        for issue in report.issues
    )
