from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from echoweave_runtime.governance import record_runtime_audit


class TodoTool:
    name = "todo"
    effect = "non_idempotent"
    description = "Track coding-agent task todos in the workspace"
    input_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "set", "clear"]},
            "items": {
                "type": "array",
                "description": "For set: list of todo items with id, content, and status pending/in_progress/completed.",
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd.resolve()
        self.path = self.cwd / ".echoweave" / "todos.json"

    def execute(self, arguments: dict[str, Any]) -> str:
        action = str(arguments["action"])
        try:
            if action == "list":
                items = self._read_items()
                result = _render_items(items)
            elif action == "set":
                items = _normalize_items(arguments.get("items"))
                self._write_items(items)
                result = "Todo list updated.\n" + _render_items(items)
            elif action == "clear":
                self._write_items([])
                result = "Todo list cleared."
            else:
                raise ValueError(f"unsupported todo action: {action}")
        except Exception as exc:
            record_runtime_audit(
                "todo",
                action,
                status="error",
                workspace=self.cwd,
                metadata={"reason": str(exc), "reason_code": "todo.failed"},
            )
            raise
        record_runtime_audit(
            "todo",
            action,
            status="ok",
            workspace=self.cwd,
            metadata={"count": len(self._read_items())},
        )
        return result

    def _read_items(self) -> list[dict[str, str]]:
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        return _normalize_items(data)

    def _write_items(self, items: list[dict[str, str]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _normalize_items(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise ValueError("todo items must be a list")
    normalized: list[dict[str, str]] = []
    seen_in_progress = False
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"todo item #{index} must be an object")
        content = str(item.get("content") or "").strip()
        if not content:
            raise ValueError(f"todo item #{index} content is required")
        status = str(item.get("status") or "pending").strip()
        if status not in {"pending", "in_progress", "completed"}:
            raise ValueError(f"todo item #{index} has invalid status: {status}")
        if status == "in_progress":
            if seen_in_progress:
                raise ValueError("only one todo item can be in_progress")
            seen_in_progress = True
        item_id = str(item.get("id") or f"todo-{index}").strip()
        normalized.append({"id": item_id, "content": content, "status": status})
    return normalized


def _render_items(items: list[dict[str, str]]) -> str:
    if not items:
        return "No todos."
    return "\n".join(f"- [{item['status']}] {item['id']}: {item['content']}" for item in items)
