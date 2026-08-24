from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, ClassVar
from uuid import uuid4


_EVENT_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class Attachment:
    """Channel-neutral attachment reference; bytes stay in channel-owned storage."""

    kind: str
    uri: str
    media_type: str | None = None
    name: str | None = None
    size_bytes: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.kind.strip():
            raise ValueError("attachment kind must not be empty")
        if not self.uri.strip():
            raise ValueError("attachment uri must not be empty")
        if self.size_bytes is not None and self.size_bytes < 0:
            raise ValueError("attachment size_bytes must not be negative")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Attachment":
        return cls(
            kind=str(value.get("kind") or "file"),
            uri=str(value.get("uri") or ""),
            media_type=_optional_text(value.get("media_type")),
            name=_optional_text(value.get("name")),
            size_bytes=_optional_int(value.get("size_bytes")),
            metadata=_dict_value(value.get("metadata")),
        )


@dataclass(frozen=True, slots=True)
class InboundMessage:
    """The single message contract accepted by channels and the Agent facade."""

    platform: str
    conversation_id: str
    sender_id: str
    text: str = ""
    message_id: str | None = None
    reply_target_id: str | None = None
    attachments: tuple[Attachment, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_identity("platform", self.platform)
        _require_identity("conversation_id", self.conversation_id)
        _require_identity("sender_id", self.sender_id)
        object.__setattr__(self, "attachments", tuple(self.attachments))

    @property
    def channel(self) -> str:
        return self.platform

    @property
    def session_id(self) -> str:
        """Compatibility view; runtime code should use conversation_id."""
        return self.conversation_id

    @property
    def conversation_key(self) -> str:
        return f"{self.platform}:{self.conversation_id}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "InboundMessage":
        attachments = value.get("attachments") or ()
        return cls(
            platform=str(value.get("platform") or value.get("channel") or ""),
            conversation_id=str(value.get("conversation_id") or value.get("session_id") or ""),
            sender_id=str(value.get("sender_id") or ""),
            text=str(value.get("text") or ""),
            message_id=_optional_text(value.get("message_id")),
            reply_target_id=_optional_text(value.get("reply_target_id")),
            attachments=tuple(
                item if isinstance(item, Attachment) else Attachment.from_dict(item)
                for item in attachments
                if isinstance(item, (Attachment, dict))
            ),
            raw=_dict_value(value.get("raw")),
        )


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    """The single channel-neutral response contract returned by the Agent facade."""

    text: str
    platform: str
    conversation_id: str
    target_id: str
    attachments: tuple[Attachment, ...] = ()
    runtime_session_id: str | None = None
    runtime_session_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_identity("platform", self.platform)
        _require_identity("conversation_id", self.conversation_id)
        _require_identity("target_id", self.target_id)
        object.__setattr__(self, "attachments", tuple(self.attachments))

    @property
    def channel(self) -> str:
        return self.platform

    @property
    def session_id(self) -> str:
        return self.conversation_id

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EventTypes:
    MESSAGE_RECEIVED: ClassVar[str] = "message.inbound"
    MESSAGE_PRODUCED: ClassVar[str] = "message.reply"
    STREAM_DELTA: ClassVar[str] = "stream.delta"
    TOOL_CALL_STARTED: ClassVar[str] = "tool.call.started"
    TOOL_CALL_FINISHED: ClassVar[str] = "tool.call.finished"
    RUN_FAILED: ClassVar[str] = "message.error"
    RUN_CANCELLED: ClassVar[str] = "run.cancelled"


@dataclass(frozen=True, slots=True)
class AgentEvent:
    """Versioned envelope shared by Web SSE, channels and runtime observers."""

    type: str
    source: str
    payload: dict[str, Any] = field(default_factory=dict)
    conversation_id: str | None = None
    correlation_id: str | None = None
    sequence: int | None = None
    event_id: str = field(default_factory=lambda: uuid4().hex)
    timestamp: str = field(default_factory=utc_now_iso)
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not _EVENT_TYPE_PATTERN.fullmatch(self.type):
            raise ValueError(f"invalid event type: {self.type!r}")
        _require_identity("source", self.source)
        if self.sequence is not None and self.sequence < 0:
            raise ValueError("event sequence must not be negative")
        if self.schema_version < 1:
            raise ValueError("event schema_version must be positive")
        object.__setattr__(self, "payload", _json_object(self.payload))

    @classmethod
    def from_message(
        cls,
        message: InboundMessage | OutboundMessage,
        *,
        correlation_id: str | None = None,
        sequence: int | None = None,
    ) -> "AgentEvent":
        event_type = (
            EventTypes.MESSAGE_RECEIVED
            if isinstance(message, InboundMessage)
            else EventTypes.MESSAGE_PRODUCED
        )
        return cls(
            type=event_type,
            source=message.platform,
            conversation_id=message.conversation_id,
            correlation_id=correlation_id,
            sequence=sequence,
            payload=message.to_dict(),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AgentEvent":
        return cls(
            type=str(value.get("type") or ""),
            source=str(value.get("source") or ""),
            payload=_dict_value(value.get("payload")),
            conversation_id=_optional_text(value.get("conversation_id")),
            correlation_id=_optional_text(value.get("correlation_id")),
            sequence=_optional_int(value.get("sequence")),
            event_id=str(value.get("event_id") or uuid4().hex),
            timestamp=str(value.get("timestamp") or utc_now_iso()),
            schema_version=int(value.get("schema_version") or 1),
        )


def _require_identity(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    return int(value)


def _dict_value(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _json_object(value: dict[str, Any]) -> dict[str, Any]:
    normalized = _json_value(value)
    return normalized if isinstance(normalized, dict) else {}


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return str(value)


__all__ = [
    "AgentEvent",
    "Attachment",
    "EventTypes",
    "InboundMessage",
    "OutboundMessage",
    "utc_now_iso",
]
