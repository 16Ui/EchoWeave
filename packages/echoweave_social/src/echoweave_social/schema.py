from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class EchoWeaveEvent:
    platform: str
    conversation_id: str
    sender_id: str
    text: str
    message_id: str | None = None
    reply_target_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EchoWeaveReply:
    text: str
    platform: str
    conversation_id: str
    target_id: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "platform": self.platform,
            "conversation_id": self.conversation_id,
            "target_id": self.target_id,
            "metadata": self.metadata,
        }
