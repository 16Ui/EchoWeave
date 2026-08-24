from __future__ import annotations

from typing import Any, Protocol

from echoweave_runtime.events import InboundMessage, OutboundMessage


class PlatformAdapter(Protocol):
    name: str

    def event_from_payload(self, payload: dict[str, Any]) -> InboundMessage:
        """Convert a platform-specific inbound payload to a normalized event."""

    def payload_from_reply(self, reply: OutboundMessage) -> dict[str, Any]:
        """Convert a normalized reply to a platform-friendly outbound payload."""
