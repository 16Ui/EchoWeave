from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from enum import Enum
from typing import Any, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class ProviderRetryPolicy:
    max_attempts: int = 3
    max_retries_per_turn: int = 4
    base_delay_seconds: float = 0.25
    max_delay_seconds: float = 4.0
    max_retry_after_seconds: float = 30.0
    jitter_ratio: float = 0.1

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.max_retries_per_turn < 0:
            raise ValueError("max_retries_per_turn must be non-negative")
        if self.base_delay_seconds < 0 or self.max_delay_seconds < 0:
            raise ValueError("retry delays must be non-negative")
        if self.max_retry_after_seconds < 0:
            raise ValueError("max_retry_after_seconds must be non-negative")
        if not 0 <= self.jitter_ratio <= 1:
            raise ValueError("jitter_ratio must be between 0 and 1")


@dataclass(frozen=True)
class CircuitBreakerPolicy:
    failure_threshold: int = 3
    recovery_timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1")
        if self.recovery_timeout_seconds < 0:
            raise ValueError("recovery_timeout_seconds must be non-negative")


@dataclass(frozen=True)
class ProviderReliabilityConfig:
    enabled: bool = True
    retry: ProviderRetryPolicy = field(default_factory=ProviderRetryPolicy)
    circuit_breaker: CircuitBreakerPolicy = field(default_factory=CircuitBreakerPolicy)


@dataclass
class ProviderRetryBudget:
    remaining: int
    consumed: int = 0

    @classmethod
    def from_policy(cls, policy: ProviderRetryPolicy) -> "ProviderRetryBudget":
        return cls(remaining=policy.max_retries_per_turn)

    def consume(self) -> bool:
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        self.consumed += 1
        return True


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class ProviderCircuitOpenError(RuntimeError):
    def __init__(self, provider_key: str, retry_after_seconds: float) -> None:
        super().__init__(
            f"provider circuit is open for {provider_key}; retry after {retry_after_seconds:.3f}s"
        )
        self.provider_key = provider_key
        self.retry_after_seconds = retry_after_seconds


@dataclass
class _CircuitRecord:
    state: CircuitState = CircuitState.CLOSED
    consecutive_failures: int = 0
    opened_at: float | None = None
    half_open_probe_active: bool = False


ProviderEventSink = Callable[[str, dict[str, Any]], None]


