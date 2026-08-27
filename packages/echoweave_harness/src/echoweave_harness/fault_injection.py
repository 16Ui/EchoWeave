from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from echoweave_runtime.execution_leases import (
    ExecutionLeaseConfig,
    ExecutionLeaseCoordinator,
    ExecutionLeaseLostError,
)
from echoweave_runtime.provider_reliability import (
    CircuitBreakerPolicy,
    ProviderCircuitOpenError,
    ProviderReliabilityConfig,
    ProviderReliabilityController,
    ProviderRetryPolicy,
)
from echoweave_runtime.session.store import SessionStore
from echoweave_runtime.tools.policy import PolicyVerdict, ShellCommandPolicy


@dataclass(frozen=True, slots=True)
class FaultScenarioResult:
    id: str
    title: str
    trace_id: str
    turn_id: str
    passed: bool
    score: float
    duration_ms: float
    expected: dict[str, Any]
    observed: dict[str, Any]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class FaultEvalReport:
    run_id: str
    generated_at: str
    workspace: str
    session_id: str
    session_path: str
    report_path: str
    scenario_count: int
    passed_count: int
    pass_rate: float
    overall_score: float
    passed: bool
    scenarios: tuple[FaultScenarioResult, ...]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["scenarios"] = [item.to_dict() for item in self.scenarios]
        return data


@dataclass(frozen=True, slots=True)
class _ProbeOutcome:
    passed: bool
    expected: dict[str, Any]
    observed: dict[str, Any]


