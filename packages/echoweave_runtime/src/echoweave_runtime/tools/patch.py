from __future__ import annotations

import difflib
import json
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from echoweave_runtime.governance import record_runtime_audit
from echoweave_runtime.tools_base import resolve_path


class PatchTool:
    name = "patch"
    description = "Stage, review, apply, discard, or rollback worker patches with explicit confirmation"
    input_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["stage", "list", "show", "apply", "discard", "rollback"]},
            "id": {"type": "string", "description": "Patch id for show/apply/discard/rollback."},
            "task": {"type": "string", "description": "Human-readable reason for a staged patch."},
            "edits": {
                "type": "array",
                "description": "For stage: list of {path, old_string, new_string}. old/new aliases are also accepted.",
            },
            "confirm": {
                "type": "boolean",
                "description": "Apply requires confirm=true so patches are never applied accidentally.",
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd.resolve()
        self.dir = self.cwd / ".echoweave" / "patches"

    def execute(self, arguments: dict[str, Any]) -> str:
        action = str(arguments["action"])
        try:
            if action == "stage":
                result = self._stage(str(arguments.get("task") or "worker patch"), arguments.get("edits"))
            elif action == "list":
                result = self._list()
            elif action == "show":
                result = self._show(_required_id(arguments))
            elif action == "apply":
                result = self._apply(_required_id(arguments), confirm=bool(arguments.get("confirm", False)))
            elif action == "discard":
                result = self._discard(_required_id(arguments))
            elif action == "rollback":
                result = self._rollback(_required_id(arguments))
            else:
                raise ValueError(f"unsupported patch action: {action}")
        except Exception as exc:
            record_runtime_audit(
                "patch",
                action,
                status="error",
                workspace=self.cwd,
                metadata={"reason": str(exc), "reason_code": "patch.failed"},
            )
            raise
        record_runtime_audit("patch", action, status="ok", workspace=self.cwd, metadata={"action": action})
        return result

    def _stage(self, task: str, edits_value: Any) -> str:
        edits = _normalize_edits(edits_value)
        patch_id = uuid4().hex[:12]
        patches: list[str] = []
        normalized_edits: list[dict[str, str]] = []
        for edit in edits:
            path = resolve_path(self.cwd, edit["path"])
            before = path.read_text(encoding="utf-8", errors="replace")
            count = before.count(edit["old_string"])
            if count == 0:
                raise ValueError(f"patch text not found in {edit['path']}")
            if count > 1:
                raise ValueError(f"patch text is not unique in {edit['path']}: {count} matches")
            after = before.replace(edit["old_string"], edit["new_string"], 1)
            patches.append(_unified_diff(before, after, path.relative_to(self.cwd)))
            normalized_edits.append(edit)
        record = {
            "id": patch_id,
            "task": task,
            "status": "staged",
            "created_at": time.time(),
            "applied_at": None,
            "rolled_back_at": None,
            "edits": normalized_edits,
            "diff": "\n\n".join(patches),
            "backups": [],
        }
        self._write_record(record)
        return f"Patch staged: {patch_id}\nStatus: staged\nApply with confirm=true after review.\n\n{record['diff']}"

    def _list(self) -> str:
        records = sorted(self._read_all(), key=lambda item: float(item.get("created_at") or 0), reverse=True)
        if not records:
            return "No patches."
        return "\n".join(f"- {item['id']} [{item.get('status', 'unknown')}] {item.get('task', '')}" for item in records)

    def _show(self, patch_id: str) -> str:
        record = self._read_record(patch_id)
        return f"Patch: {record['id']}\nStatus: {record.get('status')}\nTask: {record.get('task')}\n\n{record.get('diff', '')}"

    def _apply(self, patch_id: str, *, confirm: bool) -> str:
        if not confirm:
            raise PermissionError("patch apply requires confirm=true after reviewing the diff")
        record = self._read_record(patch_id)
        if record.get("status") != "staged":
            raise ValueError(f"patch {patch_id} is {record.get('status')}, not staged")
        backups: list[dict[str, str]] = []
        applied_diffs: list[str] = []
        for edit in _normalize_edits(record.get("edits")):
            path = resolve_path(self.cwd, edit["path"])
            before = path.read_text(encoding="utf-8", errors="replace")
            count = before.count(edit["old_string"])
            if count == 0:
                raise ValueError(f"patch text not found in {edit['path']}")
            if count > 1:
                raise ValueError(f"patch text is not unique in {edit['path']}: {count} matches")
            after = before.replace(edit["old_string"], edit["new_string"], 1)
            backups.append({"path": edit["path"], "content": before})
            path.write_text(after, encoding="utf-8")
            applied_diffs.append(_unified_diff(before, after, path.relative_to(self.cwd)))
        record["status"] = "applied"
        record["applied_at"] = time.time()
        record["backups"] = backups
        record["applied_diff"] = "\n\n".join(applied_diffs)
        self._write_record(record)
        return f"Patch applied: {patch_id}\nRollback with action=rollback if needed.\n\n{record['applied_diff']}"

    def _discard(self, patch_id: str) -> str:
        record = self._read_record(patch_id)
        if record.get("status") == "applied":
            raise ValueError("applied patches must be rolled back, not discarded")
        record["status"] = "discarded"
        record["discarded_at"] = time.time()
        self._write_record(record)
        return f"Patch discarded: {patch_id}"

    def _rollback(self, patch_id: str) -> str:
        record = self._read_record(patch_id)
        if record.get("status") != "applied":
            raise ValueError(f"patch {patch_id} is {record.get('status')}, not applied")
        backups = record.get("backups")
        if not isinstance(backups, list) or not backups:
            raise ValueError(f"patch {patch_id} has no rollback backups")
        rollback_diffs: list[str] = []
        for backup in backups:
            if not isinstance(backup, dict):
                continue
            raw_path = str(backup.get("path") or "")
            previous = str(backup.get("content") or "")
            path = resolve_path(self.cwd, raw_path)
            current = path.read_text(encoding="utf-8", errors="replace")
            path.write_text(previous, encoding="utf-8")
            rollback_diffs.append(_unified_diff(current, previous, path.relative_to(self.cwd)))
        record["status"] = "rolled_back"
        record["rolled_back_at"] = time.time()
        record["rollback_diff"] = "\n\n".join(rollback_diffs)
        self._write_record(record)
        return f"Patch rolled back: {patch_id}\n\n{record['rollback_diff']}"

    def _path(self, patch_id: str) -> Path:
        if not patch_id or any(char in patch_id for char in "\\/.."):
            raise ValueError("invalid patch id")
        return self.dir / f"{patch_id}.json"

    def _read_record(self, patch_id: str) -> dict[str, Any]:
        path = self._path(patch_id)
        if not path.exists():
            raise FileNotFoundError(f"patch not found: {patch_id}")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"invalid patch record: {patch_id}")
        return data

    def _write_record(self, record: dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self._path(str(record["id"])).write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _read_all(self) -> list[dict[str, Any]]:
        if not self.dir.exists():
            return []
        records: list[dict[str, Any]] = []
        for path in self.dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(data, dict):
                records.append(data)
        return records


def _required_id(arguments: dict[str, Any]) -> str:
    patch_id = str(arguments.get("id") or "").strip()
    if not patch_id:
        raise ValueError("patch id is required")
    return patch_id


def _normalize_edits(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise ValueError("edits must be a non-empty list")
    edits: list[dict[str, str]] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"edit #{index} must be an object")
        raw_path = item.get("path")
        old = item.get("old_string", item.get("old"))
        new = item.get("new_string", item.get("new"))
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError(f"edit #{index} path is required")
        if not isinstance(old, str) or not old:
            raise ValueError(f"edit #{index} old_string is required")
        if not isinstance(new, str):
            raise ValueError(f"edit #{index} new_string is required")
        edits.append({"path": raw_path, "old_string": old, "new_string": new})
    return edits


def _unified_diff(before: str, after: str, rel: Path) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{rel.as_posix()}",
            tofile=f"b/{rel.as_posix()}",
        )
    ).rstrip()
