from __future__ import annotations

import shutil
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest

from echoweave_agent_core import TurnFailureKind
from echoweave_agent_core.outcomes import classify_turn_failure
from echoweave_runtime.app import build_runtime
from echoweave_runtime.models.base import ModelClient
from echoweave_runtime.provider_reliability import (
    CircuitBreakerPolicy,
    CircuitState,
    ProviderCircuitOpenError,
    ProviderReliabilityConfig,
    ProviderReliabilityController,
    ProviderRetryPolicy,
)
from echoweave_runtime.runtime.observer import summarize_runtime_events
from echoweave_runtime.session.store import SessionStore
from echoweave_runtime.tools_base import ToolRegistry
from echoweave_runtime.types import AgentResponse


@contextmanager
def _local_tmp():
    path = Path.cwd() / ".test-data" / uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


class _HttpError(RuntimeError):
    def __init__(self, status_code: int, *, headers: dict[str, str] | None = None) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code
        self.headers = headers or {}


def _config(
    *,
    max_attempts: int = 3,
    turn_budget: int = 4,
    failure_threshold: int = 3,
    recovery_timeout: float = 30.0,
) -> ProviderReliabilityConfig:
    return ProviderReliabilityConfig(
        retry=ProviderRetryPolicy(
            max_attempts=max_attempts,
            max_retries_per_turn=turn_budget,
            base_delay_seconds=1.0,
            max_delay_seconds=8.0,
            max_retry_after_seconds=10.0,
            jitter_ratio=0.0,
        ),
        circuit_breaker=CircuitBreakerPolicy(
            failure_threshold=failure_threshold,
            recovery_timeout_seconds=recovery_timeout,
        ),
    )


def test_retry_uses_exponential_backoff_and_preserves_one_request_scope() -> None:
    delays: list[float] = []
    events: list[tuple[str, dict[str, object]]] = []
    controller = ProviderReliabilityController(_config(), sleeper=delays.append)
    attempts = 0

    def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise TimeoutError("temporary timeout")
        return "ok"

    budget = controller.new_budget()
    result = controller.call(
        operation,
        provider_key="demo",
        budget=budget,
        on_event=lambda event, payload: events.append((event, payload)),
    )

    assert result == "ok"
    assert attempts == 3
    assert delays == [1.0, 2.0]
    assert budget.consumed == 2
    assert [event for event, _ in events] == [
        "provider.retry_scheduled",
        "provider.retry_scheduled",
    ]
    assert controller.diagnostics("demo")["state"] == CircuitState.CLOSED.value


def test_turn_retry_budget_is_shared_across_provider_requests() -> None:
    controller = ProviderReliabilityController(_config(turn_budget=1), sleeper=lambda _: None)
    budget = controller.new_budget()
    first_attempts = 0
    second_attempts = 0
    events: list[tuple[str, dict[str, object]]] = []

    def first_operation() -> str:
        nonlocal first_attempts
        first_attempts += 1
        if first_attempts == 1:
            raise TimeoutError("first request")
        return "ok"

    def second_operation() -> str:
        nonlocal second_attempts
        second_attempts += 1
        raise TimeoutError("second request")

    assert controller.call(first_operation, provider_key="demo", budget=budget) == "ok"
    with pytest.raises(TimeoutError):
        controller.call(
            second_operation,
            provider_key="demo",
            budget=budget,
            on_event=lambda event, payload: events.append((event, payload)),
        )

    assert first_attempts == 2
    assert second_attempts == 1
    assert events[-1][0] == "provider.retry_exhausted"
    assert events[-1][1]["exhausted_by"] == "turn_retry_budget"


def test_retry_after_header_overrides_backoff_and_is_capped() -> None:
    delays: list[float] = []
    controller = ProviderReliabilityController(_config(), sleeper=delays.append)
    attempts = 0

    def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise _HttpError(429, headers={"Retry-After": "15"})
        return "ok"

    assert controller.call(operation, provider_key="demo", budget=controller.new_budget()) == "ok"
    assert delays == [10.0]


def test_non_retryable_client_error_does_not_retry_or_trip_circuit() -> None:
    controller = ProviderReliabilityController(
        _config(max_attempts=3, failure_threshold=2),
        sleeper=lambda _: None,
    )
    attempts = 0
    events: list[str] = []

    def operation() -> str:
        nonlocal attempts
        attempts += 1
        raise _HttpError(401)

    for _ in range(3):
        with pytest.raises(_HttpError):
            controller.call(
                operation,
                provider_key="demo",
                budget=controller.new_budget(),
                on_event=lambda event, _: events.append(event),
            )

    assert attempts == 3
    assert events == []
    assert controller.diagnostics("demo")["consecutive_failures"] == 0


