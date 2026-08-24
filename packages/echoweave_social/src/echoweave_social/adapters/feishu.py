from __future__ import annotations

import json
from typing import Any

from echoweave_runtime.events import InboundMessage as EchoWeaveEvent
from echoweave_runtime.events import OutboundMessage as EchoWeaveReply


class FeishuAdapter:
    """Feishu/Lark event-callback adapter.

    Supports URL verification and text-first `im.message.receive_v1` events.
    Actual active sending should use Feishu's message reply/create APIs; this
    adapter returns a ready-to-send action payload for the caller.
    """

    name = "feishu"

    def verification_response(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        challenge = payload.get("challenge")
        if isinstance(challenge, str) and (
            payload.get("type") == "url_verification" or payload.get("schema") == "2.0"
        ):
            return {"challenge": challenge}
        return None

    def event_from_payload(self, payload: dict[str, Any]) -> EchoWeaveEvent:
        event = _event_body(payload)
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}
        chat_id = str(message.get("chat_id") or event.get("chat_id") or "default")
        message_id = _optional_str(message.get("message_id") or event.get("message_id"))
        sender_id = _sender_id(sender) or _optional_str(event.get("sender_id")) or "unknown"
        return EchoWeaveEvent(
            platform=self.name,
            conversation_id=f"chat:{chat_id}",
            sender_id=sender_id,
            text=_message_text(message),
            message_id=message_id,
            reply_target_id=message_id or chat_id,
            raw=payload,
        )

    def payload_from_reply(self, reply: EchoWeaveReply) -> dict[str, Any]:
        content = json.dumps({"text": reply.text}, ensure_ascii=False)
        action = "reply_message" if reply.target_id and reply.target_id.startswith("om_") else "send_message"
        return {
            "ok": True,
            "adapter": self.name,
            "reply": reply.to_dict(),
            "feishu": {
                "action": action,
                "message_id": reply.target_id if action == "reply_message" else None,
                "receive_id": reply.target_id if action == "send_message" else None,
                "receive_id_type": "chat_id",
                "body": {"msg_type": "text", "content": content},
            },
        }


def _event_body(payload: dict[str, Any]) -> dict[str, Any]:
    event = payload.get("event")
    if isinstance(event, dict):
        return event
    header = payload.get("header")
    if isinstance(header, dict) and isinstance(payload.get("event"), dict):
        return payload["event"]
    return payload


def _message_text(message: dict[str, Any]) -> str:
    if not message:
        return ""
    if message.get("message_type") not in {None, "text", "post"}:
        return f"[{message.get('message_type')} message]"
    content = message.get("content")
    if isinstance(content, str):
        try:
            decoded = json.loads(content)
        except json.JSONDecodeError:
            return content
        if isinstance(decoded, dict):
            text = decoded.get("text")
            if isinstance(text, str):
                return text
            return _post_text(decoded)
    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            return text
        return _post_text(content)
    return ""


def _post_text(content: dict[str, Any]) -> str:
    parts: list[str] = []
    for value in content.values():
        if isinstance(value, list):
            _collect_post_parts(value, parts)
    return "".join(parts).strip()


def _collect_post_parts(value: list[Any], parts: list[str]) -> None:
    for item in value:
        if isinstance(item, list):
            _collect_post_parts(item, parts)
        elif isinstance(item, dict):
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)


def _sender_id(sender: dict[str, Any]) -> str | None:
    sender_id = sender.get("sender_id")
    if isinstance(sender_id, dict):
        for key in ("open_id", "union_id", "user_id"):
            value = sender_id.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
