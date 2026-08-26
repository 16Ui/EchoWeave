from __future__ import annotations

import multiprocessing
import shutil
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest

from echoweave_agent_core import (
    AgentCore,
    AgentCoreConfig,
    RecoverTurnRequest,
    TurnFailureKind,
    TurnRequest,
)
from echoweave_runtime.app import build_runtime
from echoweave_runtime.execution_leases import (
    ExecutionLeaseConfig,
    ExecutionLeaseCoordinator,
    ExecutionLeaseLostError,
    ExecutionLeaseUnavailableError,
)
from echoweave_runtime.models.base import ModelClient
from echoweave_runtime.models.demo import AgentResponse, SequenceModelClient
from echoweave_runtime.session.store import SessionStore
from echoweave_runtime.tools_base import ToolRegistry
from echoweave_runtime.types import ToolCall


@contextmanager
def _local_tmp():
    path = Path.cwd() / ".test-data" / uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _config(
    *,
    ttl: float = 10.0,
    heartbeat: float = 1.0,
    background: bool = False,
) -> ExecutionLeaseConfig:
    return ExecutionLeaseConfig(
        ttl_seconds=ttl,
        heartbeat_interval_seconds=heartbeat,
        lock_timeout_seconds=2.0,
        background_heartbeat=background,
    )


def _process_lease_contender(
    sessions_dir: str,
    session_path: str,
    owner_id: str,
    start_event,
    result_queue,
) -> None:
    store = SessionStore(Path(sessions_dir))
    coordinator = ExecutionLeaseCoordinator(
        store,
        ExecutionLeaseConfig(
            ttl_seconds=5.0,
            heartbeat_interval_seconds=1.0,
            lock_timeout_seconds=2.0,
            background_heartbeat=False,
        ),
        owner_id=owner_id,
    )
    start_event.wait(timeout=5)
    try:
        lease = coordinator.acquire(Path(session_path), turn_id="process-turn", trace_id=owner_id)
    except ExecutionLeaseUnavailableError:
        result_queue.put(("rejected", owner_id, None))
        return
    result_queue.put(("acquired", owner_id, lease.fencing_token))
    time.sleep(0.75)
    coordinator.release(Path(session_path), lease, trace_id=owner_id, reason="process-test")


def test_coordinator_is_a_keyed_process_singleton_per_session_store_root() -> None:
    with _local_tmp() as tmp_path:
        first_store = SessionStore(tmp_path / "sessions")
        second_store = SessionStore(tmp_path / "sessions")
        config = _config()

        first = ExecutionLeaseCoordinator.for_store(first_store, config, owner_id="same-owner")
        second = ExecutionLeaseCoordinator.for_store(second_store, config, owner_id="same-owner")

        assert first is second
        assert first.owner_id == "same-owner"
        with pytest.raises(ValueError, match="owner differs"):
            ExecutionLeaseCoordinator.for_store(second_store, config, owner_id="other-owner")


def test_two_threads_compete_for_one_turn_and_only_one_owner_wins() -> None:
    with _local_tmp() as tmp_path:
        store = SessionStore(tmp_path / "sessions")
        session_path = store.create()
        config = _config()
        coordinators = [
            ExecutionLeaseCoordinator(store, config, owner_id="owner-a"),
            ExecutionLeaseCoordinator(store, config, owner_id="owner-b"),
        ]
        barrier = threading.Barrier(2)
        successes = []
        failures = []
        result_lock = threading.Lock()

        def compete(coordinator: ExecutionLeaseCoordinator) -> None:
            barrier.wait()
            try:
                lease = coordinator.acquire(session_path, turn_id="turn", trace_id=coordinator.owner_id)
            except ExecutionLeaseUnavailableError as exc:
                with result_lock:
                    failures.append(exc)
            else:
                with result_lock:
                    successes.append((coordinator, lease))

        threads = [threading.Thread(target=compete, args=(coordinator,)) for coordinator in coordinators]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        assert all(not thread.is_alive() for thread in threads)
        assert len(successes) == 1
        assert len(failures) == 1
        winner, lease = successes[0]
        assert lease.fencing_token == 1
        assert winner.release(session_path, lease, trace_id="done", reason="test") is True
        events = store.read_events(session_path)
        assert sum(event.type == "turn.lease_acquired" for event in events) == 1
        assert sum(event.type == "turn.lease_rejected" for event in events) == 1


