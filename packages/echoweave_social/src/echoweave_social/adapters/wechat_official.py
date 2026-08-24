from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from typing import Any

from echoweave_social.schema import EchoWeaveEvent, EchoWeaveReply


class WeChatOfficialAdapter:
    """WeChat Official Account style XML adapter.

    This adapter handles plain-text XML callbacks. Encrypted WeCom/WeChat
    callbacks can be layered on top later by decrypting into the same dict
    shape before `event_from_payload`.
    """

    name = "wechat-official"

    def event_from_payload(self, payload: dict[str, Any]) -> EchoWeaveEvent:
        message = payload.get("xml") if isinstance(payload.get("xml"), dict) else payload
        from_user = str(message.get("FromUserName") or message.get("FromUserId") or "unknown")
        to_user = str(message.get("ToUserName") or "echoweave")
        msg_type = str(message.get("MsgType") or "text")
        content = str(message.get("Content") or "") if msg_type == "text" else f"[{msg_type} message]"
        return EchoWeaveEvent(
            platform=self.name,
            conversation_id=f"user:{from_user}",
            sender_id=from_user,
            text=content,
            message_id=_optional_str(message.get("MsgId") or message.get("MsgID")),
            reply_target_id=from_user,
            raw={"xml": message, "to_user": to_user},
        )

    def payload_from_reply(self, reply: EchoWeaveReply) -> dict[str, Any]:
        inbound = reply.metadata.get("event_raw") if isinstance(reply.metadata.get("event_raw"), dict) else {}
        xml = inbound.get("xml") if isinstance(inbound.get("xml"), dict) else {}
        from_user = reply.target_id
        to_user = str(xml.get("ToUserName") or "echoweave")
        body = render_text_reply_xml(to_user=from_user, from_user=to_user, content=reply.text)
        return {
            "ok": True,
            "adapter": self.name,
            "reply": reply.to_dict(),
            "wechat": {"msgtype": "text", "content": reply.text},
            "_raw_body": body,
            "_raw_content_type": "application/xml; charset=utf-8",
        }


def parse_wechat_xml(raw: bytes | str) -> dict[str, Any]:
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    root = ET.fromstring(text)
    return {"xml": {child.tag: child.text or "" for child in root}}


def render_text_reply_xml(*, to_user: str, from_user: str, content: str) -> str:
    return (
        "<xml>"
        f"<ToUserName><![CDATA[{to_user}]]></ToUserName>"
        f"<FromUserName><![CDATA[{from_user}]]></FromUserName>"
        f"<CreateTime>{int(time.time())}</CreateTime>"
        "<MsgType><![CDATA[text]]></MsgType>"
        f"<Content><![CDATA[{content}]]></Content>"
        "</xml>"
    )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
