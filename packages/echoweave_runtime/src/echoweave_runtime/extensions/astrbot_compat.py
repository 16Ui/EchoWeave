from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml


CompatibilityStatus = Literal["metadata-compatible", "api-candidate", "blocked"]

_IDENTITY_PATTERN = re.compile(r"^[^/\x00-\x1f\x7f]+$")
_BASIC_DECORATORS = {
    "command",
    "command_group",
    "event_message_type",
    "permission_type",
    "platform_adapter_type",
    "register",
}
_HOOK_DECORATORS = {
    "on_agent_begin",
    "on_astrbot_loaded",
    "on_llm_request",
    "on_llm_response",
    "on_llm_tool_respond",
    "on_using_llm_tool",
    "on_waiting_llm_request",
}
_SENSITIVE_IMPORTS = {
    "ctypes": "native-code",
    "shutil": "filesystem-write",
    "socket": "network",
    "subprocess": "host-process",
    "aiohttp": "network",
    "httpx": "network",
    "requests": "network",
}
_FILESYSTEM_WRITE_CALLS = {"mkdir", "rename", "rmdir", "unlink", "write_bytes", "write_text"}
_HOST_PROCESS_CALLS = {"execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp", "execvpe", "popen", "spawnl", "spawnv", "system"}


@dataclass(frozen=True, slots=True)
class AstrBotPluginManifest:
    name: str
    author: str
    version: str
    description: str
    repository: str | None = None
    display_name: str | None = None
    short_description: str | None = None
    support_platforms: tuple[str, ...] = ()
    astrbot_version: str | None = None
    unknown_fields: dict[str, Any] = field(default_factory=dict)

    @property
    def plugin_id(self) -> str:
        return f"{self.author}/{self.name}"


@dataclass(frozen=True, slots=True)
class AstrBotCompatibilityReport:
    plugin_root: Path
    manifest: AstrBotPluginManifest
    status: CompatibilityStatus
    execution_ready: bool
    decorators: tuple[str, ...] = ()
    lifecycle_methods: tuple[str, ...] = ()
    astrbot_imports: tuple[str, ...] = ()
    requested_capabilities: tuple[str, ...] = ()
    bundled_skills: tuple[Path, ...] = ()
    has_config_schema: bool = False
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugin_root": str(self.plugin_root),
            "plugin_id": self.manifest.plugin_id,
            "status": self.status,
            "execution_ready": self.execution_ready,
            "decorators": list(self.decorators),
            "lifecycle_methods": list(self.lifecycle_methods),
            "astrbot_imports": list(self.astrbot_imports),
            "requested_capabilities": list(self.requested_capabilities),
            "bundled_skills": [str(path) for path in self.bundled_skills],
            "has_config_schema": self.has_config_schema,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
        }


def inspect_astrbot_plugin(plugin_root: str | Path) -> AstrBotCompatibilityReport:
    """Inspect an AstrBot plugin without importing or executing plugin code."""

    root = Path(plugin_root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"AstrBot plugin root is not a directory: {root}")

    manifest = load_astrbot_manifest(root / "metadata.yaml")
    config_schema_path = root / "_conf_schema.json"
    if config_schema_path.exists():
        load_astrbot_config_schema(config_schema_path)

    main_path = root / "main.py"
    blockers: list[str] = []
    warnings: list[str] = []
    decorators: set[str] = set()
    astrbot_imports: set[str] = set()
    requested_capabilities: set[str] = set()
    lifecycle_methods: set[str] = set()

    if not main_path.is_file():
        blockers.append("missing required main.py entrypoint")
    else:
        source_files = _plugin_python_files(root)
        if main_path not in source_files:
            source_files = (main_path, *source_files)
        analysis = _AstrBotSourceAnalysis()
        for source_path in source_files:
            relative_path = source_path.relative_to(root).as_posix()
            try:
                tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
            except (OSError, UnicodeError, SyntaxError) as exc:
                blockers.append(f"{relative_path} cannot be statically parsed: {exc}")
                continue
            analysis.visit(tree)
        decorators.update(analysis.decorators)
        astrbot_imports.update(analysis.astrbot_imports)
        requested_capabilities.update(analysis.requested_capabilities)
        lifecycle_methods.update(analysis.lifecycle_methods)
        blockers.extend(analysis.blockers)

    unsupported_decorators = decorators - _BASIC_DECORATORS - _HOOK_DECORATORS
    if unsupported_decorators:
        warnings.append(
            "unclassified AstrBot decorators require a compatibility adapter: "
            + ", ".join(sorted(unsupported_decorators))
        )
    used_hooks = decorators & _HOOK_DECORATORS
    if used_hooks:
        warnings.append(
            "lifecycle/LLM hooks are recognized but not executable yet: "
            + ", ".join(sorted(used_hooks))
        )

    skills = _discover_bundled_skills(root)
    if blockers:
        status: CompatibilityStatus = "blocked"
    elif decorators or astrbot_imports:
        status = "api-candidate"
    else:
        status = "metadata-compatible"

    return AstrBotCompatibilityReport(
        plugin_root=root,
        manifest=manifest,
        status=status,
        execution_ready=False,
        decorators=tuple(sorted(decorators)),
        lifecycle_methods=tuple(sorted(lifecycle_methods)),
        astrbot_imports=tuple(sorted(astrbot_imports)),
        requested_capabilities=tuple(sorted(requested_capabilities)),
        bundled_skills=skills,
        has_config_schema=config_schema_path.is_file(),
        blockers=tuple(blockers),
        warnings=tuple(warnings),
    )


