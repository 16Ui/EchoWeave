from __future__ import annotations

import http.client
import io
import json
import shutil
import threading
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from echoweave_agent_core import AgentCore, AgentCoreConfig, AgentCoreHookBase, CoreTurnContext, SessionRuntimeFacade, TurnRequest, TurnResult
from echoweave_coding_agent import CodingAgent, CodingAgentConfig
from echoweave_harness.audit import configure_audit, read_audit_events
from echoweave_harness.feedback import suggest_harness_improvements, write_feedback_backlog
from echoweave_harness.metrics import compute_harness_metrics
from echoweave_harness.policy import configure_harness_policy
from echoweave_runtime.app import build_registry
from echoweave_runtime.extensions.base import RetrievalChunk
from echoweave_runtime.extensions.hybrid_rag_provider import HybridRagProviderConfig, HybridRagRetrievalProvider
from echoweave_runtime.rag.pipeline import Bm25Reranker, LocalMultiQueryRewriter
from echoweave_runtime.rag.model import RagSearchOptions
from echoweave_runtime.rag.pgvector_hybrid import PgVectorHybridConfig, PgVectorHybridRagModel, collect_chunks
from echoweave_runtime.models.demo import AgentResponse, SequenceModelClient, tool_response
from echoweave_runtime.session.store import SessionStore
from echoweave_social.adapters.astrbot_event import AstrBotEventAdapter
from echoweave_social.adapters.feishu import FeishuAdapter
from echoweave_social.adapters.generic_webhook import GenericWebhookAdapter
from echoweave_social.adapters.onebot_v11 import OneBotV11Adapter
from echoweave_social.adapters.wechat_official import WeChatOfficialAdapter
from echoweave_social.agent_runtime import SocialAgentConfig, EchoWeaveSocialAgent
from echoweave_social.agent_schema import SocialMessage
from echoweave_social.backend import EchoWeaveBackend, EchoWeaveBackendConfig
from echoweave_web.cli import app
from echoweave_social.config import EchoWeaveConfig
from echoweave_web.server import HubWebhookServer
from echoweave_web.server import _read_chunked_body
from echoweave_social.onebot_client import OneBotHttpClient
from echoweave_social.schema import EchoWeaveEvent, EchoWeaveReply


@contextmanager
def _local_tmp():
    path = Path.cwd() / ".test-data" / uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_onebot_adapter_maps_group_payload_and_reply_action() -> None:
    adapter = OneBotV11Adapter()

    event = adapter.event_from_payload(
        {
            "message_type": "group",
            "group_id": 123,
            "user_id": 456,
            "message_id": 789,
            "message": [{"type": "text", "data": {"text": "/status"}}],
        }
    )
    reply = adapter.payload_from_reply(
        reply=EchoWeaveReply(
            text="ok",
            platform=event.platform,
            conversation_id=event.conversation_id,
            target_id=event.reply_target_id or event.conversation_id,
        )
    )

    assert event.platform == "onebot-v11"
    assert event.conversation_id == "group:123"
    assert event.sender_id == "456"
    assert event.text == "/status"
    assert reply["reply"] == "ok"
    assert reply["auto_escape"] is False
    assert reply["onebot"]["action"] == "send_group_msg"
    assert reply["onebot"]["params"]["group_id"] == "123"
def test_onebot_adapter_unwraps_nested_payload() -> None:
    adapter = OneBotV11Adapter()

    event = adapter.event_from_payload(
        {
            "type": "wrapped",
            "data": {
                "post_type": "message",
                "message_type": "private",
                "user_id": 2488945471,
                "raw_message": "你是",
            },
        }
    )

    assert event.conversation_id == "private:2488945471"
    assert event.sender_id == "2488945471"
    assert event.text == "你是"
def test_astrbot_adapter_maps_unified_message_origin() -> None:
    adapter = AstrBotEventAdapter()

    event = adapter.event_from_payload(
        {
            "unified_msg_origin": "aiocqhttp:GroupMessage:123",
            "message_str": "/status",
            "sender_id": "456",
            "message_id": "789",
        }
    )
    outbound = adapter.payload_from_reply(
        EchoWeaveReply(
            text="ok",
            platform=event.platform,
            conversation_id=event.conversation_id,
            target_id=event.reply_target_id or event.conversation_id,
        )
    )

    assert event.platform == "astrbot:aiocqhttp"
    assert event.conversation_id == "aiocqhttp:GroupMessage:123"
    assert event.text == "/status"
    assert outbound["astrbot"]["unified_msg_origin"] == "aiocqhttp:GroupMessage:123"
def test_feishu_adapter_maps_message_receive_and_verification() -> None:
    adapter = FeishuAdapter()
    verification = adapter.verification_response({"type": "url_verification", "challenge": "abc"})
    event = adapter.event_from_payload(
        {
            "schema": "2.0",
            "header": {"event_type": "im.message.receive_v1"},
            "event": {
                "sender": {"sender_id": {"open_id": "ou_123"}},
                "message": {
                    "message_id": "om_456",
                    "chat_id": "oc_789",
                    "message_type": "text",
                    "content": json.dumps({"text": "/status"}, ensure_ascii=False),
                },
            },
        }
    )
    outbound = adapter.payload_from_reply(
        EchoWeaveReply(
            text="ok",
            platform=event.platform,
            conversation_id=event.conversation_id,
            target_id=event.reply_target_id or event.conversation_id,
        )
    )

    assert verification == {"challenge": "abc"}
    assert event.platform == "feishu"
    assert event.conversation_id == "chat:oc_789"
    assert event.sender_id == "ou_123"
    assert event.text == "/status"
    assert outbound["feishu"]["action"] == "reply_message"
    assert outbound["feishu"]["message_id"] == "om_456"
def test_wechat_official_adapter_maps_xml_and_renders_reply() -> None:
    adapter = WeChatOfficialAdapter()
    event = adapter.event_from_payload(
        {
            "xml": {
                "ToUserName": "gh_bot",
                "FromUserName": "openid_user",
                "MsgType": "text",
                "Content": "你好",
                "MsgId": "100",
            }
        }
    )
    outbound = adapter.payload_from_reply(
        EchoWeaveReply(
            text="ok",
            platform=event.platform,
            conversation_id=event.conversation_id,
            target_id=event.reply_target_id or event.conversation_id,
            metadata={"event_raw": event.raw},
        )
    )

    assert event.platform == "wechat-official"
    assert event.conversation_id == "user:openid_user"
    assert event.sender_id == "openid_user"
    assert event.text == "你好"
    assert "<ToUserName><![CDATA[openid_user]]></ToUserName>" in outbound["_raw_body"]
    assert "<FromUserName><![CDATA[gh_bot]]></FromUserName>" in outbound["_raw_body"]
    assert "<Content><![CDATA[ok]]></Content>" in outbound["_raw_body"]
def test_onebot_client_builds_group_and_private_actions() -> None:
    client = OneBotHttpClient("http://127.0.0.1:3000", "token")

    group_action, group_params = client._action_for(
        EchoWeaveReply(
            text="group ok",
            platform="onebot-v11",
            conversation_id="group:123",
            target_id="123",
        )
    )
    private_action, private_params = client._action_for(
        EchoWeaveReply(
            text="private ok",
            platform="onebot-v11",
            conversation_id="private:456",
            target_id="456",
        )
    )

    assert group_action == "send_group_msg"
    assert group_params == {"group_id": "123", "message": "group ok"}
    assert private_action == "send_private_msg"
    assert private_params == {"user_id": "456", "message": "private ok"}
    assert client._headers()["Authorization"] == "Bearer token"
