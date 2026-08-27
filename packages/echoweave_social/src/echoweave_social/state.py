from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any


class SocialStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._state = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"sessions": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"sessions": {}}
        if not isinstance(data, dict):
            return {"sessions": {}}
        sessions = data.get("sessions")
        if not isinstance(sessions, dict):
            data["sessions"] = {}
        return data

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self._state, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

    def session(self, conversation_key: str) -> dict[str, Any]:
        sessions = self._state.setdefault("sessions", {})
        if not isinstance(sessions, dict):
            sessions = {}
            self._state["sessions"] = sessions
        record = sessions.setdefault(conversation_key, {})
        if not isinstance(record, dict):
            record = {}
            sessions[conversation_key] = record
        return record

    def global_settings(self) -> dict[str, Any]:
        settings = self._state.setdefault("global", {})
        if not isinstance(settings, dict):
            settings = {}
            self._state["global"] = settings
        return settings

    def session_records(self) -> dict[str, dict[str, Any]]:
        """Return a detached projection for background inspection."""
        with self._lock:
            sessions = self._state.get("sessions")
            if not isinstance(sessions, dict):
                return {}
            return {
                str(key): dict(value)
                for key, value in sessions.items()
                if isinstance(key, str) and isinstance(value, dict)
            }

    def register_runtime_session(
        self,
        conversation_key: str,
        *,
        session_path: Path,
        session_id: str,
        workspace: Path,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Atomically register a generated or externally prepared runtime session."""
        with self._lock:
            record = self.session(conversation_key)
            record.update(
                {
                    "runtime_session": str(session_path.expanduser().resolve()),
                    "runtime_session_id": session_id,
                    "workspace": str(workspace.expanduser().resolve()),
                    "workspace_bound": True,
                }
            )
            if metadata:
                record.update(metadata)
            self.save()

    def approvals(self) -> dict[str, Any]:
        approvals = self._state.setdefault("approvals", {})
        if not isinstance(approvals, dict):
            approvals = {}
            self._state["approvals"] = approvals
        return approvals

    def approval(self, approval_id: str, *, timeout_seconds: int | None = None) -> dict[str, Any] | None:
        record = self.approvals().get(approval_id)
        if not isinstance(record, dict):
            return None
        self._expire_if_needed(approval_id, record, timeout_seconds)
        return record

    def save_approval(self, approval_id: str, record: dict[str, Any]) -> None:
        self.approvals()[approval_id] = record
        self.save()

    def list_pending_approvals(
        self,
        conversation_key: str | None = None,
        *,
        timeout_seconds: int | None = None,
    ) -> list[dict[str, Any]]:
        pending: list[dict[str, Any]] = []
        for approval_id, record in self.approvals().items():
            if not isinstance(record, dict):
                continue
            self._expire_if_needed(approval_id, record, timeout_seconds)
            if record.get("status") != "pending":
                continue
            if conversation_key is not None and record.get("conversation_key") != conversation_key:
                continue
            item = dict(record)
            item["id"] = approval_id
            pending.append(item)
        return sorted(pending, key=lambda item: str(item.get("created_at") or ""))

    def list_approvals(
        self,
        conversation_key: str | None = None,
        *,
        timeout_seconds: int | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for approval_id, record in self.approvals().items():
            if not isinstance(record, dict):
                continue
            self._expire_if_needed(approval_id, record, timeout_seconds)
            if conversation_key is not None and record.get("conversation_key") != conversation_key:
                continue
            item = dict(record)
            item["id"] = approval_id
            items.append(item)
        items.sort(key=lambda item: float(item.get("created_at") or 0), reverse=True)
        return items[:limit]

    def _expire_if_needed(
        self,
        approval_id: str,
        record: dict[str, Any],
        timeout_seconds: int | None,
    ) -> None:
        if not timeout_seconds or timeout_seconds <= 0:
            return
        if record.get("status") != "pending":
            return
        try:
            created_at = float(record.get("created_at") or 0)
        except (TypeError, ValueError):
            created_at = 0
        if created_at > 0 and time.time() - created_at > timeout_seconds:
            record["status"] = "expired"
            record["expired_at"] = time.time()
            self.save_approval(approval_id, record)

    def bind_workspace(self, conversation_key: str, workspace: Path) -> None:
        record = self.session(conversation_key)
        record["workspace"] = str(workspace.resolve())
        record["workspace_bound"] = True
        self.save()

    def unbind_workspace(self, conversation_key: str) -> None:
        record = self.session(conversation_key)
        record.pop("workspace", None)
        record.pop("workspace_bound", None)
        self.save()

    def reset_runtime_session(self, conversation_key: str) -> None:
        record = self.session(conversation_key)
        record.pop("runtime_session", None)
        record.pop("runtime_session_id", None)
        self.save()
