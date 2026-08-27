from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from echoweave_agent_core import (
    OrphanRecoveryConfig,
    OrphanRecoveryScheduler,
    OrphanScanIssue,
    OrphanScanReport,
    OrphanTurnCandidate,
)
from echoweave_agent_core.recovery import OrphanTurnScanner
from echoweave_runtime.execution_leases import ExecutionLeaseCoordinator
from echoweave_runtime.session.store import SessionStore
from echoweave_social.agent_runtime import EchoWeaveSocialAgent, SocialRecoveryContext


@dataclass(frozen=True, slots=True)
class _ScanCore:
    session_store: SessionStore
    execution_leases: ExecutionLeaseCoordinator


class SocialOrphanScanner:
    """Aggregate one process-level scan across known social workspaces."""

    def __init__(
        self,
        agent: EchoWeaveSocialAgent,
        config: OrphanRecoveryConfig,
    ) -> None:
        self.agent = agent
        self.config = config
        self._lock = threading.RLock()
        self._contexts: dict[str, SocialRecoveryContext] = {}

    def scan(self) -> OrphanScanReport:
        contexts = self.agent.recovery_contexts()
        grouped_contexts: dict[str, list[SocialRecoveryContext]] = {}
        for context in contexts:
            grouped_contexts.setdefault(str(context.session_path.resolve()), []).append(context)
        owned_sessions = {
            key: items[0]
            for key, items in grouped_contexts.items()
            if len(items) == 1
        }
        ambiguous_sessions = {
            key: items
            for key, items in grouped_contexts.items()
            if len(items) > 1
        }
        roots = sorted({context.session_path.parent.resolve() for context in contexts}, key=str)
        candidates: list[OrphanTurnCandidate] = []
        issues = [
            OrphanScanIssue(
                session_path=items[0].session_path,
                turn_id=None,
                error_type="AmbiguousRecoverySession",
                message=(
                    "session is registered to multiple social conversations: "
                    + ", ".join(item.conversation_key for item in items)
                ),
            )
            for items in ambiguous_sessions.values()
        ]
        scanned_sessions = 0

        for sessions_dir in roots:
            try:
                store = SessionStore(sessions_dir)
                coordinator = ExecutionLeaseCoordinator.for_store(store)
                report = OrphanTurnScanner(_ScanCore(store, coordinator), self.config).scan()
            except Exception as exc:
                issues.append(
                    OrphanScanIssue(
                        session_path=sessions_dir,
                        turn_id=None,
                        error_type=type(exc).__name__,
                        message=str(exc),
                    )
                )
                continue
            scanned_sessions += report.scanned_sessions
            issues.extend(report.issues)
            for candidate in report.candidates:
                candidate_key = str(candidate.session_path.resolve())
                if candidate_key in owned_sessions:
                    candidates.append(candidate)
                elif candidate_key not in ambiguous_sessions:
                    issues.append(
                        OrphanScanIssue(
                            session_path=candidate.session_path,
                            turn_id=candidate.turn_id,
                            error_type="UnmanagedRecoverySession",
                            message="orphan session is not owned by a current social conversation",
                        )
                    )

        candidates.sort(
            key=lambda candidate: (
                candidate.lease_expired_at,
                candidate.session_id,
                candidate.turn_id,
            )
        )
        with self._lock:
            self._contexts = owned_sessions
        return OrphanScanReport(scanned_sessions, tuple(candidates), tuple(issues))

    def context_for(self, session_path: Path) -> SocialRecoveryContext | None:
        key = str(session_path.expanduser().resolve())
        with self._lock:
            return self._contexts.get(key)


class SocialRecoveryController:
    """Lifecycle and management facade for social orphan recovery."""

    name = "social-orphan-recovery"

    def __init__(
        self,
        agent: EchoWeaveSocialAgent,
        config: OrphanRecoveryConfig,
    ) -> None:
        self.agent = agent
        self.config = config
        self.scanner = SocialOrphanScanner(agent, config)
        self.scheduler = OrphanRecoveryScheduler(
            None,
            config,
            core_factory=lambda candidate: self.agent.build_recovery_core(candidate.session_path),
            scanner=self.scanner,
        )

    def start(self) -> None:
        self.scheduler.start()

    def stop(self) -> None:
        self.scheduler.stop()

    def status(self) -> dict[str, Any]:
        snapshot = self.scheduler.snapshot()
        return {
            "running": snapshot.running,
            "config": {
                "scan_interval_seconds": self.config.scan_interval_seconds,
                "max_concurrent_recoveries": self.config.max_concurrent_recoveries,
                "max_recoveries_per_scan": self.config.max_recoveries_per_scan,
                "max_attempts_per_turn": self.config.max_attempts_per_turn,
            },
            "stats": {
                "scans": snapshot.scans,
                "scanned_sessions": snapshot.scanned_sessions,
                "candidates_found": snapshot.candidates_found,
                "scheduled": snapshot.scheduled,
                "completed": snapshot.completed,
                "failed": snapshot.failed,
                "contended": snapshot.contended,
                "scan_issues": snapshot.scan_issues,
                "in_flight": snapshot.in_flight,
                "last_scan_at": snapshot.last_scan_at,
                "last_scan_error": snapshot.last_scan_error,
            },
            "recent_results": [
                {
                    **self._candidate_payload(result.candidate),
                    "status": result.status,
                    "started_at": result.started_at,
                    "finished_at": result.finished_at,
                    "error_type": result.error_type,
                    "message": result.message,
                    "outcome_state": result.outcome.state.value if result.outcome else None,
                }
                for result in snapshot.recent_results
            ],
        }

    def scan_now(self, *, schedule: bool) -> dict[str, Any]:
        report = self.scanner.scan()
        if schedule and self.scheduler.snapshot().running:
            self.scheduler.trigger_scan()
        return {
            "scanned_sessions": report.scanned_sessions,
            "candidates": [self._candidate_payload(candidate) for candidate in report.candidates],
            "issues": [
                {
                    "session_path": str(issue.session_path),
                    "turn_id": issue.turn_id,
                    "error_type": issue.error_type,
                    "message": issue.message,
                }
                for issue in report.issues
            ],
            "scheduled_scan": bool(schedule and self.scheduler.snapshot().running),
        }

    def _candidate_payload(self, candidate: OrphanTurnCandidate) -> dict[str, Any]:
        context = self.scanner.context_for(candidate.session_path)
        return {
            "conversation_key": context.conversation_key if context else None,
            "workspace": str(context.workspace) if context else None,
            "session_path": str(candidate.session_path),
            "session_id": candidate.session_id,
            "turn_id": candidate.turn_id,
            "checkpoint_id": candidate.checkpoint_id,
            "latest_state": candidate.latest_state,
            "latest_attempt": candidate.latest_attempt,
            "previous_owner_id": candidate.previous_owner_id,
            "previous_fencing_token": candidate.previous_fencing_token,
            "lease_expired_at": candidate.lease_expired_at,
        }


__all__ = ["SocialOrphanScanner", "SocialRecoveryController"]
