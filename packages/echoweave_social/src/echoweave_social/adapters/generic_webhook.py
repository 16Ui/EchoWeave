from __future__ import annotations

from typing import Any

from echoweave_social.schema import EchoWeaveEvent, EchoWeaveReply


class GenericWebhookAdapter:
    name = "generic"

    def event_from_payload(self, payload: dict[str, Any]) -> EchoWeaveEvent:
        session_id = str(
            payload.get("session_id")
            or payload.get("conversation_id")
            or payload.get("chat_id")
            or "default"
        )
        sender_id = str(payload.get("sender_id") or payload.get("user_id") or "user")
        text = str(payload.get("text") or payload.get("message") or "")
        return EchoWeaveEvent(
            platform=str(payload.get("platform") or self.name),
            conversation_id=session_id,
            sender_id=sender_id,
            text=text,
            message_id=_optional_str(payload.get("message_id")),
            reply_target_id=session_id,
            raw=payload,
        )

    def payload_from_reply(self, reply: EchoWeaveReply) -> dict[str, Any]:
        return {
            "ok": True,
            "adapter": self.name,
            "reply": reply.to_dict(),
        }


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