def test_stream_retries_only_before_the_first_emitted_item() -> None:
    controller = ProviderReliabilityController(_config(), sleeper=lambda _: None)
    attempts = 0

    def operation():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TimeoutError("failed before output")
        return iter(["first", "second"])

    assert list(
        controller.stream(operation, provider_key="demo", budget=controller.new_budget())
    ) == ["first", "second"]
    assert attempts == 2


def test_stream_does_not_replay_after_partial_output() -> None:
    controller = ProviderReliabilityController(_config(), sleeper=lambda _: None)
    attempts = 0
    events: list[tuple[str, dict[str, object]]] = []

    def operation():
        nonlocal attempts
        attempts += 1

        def values():
            yield "partial"
            raise TimeoutError("stream disconnected")

        return values()

    stream = controller.stream(
        operation,
        provider_key="demo",
        budget=controller.new_budget(),
        on_event=lambda event, payload: events.append((event, payload)),
    )
    assert next(stream) == "partial"
    with pytest.raises(TimeoutError):
        next(stream)

    assert attempts == 1
    assert events[0][0] == "provider.stream_interrupted"
    assert events[0][1]["partial_output"] is True


def test_circuit_opens_rejects_calls_and_recovers_through_half_open() -> None:
    now = [100.0]
    controller = ProviderReliabilityController(
        _config(max_attempts=1, turn_budget=0, failure_threshold=2, recovery_timeout=10.0),
        clock=lambda: now[0],
    )
    operation_calls = 0
    events: list[str] = []

    def failing_operation() -> str:
        nonlocal operation_calls
        operation_calls += 1
        raise TimeoutError("provider unavailable")

    for _ in range(2):
        with pytest.raises(TimeoutError):
            controller.call(
                failing_operation,
                provider_key="demo",
                budget=controller.new_budget(),
                on_event=lambda event, _: events.append(event),
            )

    assert controller.diagnostics("demo")["state"] == CircuitState.OPEN.value
    with pytest.raises(ProviderCircuitOpenError):
        controller.call(
            failing_operation,
            provider_key="demo",
            budget=controller.new_budget(),
            on_event=lambda event, _: events.append(event),
        )
    assert operation_calls == 2

    now[0] += 10.0
    assert controller.call(
        lambda: "recovered",
        provider_key="demo",
        budget=controller.new_budget(),
        on_event=lambda event, _: events.append(event),
    ) == "recovered"
    assert controller.diagnostics("demo")["state"] == CircuitState.CLOSED.value
    assert "provider.circuit_opened" in events
    assert "provider.circuit_rejected" in events
    assert "provider.circuit_half_open" in events
    assert "provider.circuit_closed" in events


class _FlakyModel(ModelClient):
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, messages, tools, options=None) -> AgentResponse:
        self.calls += 1
        if self.calls == 1:
            raise TimeoutError("model timed out once")
        return AgentResponse(text="recovered")


def test_runtime_retries_model_request_inside_the_same_turn_and_persists_telemetry() -> None:
    with _local_tmp() as tmp_path:
        model = _FlakyModel()
        store = SessionStore(tmp_path / "sessions")
        session_path = store.create()
        runtime = build_runtime(
            model,
            ToolRegistry(),
            store,
            provider_reliability_config=ProviderReliabilityConfig(
                retry=ProviderRetryPolicy(
                    max_attempts=2,
                    max_retries_per_turn=1,
                    base_delay_seconds=0.0,
                    max_delay_seconds=0.0,
                    jitter_ratio=0.0,
                )
            ),
        )

        reply, _, _ = runtime.run_turn(
            session_path,
            [],
            "hello",
            turn_id="one-turn",
            trace_id="one-trace",
        )

        events = store.read_events(session_path)
        retry_events = [event for event in events if event.type == "provider.retry_scheduled"]
        assert reply == "recovered"
        assert model.calls == 2
        assert len(retry_events) == 1
        assert retry_events[0].payload["turn_id"] == "one-turn"
        assert retry_events[0].payload["trace_id"] == "one-trace"
        assert store.build_task_graph(events)["stats"]["provider_retry_count"] == 1


def test_provider_failures_are_classified_as_recoverable_turn_failures() -> None:
    provider_failure = classify_turn_failure(_HttpError(503), "runtime")
    circuit_failure = classify_turn_failure(ProviderCircuitOpenError("demo", 4.0), "runtime")

    assert provider_failure.kind is TurnFailureKind.PROVIDER
    assert provider_failure.retryable is True
    assert circuit_failure.kind is TurnFailureKind.PROVIDER
    assert circuit_failure.details == {"provider_key": "demo", "retry_after_seconds": 4.0}


def test_runtime_summary_counts_provider_retry_events() -> None:
    summary = summarize_runtime_events(
        [
            {"event": "provider.retry_scheduled", "payload": {"provider": {"attempt": 1}}},
            {"event": "provider.retry_scheduled", "payload": {"provider": {"attempt": 2}}},
            {"event": "turn_end", "payload": {"turn": {"reply": "you can retry later"}}},
        ]
    )

    assert summary["retry_count"] == 2
