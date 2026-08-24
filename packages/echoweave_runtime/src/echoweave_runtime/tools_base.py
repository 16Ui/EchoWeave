from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from echoweave_runtime.governance import evaluate_runtime_path, evaluate_runtime_tool

class Tool(Protocol):
    name: str
    description: str
    input_schema: dict[str, Any]

    def execute(self, arguments: dict[str, Any]) -> str:
        ...


ToolSource = Literal["builtin", "workspace", "user", "extension"]
ToolConflictPolicy = Literal["builtin_wins", "extension_wins", "error_on_conflict"]


@dataclass
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    executor: callable


class ToolRegistry:
    def __init__(self, conflict_policy: ToolConflictPolicy = "builtin_wins") -> None:
        self._tools: dict[str, Tool] = {}
        self._sources: dict[str, ToolSource] = {}
        self._conflict_policy: ToolConflictPolicy = conflict_policy
        self._conflicts: list[dict[str, Any]] = []

    def _rank(self, source: ToolSource, policy: ToolConflictPolicy) -> int:
        if policy == "extension_wins":
            order = {"extension": 4, "workspace": 3, "user": 2, "builtin": 1}
            return order[source]
        if policy == "builtin_wins":
            order = {"builtin": 4, "workspace": 3, "user": 2, "extension": 1}
            return order[source]
        return 0

    def _resolve_conflict(
        self,
        name: str,
        existing_source: ToolSource,
        incoming_source: ToolSource,
        policy: ToolConflictPolicy,
    ) -> str:
        if policy == "error_on_conflict":
            self._conflicts.append(
                {
                    "name": name,
                    "existing_source": existing_source,
                    "incoming_source": incoming_source,
                    "policy": policy,
                    "decision": "error",
                }
            )
            raise ValueError(
                f"tool conflict: {name} (existing={existing_source}, incoming={incoming_source}, policy={policy})"
            )

        existing_rank = self._rank(existing_source, policy)
        incoming_rank = self._rank(incoming_source, policy)
        decision = "replace" if incoming_rank >= existing_rank else "keep_existing"
        self._conflicts.append(
            {
                "name": name,
                "existing_source": existing_source,
                "incoming_source": incoming_source,
                "policy": policy,
                "decision": decision,
            }
        )
        return decision

    def register(
        self,
        tool: Tool,
        source: ToolSource = "builtin",
        conflict_policy: ToolConflictPolicy | None = None,
    ) -> bool:
        existing = self._tools.get(tool.name)
        if existing is None:
            self._tools[tool.name] = tool
            self._sources[tool.name] = source
            return True

        policy = conflict_policy or self._conflict_policy
        existing_source = self._sources.get(tool.name, "builtin")
        decision = self._resolve_conflict(tool.name, existing_source, source, policy)
        if decision == "replace":
            self._tools[tool.name] = tool
            self._sources[tool.name] = source
            return True
        return False

    def get(self, name: str) -> Tool:
        decision = evaluate_runtime_tool(name)
        if not decision.allowed:
            raise PermissionError(f"blocked by harness policy: {decision.reason}")
        return self._tools[name]

    def get_source(self, name: str) -> ToolSource:
        return self._sources[name]

    def list(self) -> list[Tool]:
        return list(self._tools.values())

    def list_with_sources(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "source": self._sources.get(tool.name, "builtin"),
            }
            for tool in self._tools.values()
        ]

    def conflict_diagnostics(self) -> list[dict[str, Any]]:
        return list(self._conflicts)

    def as_anthropic_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in self._tools.values()
        ]


def resolve_path(cwd: Path, raw_path: str) -> Path:
    base = cwd.resolve()
    target = (base / raw_path).resolve()
    if target != base and base not in target.parents:
        raise ValueError("path escapes working directory")
    decision = evaluate_runtime_path(str(target), workspace=base)
    if not decision.allowed:
        raise ValueError(f"{decision.reason} ({decision.reason_code})")
    return target
