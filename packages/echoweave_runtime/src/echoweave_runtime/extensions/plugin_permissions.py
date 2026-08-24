from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


KNOWN_PLUGIN_CAPABILITIES = frozenset(
    {"environment", "filesystem-write", "host-process", "native-code", "network"}
)


@dataclass(frozen=True, slots=True)
class PluginPermissionManifest:
    schema_version: int = 1
    capabilities: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class PluginPermissionDecision:
    allowed: bool
    requested: frozenset[str]
    declared: frozenset[str]
    granted: frozenset[str]
    undeclared: frozenset[str]
    ungranted: frozenset[str]

    @property
    def reasons(self) -> tuple[str, ...]:
        reasons: list[str] = []
        if self.undeclared:
            reasons.append("capabilities used but not declared: " + ", ".join(sorted(self.undeclared)))
        if self.ungranted:
            reasons.append("capabilities not granted by runtime: " + ", ".join(sorted(self.ungranted)))
        return tuple(reasons)


def load_plugin_permissions(plugin_root: str | Path) -> PluginPermissionManifest:
    path = Path(plugin_root).expanduser().resolve() / "echoweave.permissions.json"
    if not path.exists():
        return PluginPermissionManifest()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid echoweave.permissions.json: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("echoweave.permissions.json root must be an object")
    version = value.get("schema_version", 1)
    if version != 1:
        raise ValueError(f"unsupported plugin permission schema_version: {version}")
    capabilities = value.get("capabilities", [])
    if not isinstance(capabilities, list) or any(not isinstance(item, str) for item in capabilities):
        raise ValueError("plugin capabilities must be a list of strings")
    normalized = frozenset(item.strip() for item in capabilities if item.strip())
    unknown = normalized - KNOWN_PLUGIN_CAPABILITIES
    if unknown:
        raise ValueError("unknown plugin capabilities: " + ", ".join(sorted(unknown)))
    return PluginPermissionManifest(schema_version=1, capabilities=normalized)


def evaluate_plugin_permissions(
    requested: Iterable[str],
    declared: Iterable[str],
    granted: Iterable[str],
) -> PluginPermissionDecision:
    requested_set = frozenset(requested)
    declared_set = frozenset(declared)
    granted_set = frozenset(granted)
    undeclared = requested_set - declared_set
    ungranted = requested_set - granted_set
    return PluginPermissionDecision(
        allowed=not undeclared and not ungranted,
        requested=requested_set,
        declared=declared_set,
        granted=granted_set,
        undeclared=undeclared,
        ungranted=ungranted,
    )


__all__ = [
    "KNOWN_PLUGIN_CAPABILITIES",
    "PluginPermissionDecision",
    "PluginPermissionManifest",
    "evaluate_plugin_permissions",
    "load_plugin_permissions",
]
