from __future__ import annotations

import difflib
from pathlib import Path

from echoweave_runtime.governance import record_runtime_audit
from echoweave_runtime.tools_base import resolve_path


class WriteTool:
    name = "write"
    effect = "idempotent_write"
    description = "Write full content to a file; use edit for small precise changes"
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
            "overwrite": {"type": "boolean"},
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    }

    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd

    def classify_effect(self, arguments: dict[str, str]) -> str:
        return "non_idempotent" if arguments.get("overwrite") is False else self.effect

    def execute(self, arguments: dict[str, str]) -> str:
        raw_path = arguments["path"]
        try:
            path = resolve_path(self.cwd, raw_path)
            existed = path.exists()
            previous = path.read_text(encoding="utf-8") if existed else ""
            if existed and arguments.get("overwrite") is False:
                raise FileExistsError(f"file already exists: {path}")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(arguments["content"], encoding="utf-8")
            diff = _write_diff(path, previous, arguments["content"]) if existed else ""
        except Exception as exc:
            record_runtime_audit(
                "file",
                "write",
                status="error",
                workspace=self.cwd,
                metadata={"path": raw_path, "reason": str(exc), "reason_code": _reason_code(exc)},
            )
            raise
        record_runtime_audit(
            "file",
            "write",
            status="ok",
            workspace=self.cwd,
            metadata={
                "path": str(path),
                "bytes": len(arguments["content"].encode("utf-8")),
                "created": not existed,
                "diff_chars": len(diff),
            },
        )
        if existed:
            return f"Wrote {path}\n{diff}"
        return f"Created {path} ({len(arguments['content'].encode('utf-8'))} bytes)"


def _write_diff(path: Path, old_text: str, new_text: str, max_chars: int = 6000) -> str:
    diff = "".join(
        difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=f"{path}:before",
            tofile=f"{path}:after",
            lineterm="",
        )
    )
    if len(diff) <= max_chars:
        return diff
    return diff[: max_chars // 2].rstrip() + "\n... diff truncated ...\n" + diff[-max_chars // 2 :].lstrip()


def _reason_code(exc: Exception) -> str:
    if "escapes working directory" in str(exc):
        return "file.path_escape"
    return "file.write_failed"
