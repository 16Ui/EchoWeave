from __future__ import annotations

from typing import Any

from echoweave_runtime.events import InboundMessage as EchoWeaveEvent
from echoweave_runtime.events import OutboundMessage as EchoWeaveReply


class AstrBotEventAdapter:
    """Adapter for AstrBot-shaped event payloads without importing AstrBot itself."""

    name = "astrbot"

    def event_from_astrbot_event(self, event: Any) -> EchoWeaveEvent:
        message_obj = getattr(event, "message_obj", None)
        return self.event_from_payload(
            {
                "unified_msg_origin": getattr(event, "unified_msg_origin", None),
                "session_id": getattr(event, "session_id", None),
                "message_str": getattr(event, "message_str", None),
                "message_obj": message_obj,
                "platform": _nested_get(getattr(event, "platform_meta", None), "id"),
            }
        )

    def event_from_payload(self, payload: dict[str, Any]) -> EchoWeaveEvent:
        message_obj = payload.get("message_obj")
        unified_msg_origin = _optional_str(
            payload.get("unified_msg_origin") or payload.get("umo")
        )
        platform_id = _platform_from_umo(unified_msg_origin) or _optional_str(
            payload.get("platform")
            or payload.get("platform_id")
            or payload.get("platform_name")
        )
        conversation_id = unified_msg_origin or _optional_str(
            payload.get("session_id") or _nested_get(message_obj, "session_id")
        )
        sender = _nested_get(message_obj, "sender")
        sender_id = _optional_str(
            payload.get("sender_id")
            or payload.get("user_id")
            or _nested_get(sender, "user_id")
        )
        text = _optional_str(
            payload.get("message_str")
            or payload.get("text")
            or payload.get("message")
            or _nested_get(message_obj, "message_str")
        )
        message_id = _optional_str(
            payload.get("message_id") or _nested_get(message_obj, "message_id")
        )
        return EchoWeaveEvent(
            platform=f"astrbot:{platform_id or 'unknown'}",
            conversation_id=conversation_id or "astrbot:unknown:default",
            sender_id=sender_id or "unknown",
            text=text or "",
            message_id=message_id,
            reply_target_id=conversation_id,
            raw=payload,
        )

    def payload_from_reply(self, reply: EchoWeaveReply) -> dict[str, Any]:
        return {
            "ok": True,
            "adapter": self.name,
            "reply": reply.to_dict(),
            "astrbot": {
                "unified_msg_origin": reply.conversation_id,
                "message_str": reply.text,
            },
        }


def _nested_get(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _platform_from_umo(unified_msg_origin: str | None) -> str | None:
    if not unified_msg_origin:
        return None
    return unified_msg_origin.split(":", 1)[0]