def test_two_processes_are_serialized_by_the_sidecar_file_lock() -> None:
    with _local_tmp() as tmp_path:
        sessions_dir = tmp_path / "sessions"
        store = SessionStore(sessions_dir)
        session_path = store.create()
        context = multiprocessing.get_context("spawn")
        start_event = context.Event()
        result_queue = context.Queue()
        processes = [
            context.Process(
                target=_process_lease_contender,
                args=(
                    str(sessions_dir),
                    str(session_path),
                    owner_id,
                    start_event,
                    result_queue,
                ),
            )
            for owner_id in ("process-a", "process-b")
        ]
        for process in processes:
            process.start()
        start_event.set()
        results = [result_queue.get(timeout=10) for _ in processes]
        for process in processes:
            process.join(timeout=10)

        assert all(process.exitcode == 0 for process in processes)
        assert sorted(result[0] for result in results) == ["acquired", "rejected"]
        assert next(result[2] for result in results if result[0] == "acquired") == 1


def test_failed_acquire_audit_rolls_back_owner_and_heartbeat_registration() -> None:
    class FailAcquireEventStore(SessionStore):
        def __init__(self, sessions_dir: Path) -> None:
            super().__init__(sessions_dir)
            self.failed = False

        def append(self, session_path, event_type, payload):
            if event_type == "turn.lease_acquired" and not self.failed:
                self.failed = True
                raise OSError("injected lease audit failure")
            return super().append(session_path, event_type, payload)

    with _local_tmp() as tmp_path:
        store = FailAcquireEventStore(tmp_path / "sessions")
        session_path = store.create()
        config = _config()
        first = ExecutionLeaseCoordinator(store, config, owner_id="first")

        with pytest.raises(OSError, match="lease audit failure"):
            first.acquire(session_path, turn_id="turn", trace_id="trace-1")

        assert first.inspect(session_path, "turn")["state"] == "released"
        second = ExecutionLeaseCoordinator(store, config, owner_id="second")
        lease = second.acquire(session_path, turn_id="turn", trace_id="trace-2")
        assert lease.fencing_token == 2
        assert second.release(session_path, lease, trace_id="done", reason="test") is True


def test_expired_lease_can_be_taken_over_and_stale_fencing_token_is_rejected() -> None:
    with _local_tmp() as tmp_path:
        now = [100.0]
        store = SessionStore(tmp_path / "sessions")
        session_path = store.create()
        config = _config()
        old = ExecutionLeaseCoordinator(store, config, owner_id="old", clock=lambda: now[0])
        new = ExecutionLeaseCoordinator(store, config, owner_id="new", clock=lambda: now[0])
        old_lease = old.acquire(session_path, turn_id="turn", trace_id="trace-old")

        with pytest.raises(ExecutionLeaseUnavailableError):
            new.acquire(session_path, turn_id="turn", trace_id="too-early")

        now[0] += 11.0
        new_lease = new.acquire(session_path, turn_id="turn", trace_id="trace-new")

        assert new_lease.fencing_token == old_lease.fencing_token + 1
        with pytest.raises(ExecutionLeaseLostError):
            old.assert_owned(old_lease)
        assert old.release(session_path, old_lease, trace_id="stale", reason="stale") is False
        assert new.release(session_path, new_lease, trace_id="done", reason="test") is True
        events = store.read_events(session_path)
        takeover = next(event for event in events if event.type == "turn.lease_taken_over")
        assert takeover.payload["previous_owner_id"] == "old"
        assert takeover.payload["fencing_token"] == 2
        assert sum(event.type == "turn.lease_lost" for event in events) == 1


def test_single_background_scheduler_keeps_active_lease_alive() -> None:
    with _local_tmp() as tmp_path:
        store = SessionStore(tmp_path / "sessions")
        session_path = store.create()
        coordinator = ExecutionLeaseCoordinator(
            store,
            _config(ttl=0.25, heartbeat=0.05, background=True),
            owner_id="heartbeat-owner",
        )
        lease = coordinator.acquire(session_path, turn_id="turn", trace_id="trace")

        time.sleep(0.35)

        status = coordinator.inspect(session_path, "turn")
        assert status["state"] == "active"
        assert status["lease"]["heartbeat_at"] > lease.heartbeat_at
        assert coordinator.release(session_path, lease, trace_id="done", reason="test") is True


