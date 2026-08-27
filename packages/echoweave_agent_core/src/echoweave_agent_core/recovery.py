from __future__ import annotations

import threading
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal, Protocol

from echoweave_agent_core.outcomes import (
    TurnOutcome,
    TurnRecoveryConflictError,
    TurnState,
)
from echoweave_agent_core.types import RecoverTurnRequest
from echoweave_runtime.execution_leases import (
    ExecutionLeaseCoordinator,
    ExecutionLeaseUnavailableError,
)
from echoweave_runtime.session.store import SessionStore


@dataclass(frozen=True, slots=True)
class OrphanRecoveryConfig:
    """Conservative policy and bounded concurrency for automatic orphan recovery."""

    scan_interval_seconds: float = 5.0
    max_concurrent_recoveries: int = 1
    max_recoveries_per_scan: int = 4
    max_attempts_per_turn: int = 3
    recent_result_limit: int = 32

    def __post_init__(self) -> None:
        if self.scan_interval_seconds <= 0:
            raise ValueError("scan_interval_seconds must be positive")
        if self.max_concurrent_recoveries <= 0:
            raise ValueError("max_concurrent_recoveries must be positive")
        if self.max_recoveries_per_scan <= 0:
            raise ValueError("max_recoveries_per_scan must be positive")
        if self.max_attempts_per_turn < 2:
            raise ValueError("max_attempts_per_turn must allow at least one recovery attempt")
        if self.recent_result_limit <= 0:
            raise ValueError("recent_result_limit must be positive")


@dataclass(frozen=True, slots=True)
class OrphanTurnCandidate:
    session_path: Path
    session_id: str
    turn_id: str
    checkpoint_id: str
    latest_state: str
    latest_attempt: int
    previous_owner_id: str
    previous_fencing_token: int
    lease_expired_at: float

    @property
    def key(self) -> tuple[str, str]:
        return str(self.session_path.resolve()), self.turn_id


@dataclass(frozen=True, slots=True)
class OrphanScanIssue:
    session_path: Path
    turn_id: str | None
    error_type: str
    message: str


@dataclass(frozen=True, slots=True)
class OrphanScanReport:
    scanned_sessions: int
    candidates: tuple[OrphanTurnCandidate, ...]
    issues: tuple[OrphanScanIssue, ...]


@dataclass(frozen=True, slots=True)
class RecoveryDispatchResult:
    candidate: OrphanTurnCandidate
    status: Literal["completed", "failed", "contended"]
    started_at: float
    finished_at: float
    outcome: TurnOutcome | None = None
    error_type: str | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class RecoverySchedulerSnapshot:
    running: bool
    scans: int
    scanned_sessions: int
    candidates_found: int
    scheduled: int
    completed: int
    failed: int
    contended: int
    scan_issues: int
    in_flight: int
    last_scan_at: float | None
    last_scan_error: str | None
    recent_results: tuple[RecoveryDispatchResult, ...]


class _RecoveryCore(Protocol):
    session_store: SessionStore
    execution_leases: ExecutionLeaseCoordinator

    def recover_turn(self, request: RecoverTurnRequest) -> TurnOutcome: ...


