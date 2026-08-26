from __future__ import annotations

import hashlib
import json
import os
import socket
import threading
import time
import weakref
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from echoweave_runtime.concurrency import InterProcessFileLock, KeyedRLockPool
from echoweave_runtime.session.store import SessionStore


@dataclass(frozen=True)
class ExecutionLeaseConfig:
    ttl_seconds: float = 30.0
    heartbeat_interval_seconds: float = 5.0
    lock_timeout_seconds: float = 5.0
    background_heartbeat: bool = True

    def __post_init__(self) -> None:
        if self.ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if self.heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat_interval_seconds must be positive")
        if self.heartbeat_interval_seconds >= self.ttl_seconds:
            raise ValueError("heartbeat_interval_seconds must be lower than ttl_seconds")
        if self.lock_timeout_seconds < 0:
            raise ValueError("lock_timeout_seconds must be non-negative")


@dataclass(frozen=True)
class ExecutionLease:
    lease_key: str
    session_id: str
    turn_id: str
    owner_id: str
    fencing_token: int
    trace_id: str | None
    acquired_at: float
    heartbeat_at: float
    expires_at: float
    status: str = "active"

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ExecutionLease":
        return cls(
            lease_key=str(value["lease_key"]),
            session_id=str(value["session_id"]),
            turn_id=str(value["turn_id"]),
            owner_id=str(value["owner_id"]),
            fencing_token=int(value["fencing_token"]),
            trace_id=str(value["trace_id"]) if value.get("trace_id") is not None else None,
            acquired_at=float(value["acquired_at"]),
            heartbeat_at=float(value["heartbeat_at"]),
            expires_at=float(value["expires_at"]),
            status=str(value.get("status") or "active"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ExecutionLeaseUnavailableError(RuntimeError):
    def __init__(self, current: ExecutionLease, now: float) -> None:
        self.current = current
        self.retry_after_seconds = max(0.0, current.expires_at - now)
        super().__init__(
            f"turn {current.turn_id} is owned by {current.owner_id}; "
            f"retry after {self.retry_after_seconds:.3f}s"
        )


class ExecutionLeaseLostError(RuntimeError):
    def __init__(self, lease: ExecutionLease, current: ExecutionLease | None) -> None:
        self.lease = lease
        self.current = current
        current_owner = current.owner_id if current is not None else "none"
        current_token = current.fencing_token if current is not None else "none"
        super().__init__(
            f"execution lease lost for turn {lease.turn_id}: expected "
            f"{lease.owner_id}/{lease.fencing_token}, found {current_owner}/{current_token}"
        )


class ExecutionLeaseCorruptError(RuntimeError):
    pass


@dataclass(frozen=True)
class _HeartbeatRegistration:
    coordinator: weakref.ReferenceType["ExecutionLeaseCoordinator"]
    lease_key: str
    interval_seconds: float
    next_due: float


class _LeaseHeartbeatScheduler:
    """One daemon thread schedules heartbeats for every coordinator in this process."""

    _instance: "_LeaseHeartbeatScheduler | None" = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> "_LeaseHeartbeatScheduler":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._condition = threading.Condition(threading.RLock())
                    instance._registrations = {}
                    instance._thread = None
                    cls._instance = instance
        return cls._instance

    def register(self, coordinator: "ExecutionLeaseCoordinator", lease: ExecutionLease) -> None:
        key = (id(coordinator), lease.lease_key)
        interval = coordinator.config.heartbeat_interval_seconds
        with self._condition:
            self._registrations[key] = _HeartbeatRegistration(
                weakref.ref(coordinator),
                lease.lease_key,
                interval,
                time.monotonic() + interval,
            )
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._run,
                    name="echoweave-lease-heartbeat",
                    daemon=True,
                )
                self._thread.start()
            self._condition.notify_all()

    def unregister(self, coordinator: "ExecutionLeaseCoordinator", lease_key: str) -> None:
        with self._condition:
            self._registrations.pop((id(coordinator), lease_key), None)
            self._condition.notify_all()

    def _run(self) -> None:
        while True:
            with self._condition:
                if not self._registrations:
                    self._condition.wait()
                    continue
                now = time.monotonic()
                next_due = min(item.next_due for item in self._registrations.values())
                wait_seconds = next_due - now
                if wait_seconds > 0:
                    self._condition.wait(timeout=wait_seconds)
                    continue
                due = [
                    (key, item)
                    for key, item in self._registrations.items()
                    if item.next_due <= now
                ]
            for key, item in due:
                coordinator = item.coordinator()
                keep = coordinator is not None and coordinator._heartbeat_scheduled(item.lease_key)
                with self._condition:
                    current = self._registrations.get(key)
                    if current is not item:
                        continue
                    if not keep:
                        self._registrations.pop(key, None)
                    else:
                        self._registrations[key] = _HeartbeatRegistration(
                            item.coordinator,
                            item.lease_key,
                            item.interval_seconds,
                            time.monotonic() + item.interval_seconds,
                        )


class ExecutionLeaseCoordinator:
    """Coordinates thread-local use and cross-process ownership of logical turns."""

    _instances: weakref.WeakValueDictionary[str, "ExecutionLeaseCoordinator"] = (
        weakref.WeakValueDictionary()
    )
    _instances_lock = threading.Lock()

    def __init__(
        self,
        session_store: SessionStore,
        config: ExecutionLeaseConfig | None = None,
        *,
        owner_id: str | None = None,
        clock: Any = time.time,
    ) -> None:
        self.session_store = session_store
        self.config = config or ExecutionLeaseConfig()
        self.owner_id = owner_id or f"{socket.gethostname()}:{os.getpid()}:{uuid4()}"
        self._clock = clock
        self._lease_dir = session_store.sessions_dir.resolve() / ".execution-leases"
        self._lease_dir.mkdir(parents=True, exist_ok=True)
        self._active_lock = threading.RLock()
        self._active: dict[str, ExecutionLease] = {}
        self._active_paths: dict[str, Path] = {}
        self._lost_reported: set[str] = set()
        self._keyed_locks = KeyedRLockPool()
        self._scheduler = _LeaseHeartbeatScheduler()

    @classmethod
    def for_store(
        cls,
        session_store: SessionStore,
        config: ExecutionLeaseConfig | None = None,
        *,
        owner_id: str | None = None,
    ) -> "ExecutionLeaseCoordinator":
        key = str(session_store.sessions_dir.expanduser().resolve())
        requested_config = config or ExecutionLeaseConfig()
        with cls._instances_lock:
            existing = cls._instances.get(key)
            if existing is not None:
                if existing.config != requested_config:
                    raise ValueError("execution lease config differs for the same SessionStore root")
                if owner_id is not None and existing.owner_id != owner_id:
                    raise ValueError("execution lease owner differs for the same SessionStore root")
                return existing
            coordinator = cls(session_store, requested_config, owner_id=owner_id)
            cls._instances[key] = coordinator
            return coordinator

    def acquire(
        self,
        session_path: Path,
        *,
        turn_id: str,
        trace_id: str | None,
    ) -> ExecutionLease:
        session_id = self.session_store.read_header(session_path).id
        lease_key = self._lease_key(session_id, turn_id)
        now = float(self._clock())
        action = "acquired"
        previous_owner_id: str | None = None
        with self._mutation_lock(lease_key):
            current = self._read_record(lease_key)
            if current is not None and current.status == "active" and current.expires_at > now:
                self._append_event(
                    session_path,
                    "turn.lease_rejected",
                    current,
                    trace_id=trace_id,
                    requested_owner_id=self.owner_id,
                    retry_after_seconds=current.expires_at - now,
                )
                raise ExecutionLeaseUnavailableError(current, now)
            previous_token = current.fencing_token if current is not None else 0
            if current is not None and current.status == "active":
                action = "taken_over"
                previous_owner_id = current.owner_id
            lease = ExecutionLease(
                lease_key=lease_key,
                session_id=session_id,
                turn_id=turn_id,
                owner_id=self.owner_id,
                fencing_token=previous_token + 1,
                trace_id=trace_id,
                acquired_at=now,
                heartbeat_at=now,
                expires_at=now + self.config.ttl_seconds,
            )
            self._write_record(lease)
        with self._active_lock:
            self._active[lease_key] = lease
            self._active_paths[lease_key] = session_path
            self._lost_reported.discard(lease_key)
        if self.config.background_heartbeat:
            self._scheduler.register(self, lease)
        event_type = "turn.lease_taken_over" if action == "taken_over" else "turn.lease_acquired"
        try:
            self._append_event(
                session_path,
                event_type,
                lease,
                previous_owner_id=previous_owner_id,
            )
        except Exception:
            self._rollback_acquire(lease)
            raise
        return lease

    def heartbeat(self, lease: ExecutionLease) -> ExecutionLease:
        now = float(self._clock())
        try:
            with self._mutation_lock(lease.lease_key):
                current = self._read_record(lease.lease_key)
                self._assert_matches(lease, current, now=now)
                renewed = ExecutionLease(
                    **{
                        **lease.to_dict(),
                        "heartbeat_at": now,
                        "expires_at": now + self.config.ttl_seconds,
                    }
                )
                self._write_record(renewed)
        except ExecutionLeaseLostError as exc:
            self._report_lost(exc)
            raise
        with self._active_lock:
            self._active[lease.lease_key] = renewed
        return renewed

    def assert_owned(self, lease: ExecutionLease) -> ExecutionLease:
        now = float(self._clock())
        try:
            with self._mutation_lock(lease.lease_key):
                current = self._read_record(lease.lease_key)
                self._assert_matches(lease, current, now=now)
                assert current is not None
                return current
        except ExecutionLeaseLostError as exc:
            self._report_lost(exc)
            raise

    def release(
        self,
        session_path: Path,
        lease: ExecutionLease,
        *,
        trace_id: str | None,
        reason: str,
    ) -> bool:
        self._scheduler.unregister(self, lease.lease_key)
        with self._active_lock:
            self._active.pop(lease.lease_key, None)
            self._active_paths.pop(lease.lease_key, None)
            self._lost_reported.discard(lease.lease_key)
        now = float(self._clock())
        with self._mutation_lock(lease.lease_key):
            current = self._read_record(lease.lease_key)
            if not self._matches(lease, current):
                return False
            assert current is not None
            released = ExecutionLease(
                **{
                    **current.to_dict(),
                    "trace_id": trace_id,
                    "heartbeat_at": now,
                    "expires_at": now,
                    "status": "released",
                }
            )
            self._write_record(released)
        self._append_event(
            session_path,
            "turn.lease_released",
            released,
            reason=reason,
        )
        return True

    def inspect(self, session_path: Path, turn_id: str) -> dict[str, Any]:
        session_id = self.session_store.read_header(session_path).id
        lease_key = self._lease_key(session_id, turn_id)
        now = float(self._clock())
        with self._mutation_lock(lease_key):
            current = self._read_record(lease_key)
        if current is None:
            return {"state": "missing", "lease_key": lease_key, "takeover_allowed": False}
        if current.status == "released":
            state = "released"
        elif current.expires_at <= now:
            state = "expired"
        else:
            state = "active"
        return {
            "state": state,
            "lease_key": lease_key,
            "takeover_allowed": state in {"released", "expired"},
            "retry_after_seconds": max(0.0, current.expires_at - now),
            "lease": current.to_dict(),
        }

    def _heartbeat_scheduled(self, lease_key: str) -> bool:
        with self._active_lock:
            lease = self._active.get(lease_key)
        if lease is None:
            return False
        try:
            self.heartbeat(lease)
            return True
        except Exception:
            with self._active_lock:
                self._active.pop(lease_key, None)
                self._active_paths.pop(lease_key, None)
            return False

    def _report_lost(self, error: ExecutionLeaseLostError) -> None:
        with self._active_lock:
            if error.lease.lease_key in self._lost_reported:
                return
            session_path = self._active_paths.get(error.lease.lease_key)
            self._lost_reported.add(error.lease.lease_key)
        if session_path is None:
            return
        current = error.current
        try:
            self._append_event(
                session_path,
                "turn.lease_lost",
                error.lease,
                current_owner_id=current.owner_id if current else None,
                current_fencing_token=current.fencing_token if current else None,
            )
        except Exception:
            # Ownership fencing must not be hidden by telemetry persistence failure.
            return

    def _rollback_acquire(self, lease: ExecutionLease) -> None:
        self._scheduler.unregister(self, lease.lease_key)
        with self._active_lock:
            self._active.pop(lease.lease_key, None)
            self._active_paths.pop(lease.lease_key, None)
        now = float(self._clock())
        with self._mutation_lock(lease.lease_key):
            current = self._read_record(lease.lease_key)
            if not self._matches(lease, current):
                return
            assert current is not None
            self._write_record(
                ExecutionLease(
                    **{
                        **current.to_dict(),
                        "heartbeat_at": now,
                        "expires_at": now,
                        "status": "released",
                    }
                )
            )

    def _assert_matches(
        self,
        lease: ExecutionLease,
        current: ExecutionLease | None,
        *,
        now: float,
    ) -> None:
        if not self._matches(lease, current) or current is None or current.expires_at <= now:
            raise ExecutionLeaseLostError(lease, current)

    @staticmethod
    def _matches(lease: ExecutionLease, current: ExecutionLease | None) -> bool:
        return (
            current is not None
            and current.status == "active"
            and current.owner_id == lease.owner_id
            and current.fencing_token == lease.fencing_token
        )

    def _mutation_lock(self, lease_key: str):
        class _CombinedLock:
            def __init__(self, outer: "ExecutionLeaseCoordinator") -> None:
                self.local = outer._keyed_locks.lock_for(outer._record_path(lease_key))
                self.file = InterProcessFileLock(
                    outer._lock_path(lease_key),
                    timeout_seconds=outer.config.lock_timeout_seconds,
                )

            def __enter__(self):
                self.local.acquire()
                try:
                    self.file.acquire()
                except BaseException:
                    self.local.release()
                    raise
                return self

            def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
                try:
                    self.file.release()
                finally:
                    self.local.release()
                return False

        return _CombinedLock(self)

    def _append_event(
        self,
        session_path: Path,
        event_type: str,
        lease: ExecutionLease,
        **extra: Any,
    ) -> None:
        self.session_store.append(
            session_path,
            event_type,
            {**lease.to_dict(), **extra},
        )

    def _read_record(self, lease_key: str) -> ExecutionLease | None:
        path = self._record_path(lease_key)
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise TypeError("lease record must be an object")
            return ExecutionLease.from_dict(value)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ExecutionLeaseCorruptError(f"invalid execution lease record: {path}") from exc

    def _write_record(self, lease: ExecutionLease) -> None:
        path = self._record_path(lease.lease_key)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(lease.to_dict(), ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _record_path(self, lease_key: str) -> Path:
        return self._lease_dir / f"{lease_key}.json"

    def _lock_path(self, lease_key: str) -> Path:
        return self._lease_dir / "locks" / f"{lease_key}.lock"

    @staticmethod
    def _lease_key(session_id: str, turn_id: str) -> str:
        return hashlib.sha256(f"{session_id}:{turn_id}".encode("utf-8")).hexdigest()


__all__ = [
    "ExecutionLease",
    "ExecutionLeaseConfig",
    "ExecutionLeaseCoordinator",
    "ExecutionLeaseCorruptError",
    "ExecutionLeaseLostError",
    "ExecutionLeaseUnavailableError",
]
