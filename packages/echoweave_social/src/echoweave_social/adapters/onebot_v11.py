from __future__ import annotations

from typing import Any

from echoweave_runtime.events import InboundMessage as EchoWeaveEvent
from echoweave_runtime.events import OutboundMessage as EchoWeaveReply


class OneBotV11Adapter:
    """OneBot v11 webhook adapter focused on text-first coding-agent demos."""

    name = "onebot-v11"

    def event_from_payload(self, payload: dict[str, Any]) -> EchoWeaveEvent:
        payload = _unwrap_payload(payload)
        message_type = str(payload.get("message_type") or "private")
        user_id = str(payload.get("user_id") or "unknown")
        group_id = payload.get("group_id")
        if message_type == "group" and group_id is not None:
            conversation_id = f"group:{group_id}"
            reply_target_id = str(group_id)
        else:
            conversation_id = f"private:{user_id}"
            reply_target_id = user_id

        return EchoWeaveEvent(
            platform=self.name,
            conversation_id=conversation_id,
            sender_id=user_id,
            text=_extract_text(payload),
            message_id=_optional_str(payload.get("message_id")),
            reply_target_id=reply_target_id,
            raw=payload,
        )

    def payload_from_reply(self, reply: EchoWeaveReply) -> dict[str, Any]:
        if reply.conversation_id.startswith("group:"):
            action = "send_group_msg"
            params = {"group_id": reply.target_id, "message": reply.text}
        else:
            action = "send_private_msg"
            params = {"user_id": reply.target_id, "message": reply.text}
        return {
            "ok": True,
            "adapter": self.name,
            "reply": reply.text,
            "echoweave_reply": reply.to_dict(),
            "echoweave_reply": reply.to_dict(),
            "auto_escape": False,
            "quick_operation": {
                "reply": reply.text,
                "auto_escape": False,
            },
            "onebot": {
                "action": action,
                "params": params,
            },
        }


def _extract_text(payload: dict[str, Any]) -> str:
    raw_message = payload.get("raw_message") or payload.get("raw_info")
    if isinstance(raw_message, str) and raw_message:
        return raw_message

    message = payload.get("message")
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        parts: list[str] = []
        for segment in message:
            if not isinstance(segment, dict):
                continue
            if segment.get("type") != "text":
                continue
            data = segment.get("data")
            if isinstance(data, dict):
                text = data.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts).strip()
    return ""


def _unwrap_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if _looks_like_onebot_event(payload):
        return payload
    for key in ("data", "event", "payload", "body"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            found = _find_onebot_event(nested)
            if found is not None:
                return found
    found = _find_onebot_event(payload)
    return found or payload


def _find_onebot_event(value: Any, depth: int = 0) -> dict[str, Any] | None:
    if depth > 4:
        return None
    if isinstance(value, dict):
        if _looks_like_onebot_event(value):
            return value
        for nested in value.values():
            found = _find_onebot_event(nested, depth + 1)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_onebot_event(item, depth + 1)
            if found is not None:
                return found
    return None


def _looks_like_onebot_event(value: dict[str, Any]) -> bool:
    return any(
        key in value
        for key in (
            "post_type",
            "message_type",
            "raw_message",
            "raw_info",
            "user_id",
            "group_id",
            "message_id",
        )
    )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
