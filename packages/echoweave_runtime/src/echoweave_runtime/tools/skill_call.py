from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from echoweave_runtime.extensions.manager import ExtensionManager


class SkillCallTool:
    name = "skill_call"
    effect = "unknown"
    description = "Execute a registered local skill"
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "arguments": {"type": "object"},
        },
        "required": ["name"],
    }

    def __init__(self, cwd: Path, extensions: ExtensionManager) -> None:
        self.cwd = cwd.resolve()
        self.extensions = extensions

    def execute(self, arguments: dict[str, Any]) -> str:
        name = str(arguments.get("name", "")).strip()
        if not name:
            raise ValueError("skill name is required")
        payload = arguments.get("arguments", {})
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            raise ValueError("arguments must be an object")
        result = self.extensions.get_provider("skill").execute(name, payload)
        if isinstance(result, str):
            return result
        return json.dumps(result, ensure_ascii=False)
