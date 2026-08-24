from __future__ import annotations

from pathlib import Path
from typing import Any

from echoweave_harness.audit import record_audit
from echoweave_harness.policy import HarnessPolicyEvaluator, get_harness_policy
from echoweave_runtime.governance import (
    RuntimePolicyDecision,
    configure_runtime_audit_recorder,
    configure_runtime_policy_evaluator,
)


class HarnessRuntimePolicyEvaluator:
    def __init__(self, workspace: Path | None = None) -> None:
        self._evaluator = HarnessPolicyEvaluator(get_harness_policy(), workspace=workspace)

    def evaluate_tool(self, tool_name: str, arguments: dict[str, Any] | None = None) -> RuntimePolicyDecision:
        return _to_runtime_decision(self._evaluator.evaluate_tool(tool_name, arguments))

    def evaluate_path(self, raw_path: str) -> RuntimePolicyDecision:
        return _to_runtime_decision(self._evaluator.evaluate_path(raw_path))

    def evaluate_command(self, command: str) -> RuntimePolicyDecision:
        return _to_runtime_decision(self._evaluator.evaluate_command(command))


def install_runtime_bridge() -> None:
    configure_runtime_audit_recorder(record_audit)
    configure_runtime_policy_evaluator(lambda workspace=None: HarnessRuntimePolicyEvaluator(workspace))


def _to_runtime_decision(decision: Any) -> RuntimePolicyDecision:
    return RuntimePolicyDecision(
        decision=str(decision.decision),
        reason=str(decision.reason or ""),
        reason_code=str(decision.reason_code or ""),
        matched_rules=tuple(str(rule) for rule in decision.matched_rules),
    )
