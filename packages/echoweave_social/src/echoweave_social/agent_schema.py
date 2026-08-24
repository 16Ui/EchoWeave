from __future__ import annotations

"""Compatibility constructors for the removed duplicate social DTOs."""

from typing import Any

from echoweave_runtime.events import InboundMessage, OutboundMessage


def SocialMessage(
    platform: str,
    session_id: str,
    sender_id: str,
    text: str,
    message_id: str | None = None,
    raw: dict[str, Any] | None = None,
) -> InboundMessage:
    return InboundMessage(
        platform=platform,
        conversation_id=session_id,
        sender_id=sender_id,
        text=text,
        message_id=message_id,
        raw=raw or {},
    )


def SocialReply(
    text: str,
    session_id: str,
    runtime_session_id: str | None = None,
    runtime_session_path: str | None = None,
    metadata: dict[str, Any] | None = None,
    *,
    platform: str = "social",
    target_id: str | None = None,
) -> OutboundMessage:
    return OutboundMessage(
        text=text,
        platform=platform,
        conversation_id=session_id,
        target_id=target_id or session_id,
        runtime_session_id=runtime_session_id,
        runtime_session_path=runtime_session_path,
        metadata=metadata or {},
    )


__all__ = ["SocialMessage", "SocialReply"]
