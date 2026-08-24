from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from echoweave_runtime.extensions.manager import ExtensionManager


class McpCallTool:
    name = "mcp_call"
    description = "Call an MCP server method"
    input_schema = {
        "type": "object",
        "properties": {
            "server": {"type": "string"},
            "method": {"type": "string"},
            "params": {"type": "object"},
        },
        "required": ["server", "method"],
    }

    def __init__(self, cwd: Path, extensions: ExtensionManager) -> None:
        self.cwd = cwd.resolve()
        self.extensions = extensions

    def execute(self, arguments: dict[str, Any]) -> str:
        server = str(arguments.get("server", "")).strip()
        method = str(arguments.get("method", "")).strip()
        if not server:
            raise ValueError("server is required")
        if not method:
            raise ValueError("method is required")
        params = arguments.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise ValueError("params must be an object")
        result = self.extensions.get_provider("mcp").call(server, method, params)
        if isinstance(result, str):
            return result
        return json.dumps(result, ensure_ascii=False)