class ProviderReliabilityController:
    """Retries one provider request at a time and shares circuit state across turns."""

    def __init__(
        self,
        config: ProviderReliabilityConfig | None = None,
        *,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        random_source: Callable[[], float] = random.random,
    ) -> None:
        self.config = config or ProviderReliabilityConfig()
        self._sleeper = sleeper
        self._clock = clock
        self._random = random_source
        self._circuits: dict[str, _CircuitRecord] = {}
        self._lock = threading.RLock()

    def new_budget(self) -> ProviderRetryBudget:
        return ProviderRetryBudget.from_policy(self.config.retry)

    def call(
        self,
        operation: Callable[[], T],
        *,
        provider_key: str,
        budget: ProviderRetryBudget,
        on_event: ProviderEventSink | None = None,
    ) -> T:
        if not self.config.enabled:
            return operation()
        self._before_call(provider_key, on_event)
        try:
            result = self._attempt_loop(
                operation,
                provider_key=provider_key,
                budget=budget,
                on_event=on_event,
            )
        except Exception as exc:
            self._record_failure(provider_key, exc, on_event)
            raise
        self._record_success(provider_key, on_event)
        return result

    def stream(
        self,
        operation: Callable[[], Iterable[T]],
        *,
        provider_key: str,
        budget: ProviderRetryBudget,
        on_event: ProviderEventSink | None = None,
    ) -> Iterator[T]:
        if not self.config.enabled:
            yield from operation()
            return
        self._before_call(provider_key, on_event)
        emitted = False
        attempts = 0
        try:
            while True:
                attempts += 1
                try:
                    for item in operation():
                        emitted = True
                        yield item
                    self._record_success(provider_key, on_event)
                    return
                except Exception as exc:
                    if emitted:
                        self._emit(
                            on_event,
                            "provider.stream_interrupted",
                            {
                                "provider_key": provider_key,
                                "attempt": attempts,
                                "error_type": type(exc).__name__,
                                "reason": str(exc),
                                "retryable": False,
                                "partial_output": True,
                            },
                        )
                        raise
                    if not self._schedule_retry(
                        exc,
                        attempts=attempts,
                        provider_key=provider_key,
                        budget=budget,
                        on_event=on_event,
                    ):
                        raise
        except Exception as exc:
            self._record_failure(provider_key, exc, on_event)
            raise

    def diagnostics(self, provider_key: str) -> dict[str, Any]:
        with self._lock:
            record = self._circuits.get(provider_key, _CircuitRecord())
            retry_after = self._circuit_retry_after(record)
            return {
                "provider_key": provider_key,
                "state": record.state.value,
                "consecutive_failures": record.consecutive_failures,
                "retry_after_seconds": retry_after,
                "half_open_probe_active": record.half_open_probe_active,
            }

    def _attempt_loop(
        self,
        operation: Callable[[], T],
        *,
        provider_key: str,
        budget: ProviderRetryBudget,
        on_event: ProviderEventSink | None,
    ) -> T:
        attempts = 0
        while True:
            attempts += 1
            try:
                return operation()
            except Exception as exc:
                if not self._schedule_retry(
                    exc,
                    attempts=attempts,
                    provider_key=provider_key,
                    budget=budget,
                    on_event=on_event,
                ):
                    raise

    def _schedule_retry(
        self,
        error: Exception,
        *,
        attempts: int,
        provider_key: str,
        budget: ProviderRetryBudget,
        on_event: ProviderEventSink | None,
    ) -> bool:
        retryable = is_retryable_provider_error(error)
        attempts_remaining = attempts < self.config.retry.max_attempts
        if not retryable:
            return False
        if not attempts_remaining or budget.remaining <= 0:
            self._emit(
                on_event,
                "provider.retry_exhausted",
                {
                    "provider_key": provider_key,
                    "attempt": attempts,
                    "error_type": type(error).__name__,
                    "reason": str(error),
                    "retryable": True,
                    "exhausted_by": "request_attempt_limit"
                    if not attempts_remaining
                    else "turn_retry_budget",
                    "turn_budget_remaining": budget.remaining,
                    "request_attempts_remaining": max(0, self.config.retry.max_attempts - attempts),
                },
            )
            return False
        if not budget.consume():
            return False
        delay, source = self._retry_delay(error, attempts)
        self._emit(
            on_event,
            "provider.retry_scheduled",
            {
                "provider_key": provider_key,
                "attempt": attempts,
                "next_attempt": attempts + 1,
                "delay_seconds": delay,
                "delay_source": source,
                "error_type": type(error).__name__,
                "reason": str(error),
                "turn_budget_remaining": budget.remaining,
            },
        )
        if delay > 0:
            self._sleeper(delay)
        return True

    def _retry_delay(self, error: Exception, attempts: int) -> tuple[float, str]:
        retry_after = retry_after_seconds(error)
        if retry_after is not None:
            return min(retry_after, self.config.retry.max_retry_after_seconds), "retry_after"
        base = min(
            self.config.retry.base_delay_seconds * (2 ** max(0, attempts - 1)),
            self.config.retry.max_delay_seconds,
        )
        jitter = base * self.config.retry.jitter_ratio * ((self._random() * 2) - 1)
        return max(0.0, min(base + jitter, self.config.retry.max_delay_seconds)), "exponential_backoff"

    def _before_call(self, provider_key: str, on_event: ProviderEventSink | None) -> None:
        with self._lock:
            record = self._circuits.setdefault(provider_key, _CircuitRecord())
            if record.state is CircuitState.OPEN:
                retry_after = self._circuit_retry_after(record)
                if retry_after > 0:
                    self._emit(
                        on_event,
                        "provider.circuit_rejected",
                        {"provider_key": provider_key, "retry_after_seconds": retry_after},
                    )
                    raise ProviderCircuitOpenError(provider_key, retry_after)
                record.state = CircuitState.HALF_OPEN
                record.half_open_probe_active = False
            if record.state is CircuitState.HALF_OPEN:
                if record.half_open_probe_active:
                    self._emit(
                        on_event,
                        "provider.circuit_rejected",
                        {
                            "provider_key": provider_key,
                            "retry_after_seconds": self.config.circuit_breaker.recovery_timeout_seconds,
                            "reason": "half_open_probe_active",
                        },
                    )
                    raise ProviderCircuitOpenError(
                        provider_key,
                        self.config.circuit_breaker.recovery_timeout_seconds,
                    )
                record.half_open_probe_active = True
                self._emit(on_event, "provider.circuit_half_open", {"provider_key": provider_key})

    def _record_success(self, provider_key: str, on_event: ProviderEventSink | None) -> None:
        with self._lock:
            record = self._circuits.setdefault(provider_key, _CircuitRecord())
            was_not_closed = record.state is not CircuitState.CLOSED
            record.state = CircuitState.CLOSED
            record.consecutive_failures = 0
            record.opened_at = None
            record.half_open_probe_active = False
            if was_not_closed:
                self._emit(on_event, "provider.circuit_closed", {"provider_key": provider_key})

    def _record_failure(
        self,
        provider_key: str,
        error: Exception,
        on_event: ProviderEventSink | None,
    ) -> None:
        with self._lock:
            record = self._circuits.setdefault(provider_key, _CircuitRecord())
            record.half_open_probe_active = False
            if not is_retryable_provider_error(error):
                if record.state is CircuitState.HALF_OPEN:
                    record.state = CircuitState.CLOSED
                    record.consecutive_failures = 0
                    record.opened_at = None
                    self._emit(on_event, "provider.circuit_closed", {"provider_key": provider_key})
                return
            record.consecutive_failures += 1
            if (
                record.state is CircuitState.HALF_OPEN
                or record.consecutive_failures >= self.config.circuit_breaker.failure_threshold
            ):
                record.state = CircuitState.OPEN
                record.opened_at = self._clock()
                self._emit(
                    on_event,
                    "provider.circuit_opened",
                    {
                        "provider_key": provider_key,
                        "consecutive_failures": record.consecutive_failures,
                        "recovery_timeout_seconds": self.config.circuit_breaker.recovery_timeout_seconds,
                    },
                )

    def _circuit_retry_after(self, record: _CircuitRecord) -> float:
        if record.state is not CircuitState.OPEN or record.opened_at is None:
            return 0.0
        elapsed = max(0.0, self._clock() - record.opened_at)
        return max(0.0, self.config.circuit_breaker.recovery_timeout_seconds - elapsed)

    @staticmethod
    def _emit(on_event: ProviderEventSink | None, event_type: str, payload: dict[str, Any]) -> None:
        if on_event is not None:
            on_event(event_type, payload)


