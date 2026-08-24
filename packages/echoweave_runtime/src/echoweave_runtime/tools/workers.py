from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from echoweave_runtime.governance import record_runtime_audit
from echoweave_runtime.tools.agent import AgentTool
from echoweave_runtime.tools_base import resolve_path


class WorkersTool:
    name = "workers"
    effect = "non_idempotent"
    description = "Plan and run multiple isolated worker subtasks with write-conflict diagnostics"
    input_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["plan", "run"]},
            "workers": {
                "type": "array",
                "description": "Worker specs: {id, task, path, pattern, edits}. Edits use path/old_string/new_string.",
            },
            "max_workers": {"type": "integer", "minimum": 1, "maximum": 8},
        },
        "required": ["action", "workers"],
        "additionalProperties": False,
    }

    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd.resolve()

    def execute(self, arguments: dict[str, Any]) -> str:
        action = str(arguments["action"])
        specs = _normalize_workers(arguments.get("workers"), self.cwd)
        max_workers = int(arguments.get("max_workers") or 4)
        if len(specs) > max_workers:
            raise ValueError(f"too many workers: {len(specs)} > max_workers={max_workers}")
        plan = _build_plan(specs)
        try:
            if action == "plan":
                result = _render_plan(plan)
            elif action == "run":
                result = self._run(plan)
            else:
                raise ValueError(f"unsupported workers action: {action}")
        except Exception as exc:
            record_runtime_audit(
                "agent",
                "workers",
                status="error",
                workspace=self.cwd,
                metadata={"action": action, "reason": str(exc), "reason_code": "workers.failed"},
            )
            raise
        record_runtime_audit(
            "agent",
            "workers",
            status="ok",
            workspace=self.cwd,
            metadata={
                "action": action,
                "worker_count": len(specs),
                "conflicts": plan["conflicts"],
                "requires_serial": bool(plan["requires_serial"]),
            },
        )
        return result

    def _run(self, plan: dict[str, Any]) -> str:
        reports: list[dict[str, str]] = []
        agent = AgentTool(self.cwd)
        for spec in plan["workers"]:
            arguments: dict[str, Any] = {
                "role": "worker" if spec["edits"] else "explore",
                "task": spec["task"],
                "path": spec["path"],
                "max_chars": 12000,
            }
            if spec["edits"]:
                arguments["edits"] = spec["edits"]
            if spec["pattern"]:
                arguments["pattern"] = spec["pattern"]
            reports.append({"id": spec["id"], "report": agent.execute(arguments)})
        return json.dumps(
            {
                "requires_serial": plan["requires_serial"],
                "conflicts": plan["conflicts"],
                "reports": reports,
            },
            ensure_ascii=False,
            indent=2,
        )


def _normalize_workers(value: Any, cwd: Path) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ValueError("workers must be a non-empty list")
    workers: list[dict[str, Any]] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"worker #{index} must be an object")
        worker_id = str(item.get("id") or f"worker-{index}").strip()
        task = str(item.get("task") or "").strip()
        if not task:
            raise ValueError(f"worker {worker_id} task is required")
        path = str(item.get("path") or ".")
        resolve_path(cwd, path)
        edits = _normalize_edits(item.get("edits") or [])
        for edit in edits:
            resolve_path(cwd, edit["path"])
        workers.append(
            {
                "id": worker_id,
                "task": task,
                "path": path,
                "pattern": str(item.get("pattern") or "").strip(),
                "edits": edits,
            }
        )
    return workers


def _normalize_edits(value: Any) -> list[dict[str, str]]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ValueError("worker edits must be a list")
    edits: list[dict[str, str]] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"worker edit #{index} must be an object")
        raw_path = item.get("path")
        old = item.get("old_string", item.get("old"))
        new = item.get("new_string", item.get("new"))
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError(f"worker edit #{index} path is required")
        if not isinstance(old, str) or not old:
            raise ValueError(f"worker edit #{index} old_string is required")
        if not isinstance(new, str):
            raise ValueError(f"worker edit #{index} new_string is required")
        edits.append({"path": raw_path, "old_string": old, "new_string": new})
    return edits


def _build_plan(workers: list[dict[str, Any]]) -> dict[str, Any]:
    write_owners: dict[str, list[str]] = {}
    for spec in workers:
        for edit in spec["edits"]:
            write_owners.setdefault(edit["path"], []).append(spec["id"])
    conflicts = [
        {"path": path, "workers": owners}
        for path, owners in sorted(write_owners.items())
        if len(set(owners)) > 1
    ]
    return {
        "workers": workers,
        "conflicts": conflicts,
        "requires_serial": bool(conflicts),
        "read_only_workers": [spec["id"] for spec in workers if not spec["edits"]],
        "write_workers": [spec["id"] for spec in workers if spec["edits"]],
    }


def _render_plan(plan: dict[str, Any]) -> str:
    return json.dumps(
        {
            "worker_count": len(plan["workers"]),
            "read_only_workers": plan["read_only_workers"],
            "write_workers": plan["write_workers"],
            "requires_serial": plan["requires_serial"],
            "conflicts": plan["conflicts"],
            "workers": [
                {
                    "id": spec["id"],
                    "task": spec["task"],
                    "path": spec["path"],
                    "writes": [edit["path"] for edit in spec["edits"]],
                }
                for spec in plan["workers"]
            ],
        },
        ensure_ascii=False,
        indent=2,
    )