def load_astrbot_manifest(path: str | Path) -> AstrBotPluginManifest:
    manifest_path = Path(path).expanduser().resolve()
    try:
        value = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"missing AstrBot metadata.yaml: {manifest_path}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid AstrBot metadata.yaml: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("AstrBot metadata.yaml root must be an object")

    name = _required_identity(value, "name")
    author = _required_identity(value, "author")
    version = _required_text(value, "version")
    description = _required_text(value, "desc")
    platforms = value.get("support_platforms", value.get("support_platform", ()))
    if isinstance(platforms, str):
        platforms = [platforms]
    if not isinstance(platforms, (list, tuple)) or any(not isinstance(item, str) or not item.strip() for item in platforms):
        raise ValueError("AstrBot support_platforms must be a list of non-empty strings")

    known = {
        "name", "author", "version", "desc", "repo", "display_name", "short_desc",
        "support_platforms", "support_platform", "astrbot_version",
    }
    return AstrBotPluginManifest(
        name=name,
        author=author,
        version=version,
        description=description,
        repository=_optional_text(value.get("repo")),
        display_name=_optional_text(value.get("display_name")),
        short_description=_optional_text(value.get("short_desc")),
        support_platforms=tuple(item.strip() for item in platforms),
        astrbot_version=_optional_text(value.get("astrbot_version")),
        unknown_fields={str(key): item for key, item in value.items() if key not in known},
    )


def load_astrbot_config_schema(path: str | Path) -> dict[str, Any]:
    schema_path = Path(path).expanduser().resolve()
    try:
        value = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid AstrBot _conf_schema.json: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("AstrBot _conf_schema.json root must be an object")
    return value


class _AstrBotSourceAnalysis(ast.NodeVisitor):
    def __init__(self) -> None:
        self.decorators: set[str] = set()
        self.astrbot_imports: set[str] = set()
        self.requested_capabilities: set[str] = set()
        self.lifecycle_methods: set[str] = set()
        self.blockers: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for alias in node.names:
            self._record_import(alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        if node.module:
            self._record_import(node.module)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        name = _qualified_name(node.func)
        if name in {"__import__", "importlib.import_module"}:
            self.blockers.append("dynamic imports are not allowed by the compatibility loader")
        leaf = name.rsplit(".", 1)[-1]
        if leaf in _FILESYSTEM_WRITE_CALLS:
            self.requested_capabilities.add("filesystem-write")
        if name.startswith("os.") and leaf in _HOST_PROCESS_CALLS:
            self.requested_capabilities.add("host-process")
        if name in {"os.getenv"}:
            self.requested_capabilities.add("environment")
        if name == "open" and _open_call_may_write(node):
            self.requested_capabilities.add("filesystem-write")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        if _qualified_name(node) == "os.environ":
            self.requested_capabilities.add("environment")
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._record_lifecycle_method(node.name)
        self._record_decorators(node.decorator_list)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._record_lifecycle_method(node.name)
        self._record_decorators(node.decorator_list)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self._record_decorators(node.decorator_list)
        self.generic_visit(node)

    def _record_import(self, name: str) -> None:
        root = name.split(".", 1)[0]
        if name == "astrbot.core" or name.startswith("astrbot.core."):
            self.blockers.append(f"direct AstrBot core import is unsupported: {name}")
        elif name == "astrbot" or name.startswith("astrbot."):
            self.astrbot_imports.add(name)
        capability = _SENSITIVE_IMPORTS.get(root)
        if capability:
            self.requested_capabilities.add(capability)

    def _record_decorators(self, values: list[ast.expr]) -> None:
        for value in values:
            target = value.func if isinstance(value, ast.Call) else value
            name = _qualified_name(target).rsplit(".", 1)[-1]
            if name:
                self.decorators.add(name)

    def _record_lifecycle_method(self, name: str) -> None:
        if name in {"initialize", "terminate"}:
            self.lifecycle_methods.add(name)


def _qualified_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _qualified_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _open_call_may_write(node: ast.Call) -> bool:
    mode: ast.AST | None = node.args[1] if len(node.args) > 1 else None
    for keyword in node.keywords:
        if keyword.arg == "mode":
            mode = keyword.value
    if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
        return any(flag in mode.value for flag in "wax+")
    return False


def _discover_bundled_skills(root: Path) -> tuple[Path, ...]:
    skills_root = root / "skills"
    if not skills_root.is_dir():
        return ()
    direct = skills_root / "SKILL.md"
    if direct.is_file():
        return (direct.resolve(),)
    return tuple(sorted(path.resolve() for path in skills_root.glob("*/SKILL.md") if path.is_file()))


def _plugin_python_files(root: Path) -> tuple[Path, ...]:
    excluded_directories = {".git", ".tox", ".venv", "__pycache__", "node_modules", "venv"}
    files: list[Path] = []
    for path in root.rglob("*.py"):
        relative = path.relative_to(root)
        if any(part in excluded_directories for part in relative.parts):
            continue
        resolved = path.resolve()
        if not resolved.is_relative_to(root) or not resolved.is_file():
            continue
        files.append(resolved)
    return tuple(sorted(files))


def _required_identity(value: dict[str, Any], key: str) -> str:
    text = _required_text(value, key)
    if not _IDENTITY_PATTERN.fullmatch(text):
        raise ValueError(f"AstrBot {key} must not contain slash or control characters")
    return text


def _required_text(value: dict[str, Any], key: str) -> str:
    text = _optional_text(value.get(key))
    if text is None:
        raise ValueError(f"AstrBot metadata field {key!r} is required")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "AstrBotCompatibilityReport",
    "AstrBotPluginManifest",
    "CompatibilityStatus",
    "inspect_astrbot_plugin",
    "load_astrbot_config_schema",
    "load_astrbot_manifest",
]
