from __future__ import annotations

import json

import pytest

from echoweave_runtime.events import (
    AgentEvent,
    Attachment,
    EventTypes,
    InboundMessage,
    OutboundMessage,
)
from echoweave_runtime.runtime.events import build_runtime_event
from echoweave_social.agent_schema import SocialMessage
from echoweave_social.event_bus import EventBus
from echoweave_social.schema import EchoWeaveEvent


def test_message_contract_round_trips_attachments_without_channel_fields() -> None:
    message = InboundMessage(
        platform="onebot-v11",
        conversation_id="group:42",
        sender_id="7",
        text="inspect this",
        message_id="m-1",
        attachments=(
            Attachment(
                kind="image",
                uri="channel://onebot/m-1/image-1",
                media_type="image/png",
                name="frame.png",
                size_bytes=128,
            ),
        ),
    )

    restored = InboundMessage.from_dict(message.to_dict())

    assert restored == message
    assert restored.channel == "onebot-v11"
    assert restored.conversation_key == "onebot-v11:group:42"


def test_message_contract_rejects_missing_routing_identity() -> None:
    with pytest.raises(ValueError, match="conversation_id"):
        InboundMessage(platform="web", conversation_id="", sender_id="user", text="hello")

    with pytest.raises(ValueError, match="target_id"):
        OutboundMessage(text="hello", platform="web", conversation_id="room", target_id="")


def test_agent_event_is_versioned_correlated_and_json_round_trippable() -> None:
    message = InboundMessage(
        platform="web",
        conversation_id="room",
        sender_id="user",
        text="hello",
        message_id="message-1",
    )
    event = AgentEvent.from_message(message, correlation_id="turn-1", sequence=3)

    restored = AgentEvent.from_dict(json.loads(event.to_json()))

    assert restored == event
    assert restored.type == EventTypes.MESSAGE_RECEIVED
    assert restored.source == "web"
    assert restored.conversation_id == "room"
    assert restored.correlation_id == "turn-1"
    assert restored.sequence == 3
    assert restored.schema_version == 1


def test_event_type_validation_prevents_unstable_wire_names() -> None:
    with pytest.raises(ValueError, match="invalid event type"):
        AgentEvent(type="Message Received", source="test")


def test_event_bus_separates_resume_cursor_from_global_event_identity() -> None:
    bus = EventBus(maxlen=2)
    first_value = AgentEvent(type=EventTypes.STREAM_DELTA, source="model", payload={"delta": "A"})
    first = bus.publish(first_value)
    second = bus.publish(EventTypes.STREAM_DELTA, {"delta": "B"}, source="model")

    assert first.id == 1
    assert first.value is first_value
    assert first.data["event_id"] == first_value.event_id
    assert second.id == 2
    assert second.value.event_id != first.value.event_id
    assert bus.snapshot_after(1) == [second]
    assert b"event: stream.delta" in second.to_sse()


def test_legacy_names_are_thin_views_over_the_canonical_contract() -> None:
    assert EchoWeaveEvent is InboundMessage

    legacy = SocialMessage(platform="web", session_id="room", sender_id="user", text="hello")
    assert isinstance(legacy, InboundMessage)
    assert legacy.conversation_id == "room"


def test_runtime_json_stream_uses_the_same_event_envelope() -> None:
    event = build_runtime_event("turn.started", "session-1", {"prompt_chars": 5})

    assert isinstance(event, AgentEvent)
    assert event.source == "agent-runtime"
    assert event.conversation_id == "session-1"
    assert json.loads(event.to_json())["schema_version"] == 1
