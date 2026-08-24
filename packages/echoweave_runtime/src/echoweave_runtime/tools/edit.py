from __future__ import annotations

import difflib
from pathlib import Path

from echoweave_runtime.governance import record_runtime_audit
from echoweave_runtime.tools_base import resolve_path


class EditTool:
    name = "edit"
    description = "Replace exact text in a file"
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file inside the current workspace."},
            "old": {"type": "string", "description": "Exact text to replace. Must appear exactly once."},
            "new": {"type": "string", "description": "Replacement text."},
            "old_string": {"type": "string", "description": "Alias of old. Prefer this name for Claude-Code-style edits."},
            "new_string": {"type": "string", "description": "Alias of new. Prefer this name for Claude-Code-style edits."},
        },
        "required": ["path"],
        "anyOf": [
            {"required": ["old", "new"]},
            {"required": ["old_string", "new_string"]},
        ],
        "additionalProperties": False,
    }

    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd

    def execute(self, arguments: dict[str, str]) -> str:
        raw_path = arguments["path"]
        try:
            path = resolve_path(self.cwd, raw_path)
            if not path.exists():
                raise FileNotFoundError(f"file not found: {path}")
            text = path.read_text(encoding="utf-8")
            old = _pick_text_argument(arguments, "old", "old_string")
            new = _pick_text_argument(arguments, "new", "new_string")
            if old == "":
                raise ValueError("old text must not be empty")
            match_count = text.count(old)
            if match_count == 0:
                raise ValueError("old text not found")
            if match_count > 1:
                raise ValueError(
                    f"old text is not unique: found {match_count} matches; include more surrounding context"
                )
            next_text = text.replace(old, new, 1)
            diff = _unified_diff(path, text, next_text)
            path.write_text(next_text, encoding="utf-8")
        except Exception as exc:
            record_runtime_audit(
                "file",
                "edit",
                status="error",
                workspace=self.cwd,
                metadata={"path": raw_path, "reason": str(exc), "reason_code": _reason_code(exc)},
            )
            raise
        record_runtime_audit(
            "file",
            "edit",
            status="ok",
            workspace=self.cwd,
            metadata={
                "path": str(path),
                "old_length": len(old),
                "new_length": len(new),
                "match_count": 1,
                "diff_chars": len(diff),
            },
        )
        return f"Edited {path}\n{diff}"


def _pick_text_argument(arguments: dict[str, str], primary: str, alias: str) -> str:
    if primary in arguments:
        value = arguments[primary]
    elif alias in arguments:
        value = arguments[alias]
    else:
        raise ValueError(f"missing required field: {primary} or {alias}")
    if not isinstance(value, str):
        raise TypeError(f"{primary} must be a string")
    return value


def _unified_diff(path: Path, old_text: str, new_text: str, max_chars: int = 6000) -> str:
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
    head = diff[: max_chars // 2].rstrip()
    tail = diff[-max_chars // 2 :].lstrip()
    return f"{head}\n... diff truncated ...\n{tail}"


def _reason_code(exc: Exception) -> str:
    if "escapes working directory" in str(exc):
        return "file.path_escape"
    if "not unique" in str(exc):
        return "file.edit_not_unique"
    if "not found" in str(exc):
        return "file.edit_not_found"
    return "file.edit_failed"
