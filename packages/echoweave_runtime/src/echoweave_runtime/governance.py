from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol


@dataclass(frozen=True)
class RuntimePolicyDecision:
    decision: str
    reason: str = ""
    reason_code: str = ""
    matched_rules: tuple[str, ...] = ()

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"


class RuntimePolicyEvaluator(Protocol):
    def evaluate_tool(self, tool_name: str, arguments: dict[str, Any] | None = None) -> RuntimePolicyDecision:
        ...

    def evaluate_path(self, raw_path: str) -> RuntimePolicyDecision:
        ...

    def evaluate_command(self, command: str) -> RuntimePolicyDecision:
        ...


class AllowAllRuntimePolicyEvaluator:
    def __init__(self, *, workspace: Path | None = None) -> None:
        self.workspace = workspace

    def evaluate_tool(self, tool_name: str, arguments: dict[str, Any] | None = None) -> RuntimePolicyDecision:
        return RuntimePolicyDecision("allow", reason_code="runtime.tool.allowed")

    def evaluate_path(self, raw_path: str) -> RuntimePolicyDecision:
        return RuntimePolicyDecision("allow", reason_code="runtime.path.allowed")

    def evaluate_command(self, command: str) -> RuntimePolicyDecision:
        return RuntimePolicyDecision("allow", reason_code="runtime.command.allowed")


AuditRecorder = Callable[..., None]
PolicyEvaluatorFactory = Callable[[Path | None], RuntimePolicyEvaluator]


def _noop_audit_recorder(*args: Any, **kwargs: Any) -> None:
    return


_audit_recorder: AuditRecorder = _noop_audit_recorder
_policy_evaluator_factory: PolicyEvaluatorFactory = lambda workspace=None: AllowAllRuntimePolicyEvaluator(
    workspace=workspace
)


def configure_runtime_audit_recorder(recorder: AuditRecorder | None) -> None:
    global _audit_recorder
    _audit_recorder = recorder or _noop_audit_recorder


def configure_runtime_policy_evaluator(factory: PolicyEvaluatorFactory | None) -> None:
    global _policy_evaluator_factory
    _policy_evaluator_factory = factory or (
        lambda workspace=None: AllowAllRuntimePolicyEvaluator(workspace=workspace)
    )


def record_runtime_audit(
    category: str,
    action: str,
    *,
    status: str = "ok",
    subject: str | None = None,
    trace_id: str | None = None,
    conversation_id: str | None = None,
    session_id: str | None = None,
    actor_id: str | None = None,
    workspace: str | Path | None = None,
    latency_ms: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    _audit_recorder(
        category,
        action,
        status=status,
        subject=subject,
        trace_id=trace_id,
        conversation_id=conversation_id,
        session_id=session_id,
        actor_id=actor_id,
        workspace=workspace,
        latency_ms=latency_ms,
        metadata=metadata,
    )


def evaluate_runtime_tool(
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    *,
    workspace: Path | None = None,
) -> RuntimePolicyDecision:
    return _policy_evaluator_factory(workspace).evaluate_tool(tool_name, arguments)


def evaluate_runtime_path(raw_path: str, *, workspace: Path | None = None) -> RuntimePolicyDecision:
    return _policy_evaluator_factory(workspace).evaluate_path(raw_path)


def evaluate_runtime_command(command: str, *, workspace: Path | None = None) -> RuntimePolicyDecision:
    return _policy_evaluator_factory(workspace).evaluate_command(command)
