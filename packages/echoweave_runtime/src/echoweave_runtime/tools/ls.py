from __future__ import annotations

from pathlib import Path
from typing import Any

from echoweave_runtime.governance import record_runtime_audit
from echoweave_runtime.tools_base import resolve_path


class LsTool:
    name = "ls"
    effect = "read_only"
    description = "List files and directories"
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "recursive": {"type": "boolean"},
            "include_hidden": {"type": "boolean"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
        },
    }

    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd.resolve()

    def execute(self, arguments: dict[str, Any]) -> str:
        raw_path = str(arguments.get("path", "."))
        try:
            target = resolve_path(self.cwd, raw_path)
            if not target.is_dir():
                raise ValueError("path must be a directory")

            recursive = bool(arguments.get("recursive", False))
            include_hidden = bool(arguments.get("include_hidden", False))
            limit = max(1, min(int(arguments.get("limit", 200)), 1000))

            if recursive:
                entries = sorted(target.rglob("*"), key=lambda p: p.relative_to(target).as_posix())
            else:
                entries = sorted(target.iterdir(), key=lambda p: p.name)

            lines: list[str] = []
            for entry in entries:
                rel = entry.relative_to(target)
                if not include_hidden and any(part.startswith(".") for part in rel.parts):
                    continue
                suffix = "/" if entry.is_dir() else ""
                lines.append(f"{rel.as_posix()}{suffix}")
                if len(lines) >= limit:
                    break
        except Exception as exc:
            record_runtime_audit("file", "list", status="error", workspace=self.cwd, metadata={"path": raw_path, "reason": str(exc), "reason_code": _reason_code(exc)})
            raise

        record_runtime_audit("file", "list", status="ok", workspace=self.cwd, metadata={"path": str(target), "count": len(lines), "recursive": recursive})
        return "\n".join(lines) if lines else "(empty)"


def _reason_code(exc: Exception) -> str:
    if "escapes working directory" in str(exc):
        return "file.path_escape"
    return "file.list_failed"
