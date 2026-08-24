from __future__ import annotations

from pathlib import Path

from echoweave_runtime.tools_base import resolve_path


class WriteTool:
    name = "write"
    description = "Write content to a file"
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
    }

    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd

    def execute(self, arguments: dict[str, str]) -> str:
        path = resolve_path(self.cwd, arguments["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(arguments["content"], encoding="utf-8")
        return f"Wrote {path}"