class OrphanTurnScanner:
    """Builds a read-only projection of lease-proven orphan turns."""

    _eligible_states = frozenset(
        {
            TurnState.CREATED.value,
            TurnState.RUNNING.value,
            TurnState.WAITING_FOR_TOOL.value,
        }
    )

    def __init__(
        self,
        core: _RecoveryCore,
        config: OrphanRecoveryConfig | None = None,
    ) -> None:
        self.core = core
        self.config = config or OrphanRecoveryConfig()

    def scan(self) -> OrphanScanReport:
        candidates: list[OrphanTurnCandidate] = []
        issues: list[OrphanScanIssue] = []
        scanned_sessions = 0
        try:
            session_paths = self.core.session_store.list_paths()
        except Exception as exc:
            issues.append(self._issue(self.core.session_store.sessions_dir, None, exc))
            return OrphanScanReport(0, (), tuple(issues))

        for session_path in session_paths:
            scanned_sessions += 1
            try:
                events = self.core.session_store.read_events(session_path)
                session_id = self.core.session_store.read_header(session_path).id
            except Exception as exc:
                issues.append(self._issue(session_path, None, exc))
                continue

            latest_states: dict[str, dict[str, Any]] = {}
            checkpoints: dict[str, dict[str, Any]] = {}
            completed_turns: set[str] = set()
            abandoned_turns: set[str] = set()
            for event in events:
                payload = event.payload if isinstance(event.payload, dict) else {}
                turn_id = str(payload.get("turn_id") or "").strip()
                if not turn_id:
                    continue
                if event.type == "turn.state_changed":
                    latest_states[turn_id] = payload
                    if payload.get("state") == TurnState.COMPLETED.value:
                        completed_turns.add(turn_id)
                elif event.type == "turn.abandoned":
                    abandoned_turns.add(turn_id)
                elif event.type == "checkpoint.created" and turn_id not in checkpoints:
                    checkpoints[turn_id] = payload

            for turn_id, latest in latest_states.items():
                latest_state = str(latest.get("state") or "")
                latest_attempt = self._positive_int(latest.get("attempt"), default=1)
                checkpoint = checkpoints.get(turn_id)
                if (
                    latest_state not in self._eligible_states
                    or turn_id in completed_turns
                    or turn_id in abandoned_turns
                    or checkpoint is None
                    or latest_attempt >= self.config.max_attempts_per_turn
                ):
                    continue
                checkpoint_id = str(checkpoint.get("id") or "").strip()
                if not checkpoint_id:
                    continue
                try:
                    lease_status = self.core.execution_leases.inspect(session_path, turn_id)
                    if lease_status.get("state") != "expired":
                        continue
                    lease = lease_status.get("lease")
                    if not isinstance(lease, dict):
                        raise ValueError("expired lease projection is missing its lease record")
                    candidates.append(
                        OrphanTurnCandidate(
                            session_path=session_path.resolve(),
                            session_id=session_id,
                            turn_id=turn_id,
                            checkpoint_id=checkpoint_id,
                            latest_state=latest_state,
                            latest_attempt=latest_attempt,
                            previous_owner_id=str(lease.get("owner_id") or ""),
                            previous_fencing_token=int(lease.get("fencing_token") or 0),
                            lease_expired_at=float(lease.get("expires_at") or 0.0),
                        )
                    )
                except Exception as exc:
                    issues.append(self._issue(session_path, turn_id, exc))

        candidates.sort(
            key=lambda candidate: (
                candidate.lease_expired_at,
                candidate.session_id,
                candidate.turn_id,
            )
        )
        return OrphanScanReport(scanned_sessions, tuple(candidates), tuple(issues))

    @staticmethod
    def _positive_int(value: Any, *, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    @staticmethod
    def _issue(session_path: Path, turn_id: str | None, error: Exception) -> OrphanScanIssue:
        return OrphanScanIssue(
            session_path=session_path,
            turn_id=turn_id,
            error_type=type(error).__name__,
            message=str(error),
        )


class OrphanRecoveryScheduler:
    """Runtime lifecycle component that dispatches bounded orphan recoveries."""

    name = "orphan-recovery-scheduler"

    def __init__(
        self,
        core: _RecoveryCore,
        config: OrphanRecoveryConfig | None = None,
        *,
        core_factory: Callable[[OrphanTurnCandidate], _RecoveryCore] | None = None,
    ) -> None:
        self.core = core
        self.config = config or OrphanRecoveryConfig()
        if self.config.max_concurrent_recoveries > 1 and core_factory is None:
            raise ValueError(
                "parallel orphan recovery requires a core_factory that creates an isolated runtime"
            )
        self.core_factory = core_factory
        self.scanner = OrphanTurnScanner(core, self.config)
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._dispatcher: threading.Thread | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._in_flight: dict[
            tuple[str, str],
            tuple[OrphanTurnCandidate, Future[RecoveryDispatchResult]],
        ] = {}
        self._recent_results: deque[RecoveryDispatchResult] = deque(
            maxlen=self.config.recent_result_limit
        )
        self._scans = 0
        self._scanned_sessions = 0
        self._candidates_found = 0
        self._scheduled = 0
        self._completed = 0
        self._failed = 0
        self._contended = 0
        self._scan_issues = 0
        self._last_scan_at: float | None = None
        self._last_scan_error: str | None = None

    def start(self) -> None:
        with self._lock:
            if self._dispatcher is not None and self._dispatcher.is_alive():
                return
            self._stop_event.clear()
            self._wake_event.clear()
            self._executor = ThreadPoolExecutor(
                max_workers=self.config.max_concurrent_recoveries,
                thread_name_prefix="echoweave-orphan-recovery",
            )
            self._dispatcher = threading.Thread(
                target=self._run,
                name="echoweave-orphan-scanner",
                daemon=True,
            )
            self._dispatcher.start()

    def stop(self) -> None:
        with self._lock:
            dispatcher = self._dispatcher
            executor = self._executor
            if dispatcher is None and executor is None:
                return
            self._stop_event.set()
            self._wake_event.set()
        if dispatcher is not None and dispatcher is not threading.current_thread():
            dispatcher.join()
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
        with self._lock:
            self._dispatcher = None
            self._executor = None
            self._in_flight.clear()

    def trigger_scan(self) -> None:
        self._wake_event.set()

    def scan_now(self) -> OrphanScanReport:
        return self.scanner.scan()

    def snapshot(self) -> RecoverySchedulerSnapshot:
        with self._lock:
            return RecoverySchedulerSnapshot(
                running=self._dispatcher is not None and self._dispatcher.is_alive(),
                scans=self._scans,
                scanned_sessions=self._scanned_sessions,
                candidates_found=self._candidates_found,
                scheduled=self._scheduled,
                completed=self._completed,
                failed=self._failed,
                contended=self._contended,
                scan_issues=self._scan_issues,
                in_flight=len(self._in_flight),
                last_scan_at=self._last_scan_at,
                last_scan_error=self._last_scan_error,
                recent_results=tuple(self._recent_results),
            )

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._dispatch_once()
            except Exception as exc:
                with self._lock:
                    self._last_scan_error = f"{type(exc).__name__}: {exc}"
            self._wake_event.wait(self.config.scan_interval_seconds)
            self._wake_event.clear()

    def _dispatch_once(self) -> None:
        report = self.scanner.scan()
        now = time.time()
        with self._lock:
            self._scans += 1
            self._scanned_sessions += report.scanned_sessions
            self._candidates_found += len(report.candidates)
            self._scan_issues += len(report.issues)
            self._last_scan_at = now
            self._last_scan_error = (
                f"{report.issues[0].error_type}: {report.issues[0].message}"
                if report.issues
                else None
            )
            executor = self._executor
            capacity = self.config.max_concurrent_recoveries - len(self._in_flight)
        if executor is None or capacity <= 0 or self._stop_event.is_set():
            return

        dispatch_limit = min(capacity, self.config.max_recoveries_per_scan)
        dispatched = 0
        for candidate in report.candidates:
            if dispatched >= dispatch_limit or self._stop_event.is_set():
                break
            with self._lock:
                if candidate.key in self._in_flight or self._executor is not executor:
                    continue
                future = executor.submit(self._recover_candidate, candidate)
                self._in_flight[candidate.key] = (candidate, future)
                self._scheduled += 1
                future.add_done_callback(
                    lambda completed, key=candidate.key: self._recovery_finished(key, completed)
                )
            dispatched += 1

    def _recover_candidate(self, candidate: OrphanTurnCandidate) -> RecoveryDispatchResult:
        started_at = time.time()
        try:
            recovery_core = self.core_factory(candidate) if self.core_factory is not None else self.core
            outcome = recovery_core.recover_turn(
                RecoverTurnRequest(
                    session_path=candidate.session_path,
                    checkpoint_id=candidate.checkpoint_id,
                    metadata={
                        "automatic_recovery": True,
                        "recovery_trigger": "expired_execution_lease",
                        "orphan_previous_owner_id": candidate.previous_owner_id,
                        "orphan_previous_fencing_token": candidate.previous_fencing_token,
                    },
                )
            )
        except ExecutionLeaseUnavailableError as exc:
            return RecoveryDispatchResult(
                candidate,
                "contended",
                started_at,
                time.time(),
                error_type=type(exc).__name__,
                message=str(exc),
            )
        except TurnRecoveryConflictError as exc:
            return RecoveryDispatchResult(
                candidate,
                "contended",
                started_at,
                time.time(),
                error_type=type(exc).__name__,
                message=str(exc),
            )
        except ValueError as exc:
            return RecoveryDispatchResult(
                candidate,
                "failed",
                started_at,
                time.time(),
                error_type=type(exc).__name__,
                message=str(exc),
            )
        except Exception as exc:
            return RecoveryDispatchResult(
                candidate,
                "failed",
                started_at,
                time.time(),
                error_type=type(exc).__name__,
                message=str(exc),
            )
        return RecoveryDispatchResult(
            candidate,
            "completed" if outcome.succeeded else "failed",
            started_at,
            time.time(),
            outcome=outcome,
            error_type=outcome.failure.error_type if outcome.failure else None,
            message=outcome.failure.message if outcome.failure else None,
        )

    def _recovery_finished(
        self,
        key: tuple[str, str],
        future: Future[RecoveryDispatchResult],
    ) -> None:
        with self._lock:
            registration = self._in_flight.get(key)
        candidate = registration[0] if registration is not None else None
        try:
            result = future.result()
        except BaseException as exc:
            if candidate is None:
                with self._lock:
                    self._in_flight.pop(key, None)
                    self._failed += 1
                self._wake_event.set()
                return
            result = RecoveryDispatchResult(
                candidate,
                "failed",
                time.time(),
                time.time(),
                error_type=type(exc).__name__,
                message=str(exc),
            )
        with self._lock:
            self._in_flight.pop(key, None)
            self._recent_results.append(result)
            if result.status == "completed":
                self._completed += 1
            elif result.status == "contended":
                self._contended += 1
            else:
                self._failed += 1
        self._wake_event.set()


__all__ = [
    "OrphanRecoveryConfig",
    "OrphanRecoveryScheduler",
    "OrphanScanIssue",
    "OrphanScanReport",
    "OrphanTurnCandidate",
    "RecoveryDispatchResult",
    "RecoverySchedulerSnapshot",
]