class FaultInjectionEvalRunner:
    """Run deterministic reliability probes without network or shell side effects."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        output_root: str | Path | None = None,
    ) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.output_root = (
            Path(output_root).expanduser().resolve()
            if output_root is not None
            else self.workspace / "echoweave-data" / "demos"
        )

    def run(self) -> FaultEvalReport:
        run_id = _run_id()
        run_root = self.output_root / run_id
        demo_workspace = run_root / "workspace"
        store = SessionStore(demo_workspace / "echoweave-data" / "sessions")
        session_path = store.create()
        session_id = store.read_header(session_path).id
        report_path = run_root / "fault-eval-report.json"

        probes: tuple[tuple[str, str, Callable[..., _ProbeOutcome]], ...] = (
            (
                "provider-transient-retry",
                "Provider 瞬时故障经过受限重试后恢复",
                self._provider_retry_probe,
            ),
            (
                "provider-circuit-breaker",
                "Provider 连续故障触发熔断并拒绝新请求",
                self._provider_circuit_probe,
            ),
            (
                "sandbox-path-escape",
                "路径穿越命令在执行前被策略阻断",
                self._policy_escape_probe,
            ),
            (
                "lease-takeover-fencing",
                "过期 Lease 被接管且旧 Owner 被 fencing",
                self._lease_takeover_probe,
            ),
        )
        results = tuple(
            self._run_probe(
                store,
                session_path,
                run_id=run_id,
                scenario_id=scenario_id,
                title=title,
                probe=probe,
            )
            for scenario_id, title, probe in probes
        )
        passed_count = sum(1 for result in results if result.passed)
        score = sum(result.score for result in results) / len(results) if results else 0.0
        report = FaultEvalReport(
            run_id=run_id,
            generated_at=datetime.now(timezone.utc).isoformat(),
            workspace=str(demo_workspace),
            session_id=session_id,
            session_path=str(session_path),
            report_path=str(report_path),
            scenario_count=len(results),
            passed_count=passed_count,
            pass_rate=(passed_count / len(results)) if results else 0.0,
            overall_score=score,
            passed=passed_count == len(results),
            scenarios=results,
        )
        payload = report.to_dict()
        _atomic_write_json(report_path, payload)
        _atomic_write_json(self.output_root / "latest.json", payload)
        return report

    def _run_probe(
        self,
        store: SessionStore,
        session_path: Path,
        *,
        run_id: str,
        scenario_id: str,
        title: str,
        probe: Callable[..., _ProbeOutcome],
    ) -> FaultScenarioResult:
        turn_id = f"demo:{scenario_id}"
        trace_id = f"{run_id}:{scenario_id}"
        base = {"turn_id": turn_id, "trace_id": trace_id, "attempt": 1}
        store.append(
            session_path,
            "eval.case_started",
            {**base, "id": scenario_id, "title": title},
        )
        store.append(
            session_path,
            "turn.state_changed",
            {**base, "from": None, "state": "created", "sequence": 0},
        )
        store.append(
            session_path,
            "turn.state_changed",
            {**base, "from": "created", "state": "running", "sequence": 1},
        )
        started = time.perf_counter()
        error: str | None = None
        try:
            outcome = probe(store, session_path, turn_id=turn_id, trace_id=trace_id)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            outcome = _ProbeOutcome(
                passed=False,
                expected={"probe_completed_without_internal_error": True},
                observed={"probe_completed_without_internal_error": False},
            )
        duration_ms = (time.perf_counter() - started) * 1000
        final_state = "completed" if outcome.passed else "failed"
        store.append(
            session_path,
            "turn.state_changed",
            {
                **base,
                "from": "running",
                "state": final_state,
                "sequence": 2,
                "error": error,
            },
        )
        result = FaultScenarioResult(
            id=scenario_id,
            title=title,
            trace_id=trace_id,
            turn_id=turn_id,
            passed=outcome.passed,
            score=1.0 if outcome.passed else 0.0,
            duration_ms=duration_ms,
            expected=outcome.expected,
            observed=outcome.observed,
            error=error,
        )
        store.append(
            session_path,
            "eval.case_finished",
            {**base, **result.to_dict()},
        )
        return result

    @staticmethod
    def _provider_retry_probe(
        store: SessionStore,
        session_path: Path,
        *,
        turn_id: str,
        trace_id: str,
    ) -> _ProbeOutcome:
        emitted: list[str] = []
        attempts = 0
        controller = ProviderReliabilityController(
            ProviderReliabilityConfig(
                retry=ProviderRetryPolicy(
                    max_attempts=2,
                    max_retries_per_turn=1,
                    base_delay_seconds=0,
                    max_delay_seconds=0,
                    jitter_ratio=0,
                )
            ),
            sleeper=lambda _: None,
        )

        def operation() -> str:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise TimeoutError("injected transient provider timeout")
            return "recovered"

        def on_event(event_type: str, payload: dict[str, Any]) -> None:
            emitted.append(event_type)
            store.append(
                session_path,
                event_type,
                {"turn_id": turn_id, "trace_id": trace_id, **payload},
            )

        result = controller.call(
            operation,
            provider_key="demo-fault-provider",
            budget=controller.new_budget(),
            on_event=on_event,
        )
        observed = {"result": result, "attempts": attempts, "events": emitted}
        expected = {
            "result": "recovered",
            "attempts": 2,
            "required_event": "provider.retry_scheduled",
        }
        return _ProbeOutcome(
            passed=(
                result == "recovered"
                and attempts == 2
                and "provider.retry_scheduled" in emitted
            ),
            expected=expected,
            observed=observed,
        )

    @staticmethod
    def _provider_circuit_probe(
        store: SessionStore,
        session_path: Path,
        *,
        turn_id: str,
        trace_id: str,
    ) -> _ProbeOutcome:
        emitted: list[str] = []
        controller = ProviderReliabilityController(
            ProviderReliabilityConfig(
                retry=ProviderRetryPolicy(
                    max_attempts=1,
                    max_retries_per_turn=0,
                    base_delay_seconds=0,
                    max_delay_seconds=0,
                    jitter_ratio=0,
                ),
                circuit_breaker=CircuitBreakerPolicy(
                    failure_threshold=1,
                    recovery_timeout_seconds=60,
                ),
            ),
            sleeper=lambda _: None,
        )

        def on_event(event_type: str, payload: dict[str, Any]) -> None:
            emitted.append(event_type)
            store.append(
                session_path,
                event_type,
                {"turn_id": turn_id, "trace_id": trace_id, **payload},
            )

        def fail_persistently() -> str:
            raise TimeoutError("injected persistent timeout")

        first_failed = False
        try:
            controller.call(
                fail_persistently,
                provider_key="demo-circuit-provider",
                budget=controller.new_budget(),
                on_event=on_event,
            )
        except TimeoutError:
            first_failed = True
        second_rejected = False
        try:
            controller.call(
                lambda: "must-not-run",
                provider_key="demo-circuit-provider",
                budget=controller.new_budget(),
                on_event=on_event,
            )
        except ProviderCircuitOpenError:
            second_rejected = True
        diagnostics = controller.diagnostics("demo-circuit-provider")
        observed = {
            "first_failed": first_failed,
            "second_rejected": second_rejected,
            "circuit_state": diagnostics["state"],
            "events": emitted,
        }
        expected = {
            "first_failed": True,
            "second_rejected": True,
            "circuit_state": "open",
            "required_events": ["provider.circuit_opened", "provider.circuit_rejected"],
        }
        return _ProbeOutcome(
            passed=(
                first_failed
                and second_rejected
                and diagnostics["state"] == "open"
                and "provider.circuit_opened" in emitted
                and "provider.circuit_rejected" in emitted
            ),
            expected=expected,
            observed=observed,
        )

    @staticmethod
    def _policy_escape_probe(
        store: SessionStore,
        session_path: Path,
        *,
        turn_id: str,
        trace_id: str,
    ) -> _ProbeOutcome:
        command = "cat ../outside-secrets.txt"
        decision = ShellCommandPolicy(auto_approve=True).check(command)
        store.append(
            session_path,
            "policy.command_checked",
            {
                "turn_id": turn_id,
                "trace_id": trace_id,
                "command": command,
                "decision": decision.verdict.value,
                "reason": decision.reason,
                "reason_code": decision.reason_code,
                "risk_level": decision.risk_level,
            },
        )
        observed = {
            "decision": decision.verdict.value,
            "reason_code": decision.reason_code,
            "risk_level": decision.risk_level,
        }
        expected = {"decision": "deny", "reason_code": "deny.path_traversal"}
        return _ProbeOutcome(
            passed=(
                decision.verdict is PolicyVerdict.DENY
                and decision.reason_code == "deny.path_traversal"
            ),
            expected=expected,
            observed=observed,
        )

    @staticmethod
    def _lease_takeover_probe(
        store: SessionStore,
        session_path: Path,
        *,
        turn_id: str,
        trace_id: str,
    ) -> _ProbeOutcome:
        now = [100.0]
        config = ExecutionLeaseConfig(
            ttl_seconds=1.0,
            heartbeat_interval_seconds=0.1,
            lock_timeout_seconds=2.0,
            background_heartbeat=False,
        )
        old = ExecutionLeaseCoordinator(
            store,
            config,
            owner_id="demo-old-owner",
            clock=lambda: now[0],
        )
        old_lease = old.acquire(session_path, turn_id=turn_id, trace_id=trace_id)
        now[0] = 102.0
        replacement = ExecutionLeaseCoordinator(
            store,
            config,
            owner_id="demo-replacement-owner",
            clock=lambda: now[0],
        )
        replacement_lease = replacement.acquire(
            session_path,
            turn_id=turn_id,
            trace_id=trace_id,
        )
        stale_owner_fenced = False
        try:
            old.assert_owned(old_lease)
        except ExecutionLeaseLostError:
            stale_owner_fenced = True
        replacement.release(
            session_path,
            replacement_lease,
            trace_id=trace_id,
            reason="fault-eval-complete",
        )
        observed = {
            "old_fencing_token": old_lease.fencing_token,
            "replacement_fencing_token": replacement_lease.fencing_token,
            "previous_owner_id": old_lease.owner_id,
            "replacement_owner_id": replacement_lease.owner_id,
            "stale_owner_fenced": stale_owner_fenced,
        }
        expected = {
            "replacement_token_greater_than_old": True,
            "stale_owner_fenced": True,
        }
        return _ProbeOutcome(
            passed=(
                replacement_lease.fencing_token > old_lease.fencing_token
                and stale_owner_fenced
            ),
            expected=expected,
            observed=observed,
        )


def load_latest_fault_eval(output_root: str | Path) -> dict[str, Any] | None:
    path = Path(output_root).expanduser().resolve() / "latest.json"
    if not path.exists() or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"reliability-{stamp}-{uuid4().hex[:8]}"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


__all__ = [
    "FaultEvalReport",
    "FaultInjectionEvalRunner",
    "FaultScenarioResult",
    "load_latest_fault_eval",
]
