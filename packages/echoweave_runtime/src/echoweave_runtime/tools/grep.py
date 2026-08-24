from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from echoweave_runtime.governance import record_runtime_audit
from echoweave_runtime.tools_base import resolve_path


class GrepTool:
    name = "grep"
    description = "Search text by regex pattern"
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string"},
            "glob": {"type": "string"},
            "ignore_case": {"type": "boolean"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
            "include_hidden": {"type": "boolean"},
        },
        "required": ["pattern"],
    }

    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd.resolve()

    def execute(self, arguments: dict[str, Any]) -> str:
        raw_path = str(arguments.get("path", "."))
        try:
            target = resolve_path(self.cwd, raw_path)
            file_glob = str(arguments.get("glob", "*"))
            include_hidden = bool(arguments.get("include_hidden", False))
            limit = max(1, min(int(arguments.get("limit", 200)), 1000))

            flags = re.IGNORECASE if bool(arguments.get("ignore_case", False)) else 0
            try:
                regex = re.compile(str(arguments["pattern"]), flags)
            except re.error as exc:
                raise ValueError(f"invalid regex pattern: {exc}") from exc

            if target.is_file():
                files = [target]
                root = target.parent
            elif target.is_dir():
                files = sorted(
                    (p for p in target.rglob(file_glob) if p.is_file()),
                    key=lambda p: p.relative_to(target).as_posix(),
                )
                root = target
            else:
                raise ValueError("path does not exist")

            lines: list[str] = []
            for file_path in files:
                rel = file_path.relative_to(root)
                if not include_hidden and any(part.startswith(".") for part in rel.parts):
                    continue

                content = file_path.read_text(encoding="utf-8", errors="ignore")
                for line_no, line in enumerate(content.splitlines(), start=1):
                    if regex.search(line):
                        lines.append(f"{rel.as_posix()}:{line_no}:{line}")
                        if len(lines) >= limit:
                            record_runtime_audit("file", "grep", status="ok", workspace=self.cwd, metadata={"path": str(target), "pattern": str(arguments["pattern"]), "matches": len(lines)})
                            return "\n".join(lines)
        except Exception as exc:
            record_runtime_audit("file", "grep", status="error", workspace=self.cwd, metadata={"path": raw_path, "pattern": str(arguments.get("pattern", "")), "reason": str(exc), "reason_code": _reason_code(exc)})
            raise

        record_runtime_audit("file", "grep", status="ok", workspace=self.cwd, metadata={"path": str(target), "pattern": str(arguments["pattern"]), "matches": len(lines)})
        return "\n".join(lines) if lines else "(no matches)"


def _reason_code(exc: Exception) -> str:
    if "escapes working directory" in str(exc):
        return "file.path_escape"
    return "file.grep_failed"