def test_session_store_serializes_concurrent_jsonl_appends_across_instances() -> None:
    with _local_tmp() as tmp_path:
        root = tmp_path / "sessions"
        creator = SessionStore(root)
        session_path = creator.create()
        stores = [SessionStore(root) for _ in range(8)]
        barrier = threading.Barrier(len(stores))

        def append_many(worker_index: int) -> None:
            barrier.wait()
            store = stores[worker_index]
            for item_index in range(40):
                store.append(
                    session_path,
                    "concurrency.test",
                    {"worker": worker_index, "index": item_index},
                )

        threads = [threading.Thread(target=append_many, args=(index,)) for index in range(len(stores))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert all(not thread.is_alive() for thread in threads)
        events = creator.read_events(session_path)
        concurrent_events = [event for event in events if event.type == "concurrency.test"]
        assert len(concurrent_events) == 320
        assert {
            (event.payload["worker"], event.payload["index"])
            for event in concurrent_events
        } == {(worker, index) for worker in range(8) for index in range(40)}


def test_recover_turn_automatically_takes_over_an_expired_orphan_lease() -> None:
    with _local_tmp() as tmp_path:
        store = SessionStore(tmp_path / "sessions")
        session_path = store.create()
        config = _config(ttl=0.2, heartbeat=0.05, background=False)
        old = ExecutionLeaseCoordinator(store, config, owner_id="crashed-process")
        old.acquire(session_path, turn_id="orphan-turn", trace_id="old-trace")
        store.append(
            session_path,
            "turn.state_changed",
            {
                "turn_id": "orphan-turn",
                "trace_id": "old-trace",
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
                "turn_id": "orphan-turn",
                "trace_id": "old-trace",
                "from": "created",
                "state": "running",
                "sequence": 1,
                "attempt": 1,
            },
        )
        checkpoint = store.create_checkpoint(
            session_path,
            label="orphan-start",
            turn_id="orphan-turn",
            trace_id="old-trace",
        )
        store.append(
            session_path,
            "message",
            {"role": "user", "content": "recover orphan"},
        )
        recovered_core = AgentCore.from_config(
            AgentCoreConfig(
                model_client=SequenceModelClient([AgentResponse(text="taken over")]),
                tool_registry=ToolRegistry(),
                session_store=store,
                execution_lease=config,
                execution_owner_id="replacement-process",
            )
        )

        with pytest.raises(ValueError, match="active execution lease"):
            recovered_core.recover_turn(
                RecoverTurnRequest(session_path=session_path, checkpoint_id=str(checkpoint["id"]))
            )

        time.sleep(0.25)
        recovered = recovered_core.recover_turn(
            RecoverTurnRequest(session_path=session_path, checkpoint_id=str(checkpoint["id"]))
        )

        assert recovered.succeeded is True
        assert recovered.require_result().text == "taken over"
        assert recovered.result.metadata["execution_owner_id"] == "replacement-process"
        assert recovered.result.metadata["fencing_token"] == 2
        events = store.read_events(session_path)
        assert sum(event.type == "turn.lease_taken_over" for event in events) == 1


def test_stale_owner_is_fenced_before_tool_side_effect_executes() -> None:
    class SideEffectTool:
        name = "side_effect"
        effect = "non_idempotent"
        description = "Must not run after ownership changes"
        input_schema = {"type": "object", "properties": {}, "additionalProperties": False}

        def __init__(self) -> None:
            self.calls = 0

        def execute(self, arguments):
            self.calls += 1
            return "executed"

    class TakeoverModel(ModelClient):
        def __init__(self, takeover) -> None:
            self.takeover = takeover

        def generate(self, messages, tools, options=None):
            self.takeover()
            return AgentResponse(
                tool_calls=[ToolCall(id="stale-call", name="side_effect", input={})]
            )

    with _local_tmp() as tmp_path:
        now = [100.0]
        store = SessionStore(tmp_path / "sessions")
        session_path = store.create()
        config = _config()
        old = ExecutionLeaseCoordinator(store, config, owner_id="old", clock=lambda: now[0])
        replacement = ExecutionLeaseCoordinator(
            store,
            config,
            owner_id="replacement",
            clock=lambda: now[0],
        )
        replacement_lease = []

        def takeover() -> None:
            now[0] += 11.0
            replacement_lease.append(
                replacement.acquire(
                    session_path,
                    turn_id="fenced-turn",
                    trace_id="replacement-trace",
                )
            )

        tool = SideEffectTool()
        registry = ToolRegistry()
        registry.register(tool)
        runtime = build_runtime(TakeoverModel(takeover), registry, store)
        core = AgentCore(runtime, store, execution_leases=old)

        outcome = core._execute_turn(
            TurnRequest(prompt="do not let stale owner write", session_path=session_path),
            create_checkpoint=True,
            turn_id="fenced-turn",
            trace_id="old-trace",
        )

        assert outcome.failure is not None
        assert outcome.failure.kind is TurnFailureKind.CONCURRENCY
        assert tool.calls == 0
        assert not any(
            event.type == "tool.invocation_started"
            for event in store.read_events(session_path)
        )
        assert replacement.release(
            session_path,
            replacement_lease[0],
            trace_id="replacement-done",
            reason="test",
        ) is True
