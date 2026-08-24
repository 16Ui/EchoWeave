from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SocialMessage:
    platform: str
    session_id: str
    sender_id: str
    text: str
    message_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def conversation_key(self) -> str:
        return f"{self.platform}:{self.session_id}"


@dataclass(frozen=True)
class SocialReply:
    text: str
    session_id: str
    runtime_session_id: str | None = None
    runtime_session_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "session_id": self.session_id,
            "runtime_session_id": self.runtime_session_id,
            "runtime_session_path": self.runtime_session_path,
            "metadata": self.metadata,
        }
