from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib import request

from echoweave_runtime.events import OutboundMessage as EchoWeaveReply


@dataclass(frozen=True)
class OneBotSendResult:
    ok: bool
    action: str
    status: int | None = None
    response: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "action": self.action,
            "status": self.status,
            "response": self.response,
            "error": self.error,
        }


class OneBotHttpClient:
    """Minimal OneBot v11 HTTP API client for NapCat/Lagrange/go-cqhttp style endpoints."""

    def __init__(self, api_url: str, access_token: str | None = None, timeout: float = 10) -> None:
        self.api_url = api_url.rstrip("/")
        self.access_token = access_token
        self.timeout = timeout

    def send_reply(self, reply: EchoWeaveReply) -> OneBotSendResult:
        action, params = self._action_for(reply)
        try:
            status, body = self._post(action, params)
            return OneBotSendResult(
                ok=200 <= status < 300,
                action=action,
                status=status,
                response=body,
            )
        except Exception as exc:  # pragma: no cover - defensive network edge
            return OneBotSendResult(ok=False, action=action, error=str(exc))

    def _action_for(self, reply: EchoWeaveReply) -> tuple[str, dict[str, Any]]:
        if reply.conversation_id.startswith("group:"):
            return "send_group_msg", {"group_id": reply.target_id, "message": reply.text}
        return "send_private_msg", {"user_id": reply.target_id, "message": reply.text}

    def _post(self, action: str, params: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        payload = json.dumps(params, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            f"{self.api_url}/{action}",
            data=payload,
            method="POST",
            headers=self._headers(),
        )
        with request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
            raw = resp.read().decode("utf-8")
            if not raw:
                return resp.status, {}
            data = json.loads(raw)
            if not isinstance(data, dict):
                return resp.status, {"data": data}
            return resp.status, data

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers
