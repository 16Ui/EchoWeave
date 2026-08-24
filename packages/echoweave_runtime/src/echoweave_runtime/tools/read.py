from __future__ import annotations

from pathlib import Path
from typing import Any

from echoweave_runtime.governance import record_runtime_audit
from echoweave_runtime.tools_base import resolve_path


class ReadTool:
    name = "read"
    description = "Read a file from disk"
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "start_line": {"type": "integer", "minimum": 1},
            "end_line": {"type": "integer", "minimum": 1},
            "max_chars": {"type": "integer", "minimum": 500, "maximum": 50000},
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd

    def execute(self, arguments: dict[str, Any]) -> str:
        raw_path = arguments["path"]
        try:
            path = resolve_path(self.cwd, raw_path)
            content = path.read_text(encoding="utf-8")
            selected = _select_lines(
                content,
                start_line=arguments.get("start_line"),
                end_line=arguments.get("end_line"),
            )
            rendered, truncated = _truncate_output(selected, max_chars=int(arguments.get("max_chars") or 20000))
        except Exception as exc:
            record_runtime_audit(
                "file",
                "read",
                status="error",
                workspace=self.cwd,
                metadata={"path": raw_path, "reason": str(exc), "reason_code": _reason_code(exc)},
            )
            raise
        record_runtime_audit(
            "file",
            "read",
            status="ok",
            workspace=self.cwd,
            metadata={
                "path": str(path),
                "bytes": len(content.encode("utf-8")),
                "rendered_chars": len(rendered),
                "truncated": truncated,
                "start_line": arguments.get("start_line"),
                "end_line": arguments.get("end_line"),
            },
        )
        return rendered


def _select_lines(content: str, *, start_line: Any = None, end_line: Any = None) -> str:
    if start_line is None and end_line is None:
        return content
    lines = content.splitlines()
    start = max(1, int(start_line or 1))
    end = int(end_line or len(lines))
    if end < start:
        raise ValueError("end_line must be >= start_line")
    selected = lines[start - 1 : end]
    return "\n".join(f"{line_no}: {line}" for line_no, line in enumerate(selected, start=start))


def _truncate_output(content: str, *, max_chars: int) -> tuple[str, bool]:
    if len(content) <= max_chars:
        return content, False
    head_size = max_chars // 2
    tail_size = max_chars - head_size
    rendered = (
        content[:head_size].rstrip()
        + f"\n... file output truncated, original_chars={len(content)} ...\n"
        + content[-tail_size:].lstrip()
    )
    return rendered, True


def _reason_code(exc: Exception) -> str:
    if "escapes working directory" in str(exc):
        return "file.path_escape"
    return "file.read_failed"
