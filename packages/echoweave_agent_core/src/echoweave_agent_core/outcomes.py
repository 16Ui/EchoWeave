from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from echoweave_agent_core.types import TurnResult
from echoweave_runtime.concurrency import FileLockTimeoutError
from echoweave_runtime.execution_leases import (
    ExecutionLeaseCorruptError,
    ExecutionLeaseLostError,
    ExecutionLeaseUnavailableError,
)
from echoweave_runtime.provider_reliability import (
    ProviderCircuitOpenError,
    is_retryable_provider_error,
)
from echoweave_runtime.tool_invocations import ToolInvocationBlockedError


class TurnState(str, Enum):
    """A recoverable turn's durable lifecycle state."""

    CREATED = "created"
    RUNNING = "running"
    WAITING_FOR_TOOL = "waiting_for_tool"
    SUSPENDED = "suspended"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"

    @property
    def terminal(self) -> bool:
        return self in {
            TurnState.COMPLETED,
            TurnState.FAILED,
            TurnState.CANCELLED,
            TurnState.TIMED_OUT,
        }


_ALLOWED_TRANSITIONS: dict[TurnState, frozenset[TurnState]] = {
    TurnState.CREATED: frozenset(
        {TurnState.RUNNING, TurnState.FAILED, TurnState.CANCELLED, TurnState.TIMED_OUT}
    ),
    TurnState.RUNNING: frozenset(
        {
            TurnState.WAITING_FOR_TOOL,
            TurnState.SUSPENDED,
            TurnState.COMPLETED,
            TurnState.FAILED,
            TurnState.CANCELLED,
            TurnState.TIMED_OUT,
        }
    ),
    TurnState.WAITING_FOR_TOOL: frozenset(
        {
            TurnState.RUNNING,
            TurnState.SUSPENDED,
            TurnState.FAILED,
            TurnState.CANCELLED,
            TurnState.TIMED_OUT,
        }
    ),
    TurnState.SUSPENDED: frozenset(
        {TurnState.RUNNING, TurnState.FAILED, TurnState.CANCELLED, TurnState.TIMED_OUT}
    ),
    TurnState.COMPLETED: frozenset(),
    TurnState.FAILED: frozenset(),
    TurnState.CANCELLED: frozenset(),
    TurnState.TIMED_OUT: frozenset(),
}


class InvalidTurnTransition(ValueError):
    """Raised when code attempts an impossible lifecycle transition."""


@dataclass
class TurnStateMachine:
    state: TurnState = TurnState.CREATED

    def transition(self, target: TurnState) -> tuple[TurnState, TurnState]:
        previous = self.state
        if target not in _ALLOWED_TRANSITIONS[previous]:
            raise InvalidTurnTransition(f"invalid turn transition: {previous.value} -> {target.value}")
        self.state = target
        return previous, target


class TurnFailureKind(str, Enum):
    SESSION = "session"
    PERSISTENCE = "persistence"
    CHECKPOINT = "checkpoint"
    CONCURRENCY = "concurrency"
    HOOK = "hook"
    PROVIDER = "provider"
    RUNTIME = "runtime"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    INDETERMINATE_TOOL = "indeterminate_tool"
    INTERNAL = "internal"


@dataclass(frozen=True)
class TurnFailure:
    kind: TurnFailureKind
    stage: str
    error_type: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)
    _cause: BaseException | None = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "stage": self.stage,
            "error_type": self.error_type,
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details,
        }


class TurnExecutionError(RuntimeError):
    def __init__(self, failure: TurnFailure) -> None:
        super().__init__(f"{failure.kind.value} failure during {failure.stage}: {failure.message}")
        self.failure = failure


@dataclass(frozen=True)
class TurnOutcome:
    turn_id: str
    trace_id: str
    state: TurnState
    started_at: str
    finished_at: str
    latency_ms: float
    session_path: Path | None = None
    session_id: str | None = None
    checkpoint: dict[str, Any] | None = None
    result: TurnResult | None = None
    failure: TurnFailure | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.state is TurnState.COMPLETED and self.result is not None

    def require_result(self) -> TurnResult:
        if self.result is not None:
            return self.result
        if self.failure is not None and self.failure._cause is not None:
            raise self.failure._cause
        if self.failure is not None:
            raise TurnExecutionError(self.failure)
        raise TurnExecutionError(
            TurnFailure(
                kind=TurnFailureKind.INTERNAL,
                stage="outcome",
                error_type="MissingTurnResult",
                message="turn ended without a result or failure",
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "trace_id": self.trace_id,
            "state": self.state.value,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "latency_ms": self.latency_ms,
            "session_path": str(self.session_path) if self.session_path else None,
            "session_id": self.session_id,
            "checkpoint": self.checkpoint,
            "result": {
                "text": self.result.text,
                "session_path": str(self.result.session_path),
                "session_id": self.result.session_id,
                "history": self.result.history,
                "summary": self.result.summary,
                "metadata": self.result.metadata,
            }
            if self.result
            else None,
            "failure": self.failure.to_dict() if self.failure else None,
            "metadata": self.metadata,
        }


def classify_turn_failure(error: BaseException, stage: str) -> TurnFailure:
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        kind = TurnFailureKind.TIMEOUT
        retryable = True
    elif isinstance(error, asyncio.CancelledError):
        kind = TurnFailureKind.CANCELLED
        retryable = True
    elif isinstance(error, ToolInvocationBlockedError):
        kind = TurnFailureKind.INDETERMINATE_TOOL
        retryable = False
    elif isinstance(
        error,
        (
            ExecutionLeaseUnavailableError,
            ExecutionLeaseLostError,
            ExecutionLeaseCorruptError,
            FileLockTimeoutError,
        ),
    ):
        kind = TurnFailureKind.CONCURRENCY
        retryable = True
    elif stage == "session":
        kind = TurnFailureKind.SESSION
        retryable = False
    elif stage == "checkpoint":
        kind = TurnFailureKind.CHECKPOINT
        retryable = True
    elif stage == "state_persist":
        kind = TurnFailureKind.PERSISTENCE
        retryable = True
    elif stage in {"before_hook", "after_hook", "error_hook"}:
        kind = TurnFailureKind.HOOK
        retryable = False
    elif isinstance(error, ProviderCircuitOpenError):
        kind = TurnFailureKind.PROVIDER
        retryable = True
    elif stage == "runtime":
        retryable = isinstance(error, Exception) and is_retryable_provider_error(error)
        kind = TurnFailureKind.PROVIDER if retryable else TurnFailureKind.RUNTIME
    else:
        kind = TurnFailureKind.INTERNAL
        retryable = False
    details = {}
    if isinstance(error, ProviderCircuitOpenError):
        details = {
            "provider_key": error.provider_key,
            "retry_after_seconds": error.retry_after_seconds,
        }
    elif isinstance(error, ExecutionLeaseUnavailableError):
        details = {
            "owner_id": error.current.owner_id,
            "fencing_token": error.current.fencing_token,
            "retry_after_seconds": error.retry_after_seconds,
        }
    elif isinstance(error, ExecutionLeaseLostError):
        details = {
            "expected_owner_id": error.lease.owner_id,
            "expected_fencing_token": error.lease.fencing_token,
            "current_owner_id": error.current.owner_id if error.current else None,
            "current_fencing_token": error.current.fencing_token if error.current else None,
        }
    return TurnFailure(
        kind=kind,
        stage=stage,
        error_type=type(error).__name__,
        message=str(error),
        retryable=retryable,
        details=details,
        _cause=error,
    )