def is_retryable_provider_error(error: Exception) -> bool:
    if isinstance(error, (TimeoutError, ConnectionError, OSError)):
        return True
    status = _status_code(error)
    return status in {408, 429} or (status is not None and 500 <= status <= 599)


def retry_after_seconds(error: Exception, *, now: datetime | None = None) -> float | None:
    headers = _headers(error)
    raw_value = headers.get("retry-after") if headers else None
    if raw_value is None:
        direct = getattr(error, "retry_after", None)
        raw_value = direct if direct is not None else getattr(error, "retry_after_seconds", None)
    if raw_value is None:
        return None
    try:
        return max(0.0, float(raw_value))
    except (TypeError, ValueError):
        pass
    try:
        target = parsedate_to_datetime(str(raw_value))
    except (TypeError, ValueError, OverflowError):
        return None
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return max(0.0, (target - current).total_seconds())


def _status_code(error: Exception) -> int | None:
    candidates = [getattr(error, "status_code", None), getattr(error, "status", None)]
    response = getattr(error, "response", None)
    if response is not None:
        candidates.extend([getattr(response, "status_code", None), getattr(response, "status", None)])
    for value in candidates:
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _headers(error: Exception) -> dict[str, str]:
    sources = [getattr(error, "headers", None)]
    response = getattr(error, "response", None)
    if response is not None:
        sources.append(getattr(response, "headers", None))
    for source in sources:
        if source is None:
            continue
        try:
            return {str(key).lower(): str(value) for key, value in source.items()}
        except (AttributeError, TypeError):
            continue
    return {}
