from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

import pytest

from echoweave_agent_core import (
    AgentCore,
    OrphanRecoveryConfig,
    OrphanRecoveryScheduler,
    RecoverTurnRequest,
)
from echoweave_agent_core.recovery import OrphanTurnScanner
from echoweave_runtime.app import build_runtime
from echoweave_runtime.execution_leases import (
    ExecutionLeaseConfig,
    ExecutionLeaseCoordinator,
    ExecutionLeaseUnavailableError,
)
from echoweave_runtime.lifecycle import RuntimeHost
from echoweave_runtime.models.base import ModelClient
from echoweave_runtime.models.demo import SequenceModelClient
from echoweave_runtime.session.store import SessionStore
from echoweave_runtime.tools_base import ToolRegistry
from echoweave_runtime.types import AgentResponse


def _lease_config() -> ExecutionLeaseConfig:
    return ExecutionLeaseConfig(
        ttl_seconds=1.0,
        heartbeat_interval_seconds=0.1,
        lock_timeout_seconds=2.0,
        background_heartbeat=False,
    )


def _create_orphan(
    store: SessionStore,
    coordinator: ExecutionLeaseCoordinator,
    session_path: Path,
    *,
    turn_id: str,
    attempt: int = 1,
    state: str = "running",
    with_checkpoint: bool = True,
) -> dict | None:
    coordinator.acquire(session_path, turn_id=turn_id, trace_id=f"trace-{turn_id}")
    store.append(
        session_path,
        "turn.state_changed",
        {
            "turn_id": turn_id,
            "trace_id": f"trace-{turn_id}",
            "from": None,
            "state": "created",
            "sequence": 0,
            "attempt": attempt,
        },
    )
    if state != "created":
        store.append(
            session_path,
            "turn.state_changed",
            {
                "turn_id": turn_id,
                "trace_id": f"trace-{turn_id}",
                "from": "created",
                "state": state,
                "sequence": 1,
                "attempt": attempt,
            },
        )
    checkpoint = None
    if with_checkpoint:
        checkpoint = store.create_checkpoint(
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
    return checkpoint


def _core(
    store: SessionStore,
    coordinator: ExecutionLeaseCoordinator,
    model: ModelClient,
) -> AgentCore:
    return AgentCore(
        build_runtime(model, ToolRegistry(), store),
        store,
        execution_leases=coordinator,
    )


def _wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not met before timeout")


class _BlockingModel(ModelClient):
    def __init__(self, entered: threading.Event, release: threading.Event) -> None:
        self.entered = entered
        self.release = release

    def generate(self, messages, tools, options=None):
        self.entered.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("test model was not released")
        return AgentResponse(text="recovered")


def test_scanner_only_selects_expired_checkpointed_non_suspended_turns_and_isolates_bad_sessions(
    tmp_path: Path,
) -> None:
    now = [100.0]
    store = SessionStore(tmp_path / "sessions")
    session_path = store.create()
    old = ExecutionLeaseCoordinator(
        store,
        _lease_config(),
        owner_id="old",
        clock=lambda: now[0],
    )
    _create_orphan(store, old, session_path, turn_id="eligible")
    _create_orphan(store, old, session_path, turn_id="suspended", state="suspended")
    _create_orphan(store, old, session_path, turn_id="attempt-limit", attempt=3)
    _create_orphan(store, old, session_path, turn_id="no-checkpoint", with_checkpoint=False)
    (store.sessions_dir / "broken.jsonl").write_text("{not-json", encoding="utf-8")
    now[0] = 102.0
    replacement = ExecutionLeaseCoordinator(
        store,
        _lease_config(),
        owner_id="replacement",
        clock=lambda: now[0],
    )
    scanner = OrphanTurnScanner(
        _core(store, replacement, SequenceModelClient([AgentResponse(text="unused")])),
        OrphanRecoveryConfig(max_attempts_per_turn=3),
    )

    report = scanner.scan()

    assert [candidate.turn_id for candidate in report.candidates] == ["eligible"]
    assert report.candidates[0].latest_attempt == 1
    assert report.candidates[0].previous_owner_id == "old"
    assert report.scanned_sessions == 2
    assert len(report.issues) == 1
    assert report.issues[0].session_path.name == "broken.jsonl"


def test_scheduler_recovers_an_orphan_as_a_runtime_lifecycle_component(tmp_path: Path) -> None:
    now = [100.0]
    store = SessionStore(tmp_path / "sessions")
    session_path = store.create()
    config = _lease_config()
    old = ExecutionLeaseCoordinator(store, config, owner_id="old", clock=lambda: now[0])
    _create_orphan(store, old, session_path, turn_id="orphan")
    now[0] = 102.0
    replacement = ExecutionLeaseCoordinator(
        store,
        config,
        owner_id="replacement",
        clock=lambda: now[0],
    )
    core = _core(store, replacement, SequenceModelClient([AgentResponse(text="automatic")]))
    scheduler = OrphanRecoveryScheduler(
        core,
        OrphanRecoveryConfig(scan_interval_seconds=0.02),
    )
    host = RuntimeHost().register(scheduler)

    host.start()
    try:
        _wait_until(lambda: scheduler.snapshot().completed == 1)
    finally:
        host.stop()

    snapshot = scheduler.snapshot()
    assert snapshot.running is False
    assert snapshot.scheduled == 1
    assert snapshot.failed == 0
    events = store.read_events(session_path)
    recovery_started = [event for event in events if event.type == "turn.recovery_started"]
    assert len(recovery_started) == 1
    assert recovery_started[0].payload["mode"] == "automatic"
    assert recovery_started[0].payload["trigger"] == "expired_execution_lease"
    assert sum(event.type == "turn.lease_taken_over" for event in events) == 1


def test_scheduler_does_not_queue_the_same_turn_while_recovery_is_in_flight(
    tmp_path: Path,
) -> None:
    now = [100.0]
    entered = threading.Event()
    release = threading.Event()
    store = SessionStore(tmp_path / "sessions")
    session_path = store.create()
    config = _lease_config()
    old = ExecutionLeaseCoordinator(store, config, owner_id="old", clock=lambda: now[0])
    _create_orphan(store, old, session_path, turn_id="slow-orphan")
    now[0] = 102.0
    replacement = ExecutionLeaseCoordinator(
        store,
        config,
        owner_id="replacement",
        clock=lambda: now[0],
    )
    scheduler = OrphanRecoveryScheduler(
        _core(store, replacement, _BlockingModel(entered, release)),
        OrphanRecoveryConfig(scan_interval_seconds=0.01),
    )

    scheduler.start()
    try:
        assert entered.wait(timeout=3)
        for _ in range(5):
            scheduler.trigger_scan()
            time.sleep(0.02)
        assert scheduler.snapshot().scheduled == 1
        assert scheduler.snapshot().in_flight == 1
        release.set()
        _wait_until(lambda: scheduler.snapshot().completed == 1)
    finally:
        release.set()
        scheduler.stop()


def test_parallel_scheduler_requires_an_isolated_core_factory(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    coordinator = ExecutionLeaseCoordinator(
        store,
        _lease_config(),
        owner_id="replacement",
    )
    core = _core(store, coordinator, SequenceModelClient([AgentResponse(text="unused")]))

    with pytest.raises(ValueError, match="isolated runtime"):
        OrphanRecoveryScheduler(
            core,
            OrphanRecoveryConfig(max_concurrent_recoveries=2),
        )


def test_two_recovery_callers_mutate_the_event_stream_only_after_winning_the_lease(
    tmp_path: Path,
) -> None:
    now = [100.0]
    entered = threading.Event()
    release = threading.Event()
    store = SessionStore(tmp_path / "sessions")
    session_path = store.create()
    config = _lease_config()
    old = ExecutionLeaseCoordinator(store, config, owner_id="old", clock=lambda: now[0])
    checkpoint = _create_orphan(store, old, session_path, turn_id="raced")
    assert checkpoint is not None
    now[0] = 102.0
    cores = [
        _core(
            store,
            ExecutionLeaseCoordinator(
                store,
                config,
                owner_id=f"replacement-{index}",
                clock=lambda: now[0],
            ),
            _BlockingModel(entered, release),
        )
        for index in range(2)
    ]
    barrier = threading.Barrier(2)
    results = []
    errors = []
    result_lock = threading.Lock()

    def recover(core: AgentCore) -> None:
        barrier.wait()
        try:
            outcome = core.recover_turn(
                RecoverTurnRequest(
                    session_path=session_path,
                    checkpoint_id=str(checkpoint["id"]),
                )
            )
        except Exception as exc:
            with result_lock:
                errors.append(exc)
        else:
            with result_lock:
                results.append(outcome)

    threads = [threading.Thread(target=recover, args=(core,)) for core in cores]
    for thread in threads:
        thread.start()
    assert entered.wait(timeout=3)
    _wait_until(lambda: len(errors) == 1)
    release.set()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert len(results) == 1 and results[0].succeeded
    assert len(errors) == 1
    assert isinstance(errors[0], (ValueError, ExecutionLeaseUnavailableError))
    events = store.read_events(session_path)
    assert sum(event.type == "turn.recovery_started" for event in events) == 1
    assert sum(event.type == "history_reset" for event in events) == 1


def test_stale_recovery_snapshot_is_revalidated_after_a_fast_winner_releases(
    tmp_path: Path,
) -> None:
    class DelayedAcquireCoordinator(ExecutionLeaseCoordinator):
        def __init__(
            self,
            *args,
            acquire_entered: threading.Event,
            allow_acquire: threading.Event,
            **kwargs,
        ):
            super().__init__(*args, **kwargs)
            self.acquire_entered = acquire_entered
            self.allow_acquire = allow_acquire

        def acquire(self, session_path, *, turn_id, trace_id):
            self.acquire_entered.set()
            if not self.allow_acquire.wait(timeout=5):
                raise TimeoutError("delayed recovery was not released")
            return super().acquire(session_path, turn_id=turn_id, trace_id=trace_id)

    now = [100.0]
    acquire_entered = threading.Event()
    allow_acquire = threading.Event()
    store = SessionStore(tmp_path / "sessions")
    session_path = store.create()
    config = _lease_config()
    old = ExecutionLeaseCoordinator(store, config, owner_id="old", clock=lambda: now[0])
    checkpoint = _create_orphan(store, old, session_path, turn_id="fast-winner")
    assert checkpoint is not None
    now[0] = 102.0
    winner = _core(
        store,
        ExecutionLeaseCoordinator(store, config, owner_id="winner", clock=lambda: now[0]),
        SequenceModelClient([AgentResponse(text="winner")]),
    )
    delayed = _core(
        store,
        DelayedAcquireCoordinator(
            store,
            config,
            owner_id="delayed",
            clock=lambda: now[0],
            acquire_entered=acquire_entered,
            allow_acquire=allow_acquire,
        ),
        SequenceModelClient([AgentResponse(text="must not run")]),
    )
    delayed_errors = []

    def run_delayed() -> None:
        try:
            delayed.recover_turn(
                RecoverTurnRequest(
                    session_path=session_path,
                    checkpoint_id=str(checkpoint["id"]),
                )
            )
        except Exception as exc:
            delayed_errors.append(exc)

    thread = threading.Thread(target=run_delayed)
    thread.start()
    assert acquire_entered.wait(timeout=3)

    winner_outcome = winner.recover_turn(
        RecoverTurnRequest(
            session_path=session_path,
            checkpoint_id=str(checkpoint["id"]),
        )
    )
    assert winner_outcome.succeeded
    allow_acquire.set()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert len(delayed_errors) == 1
    assert "turn changed while acquiring recovery ownership" in str(delayed_errors[0])
    events = store.read_events(session_path)
    assert sum(event.type == "turn.recovery_started" for event in events) == 1
    assert sum(event.type == "history_reset" for event in events) == 1
