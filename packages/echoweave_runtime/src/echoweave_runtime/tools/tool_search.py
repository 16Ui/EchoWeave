from __future__ import annotations

from typing import Any

from echoweave_runtime.governance import record_runtime_audit
from echoweave_runtime.tools_base import ToolRegistry


class ToolSearchTool:
    name = "tool_search"
    description = "Search and list currently registered tools by name or description"
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Keyword to match tool name or description."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry
        self.cwd = None

    def execute(self, arguments: dict[str, Any]) -> str:
        query = str(arguments.get("query") or "").strip().lower()
        limit = max(1, min(int(arguments.get("limit") or 20), 50))
        items = self.registry.list_with_sources()
        if query:
            items = [
                item
                for item in items
                if query in str(item.get("name", "")).lower()
                or query in str(item.get("description", "")).lower()
                or query in str(item.get("source", "")).lower()
            ]
        items = items[:limit]
        record_runtime_audit(
            "tool",
            "search",
            status="ok",
            subject=query,
            metadata={"query": query, "result_count": len(items), "limit": limit},
        )
        if not items:
            return "(no matching tools)"
        return "\n".join(
            f"- {item['name']} [{item.get('source', 'unknown')}]: {item.get('description', '')}"
            for item in items
        )
