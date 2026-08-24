from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PolicyDecision:
    decision: str
    reason: str = ""
    reason_code: str = ""
    matched_rules: tuple[str, ...] = ()

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"


@dataclass(frozen=True)
class HarnessPolicy:
    allowed_tools: tuple[str, ...] = ()
    denied_tools: tuple[str, ...] = ()
    allowed_paths: tuple[str, ...] = ()
    denied_paths: tuple[str, ...] = ()
    command_allow_patterns: tuple[str, ...] = ()
    command_approval_patterns: tuple[str, ...] = ()
    command_deny_patterns: tuple[str, ...] = ()
    session_model_allowlist: tuple[str, ...] = ()
    session_skill_allowlist: tuple[str, ...] = ()
    session_rag_enabled: bool | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class HarnessPolicyEvaluator:
    def __init__(self, policy: HarnessPolicy, *, workspace: Path | None = None) -> None:
        self.policy = policy
        self.workspace = workspace.expanduser().resolve() if workspace is not None else None

    def evaluate_tool(self, tool_name: str, arguments: dict[str, Any] | None = None) -> PolicyDecision:
        if tool_name in self.policy.denied_tools:
            return PolicyDecision("deny", f"tool is denied: {tool_name}", "harness.tool.denied", (tool_name,))
        if self.policy.allowed_tools and tool_name not in self.policy.allowed_tools:
            return PolicyDecision("deny", f"tool is not in allowed_tools: {tool_name}", "harness.tool.not_allowed", (tool_name,))
        if arguments:
            path_value = arguments.get("path") or arguments.get("cwd")
            if isinstance(path_value, str) and path_value:
                path_decision = self.evaluate_path(path_value)
                if not path_decision.allowed:
                    return path_decision
        return PolicyDecision("allow", reason_code="harness.tool.allowed")

    def evaluate_path(self, raw_path: str) -> PolicyDecision:
        path = Path(raw_path).expanduser()
        if self.workspace is not None and not path.is_absolute():
            path = self.workspace / path
        resolved = path.resolve()
        for denied in self.policy.denied_paths:
            denied_path = Path(denied).expanduser().resolve()
            if _is_relative_to(resolved, denied_path) or resolved == denied_path:
                return PolicyDecision("deny", f"path is denied: {resolved}", "harness.path.denied", (denied,))
        if self.policy.allowed_paths:
            allowed = False
            for raw_allowed in self.policy.allowed_paths:
                allowed_path = Path(raw_allowed).expanduser().resolve()
                if _is_relative_to(resolved, allowed_path) or resolved == allowed_path:
                    allowed = True
                    break
            if not allowed:
                return PolicyDecision("deny", f"path is outside allowed_paths: {resolved}", "harness.path.not_allowed")
        return PolicyDecision("allow", reason_code="harness.path.allowed")

    def evaluate_command(self, command: str) -> PolicyDecision:
        normalized = " ".join(command.strip().split())
        for index, pattern in enumerate(self.policy.command_deny_patterns):
            if re.search(pattern, normalized, flags=re.IGNORECASE):
                return PolicyDecision("deny", "command matched deny pattern", "harness.command.denied", (f"deny[{index}]",))
        for index, pattern in enumerate(self.policy.command_approval_patterns):
            if re.search(pattern, normalized, flags=re.IGNORECASE):
                return PolicyDecision("escalate", "command matched approval pattern", "harness.command.requires_approval", (f"approval[{index}]",))
        if self.policy.command_allow_patterns:
            for index, pattern in enumerate(self.policy.command_allow_patterns):
                if re.search(pattern, normalized, flags=re.IGNORECASE):
                    return PolicyDecision("allow", reason_code="harness.command.allowed", matched_rules=(f"allow[{index}]",))
            return PolicyDecision("deny", "command did not match allow patterns", "harness.command.not_allowed")
        return PolicyDecision("allow", reason_code="harness.command.allowed")

    def evaluate_model(self, model_name: str) -> PolicyDecision:
        if self.policy.session_model_allowlist and model_name not in self.policy.session_model_allowlist:
            return PolicyDecision(
                "deny",
                f"model is outside session_model_allowlist: {model_name}",
                "harness.model.not_allowed",
                (model_name,),
            )
        return PolicyDecision("allow", reason_code="harness.model.allowed")

    def evaluate_skill(self, skill_name: str) -> PolicyDecision:
        if self.policy.session_skill_allowlist and skill_name not in self.policy.session_skill_allowlist:
            return PolicyDecision(
                "deny",
                f"skill is outside session_skill_allowlist: {skill_name}",
                "harness.skill.not_allowed",
                (skill_name,),
            )
        return PolicyDecision("allow", reason_code="harness.skill.allowed")

    def evaluate_rag(self, enabled: bool) -> PolicyDecision:
        if self.policy.session_rag_enabled is not None and enabled != self.policy.session_rag_enabled:
            expected = "enabled" if self.policy.session_rag_enabled else "disabled"
            actual = "enabled" if enabled else "disabled"
            return PolicyDecision(
                "deny",
                f"RAG is {actual}, but policy requires {expected}",
                "harness.rag.not_allowed",
            )
        return PolicyDecision("allow", reason_code="harness.rag.allowed")


def load_harness_policy(data: dict[str, Any] | None) -> HarnessPolicy:
    if not isinstance(data, dict):
        return HarnessPolicy()
    return HarnessPolicy(
        allowed_tools=_str_tuple(data.get("allowed_tools")),
        denied_tools=_str_tuple(data.get("denied_tools")),
        allowed_paths=_str_tuple(data.get("allowed_paths")),
        denied_paths=_str_tuple(data.get("denied_paths")),
        command_allow_patterns=_str_tuple(data.get("command_allow_patterns")),
        command_approval_patterns=_str_tuple(data.get("command_approval_patterns")),
        command_deny_patterns=_str_tuple(data.get("command_deny_patterns")),
        session_model_allowlist=_str_tuple(data.get("session_model_allowlist")),
        session_skill_allowlist=_str_tuple(data.get("session_skill_allowlist")),
        session_rag_enabled=data.get("session_rag_enabled") if isinstance(data.get("session_rag_enabled"), bool) else None,
        metadata={key: value for key, value in data.items() if key not in _KNOWN_KEYS},
    )


def _str_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(",") if part.strip())
    if isinstance(value, list):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


_KNOWN_KEYS = {
    "allowed_tools",
    "denied_tools",
    "allowed_paths",
    "denied_paths",
    "command_allow_patterns",
    "command_approval_patterns",
    "command_deny_patterns",
    "session_model_allowlist",
    "session_skill_allowlist",
    "session_rag_enabled",
}


_active_policy = HarnessPolicy()


def configure_harness_policy(data: dict[str, Any] | HarnessPolicy | None) -> HarnessPolicy:
    global _active_policy
    if isinstance(data, HarnessPolicy):
        _active_policy = data
    else:
        _active_policy = load_harness_policy(data)
    return _active_policy


def get_harness_policy() -> HarnessPolicy:
    return _active_policy
