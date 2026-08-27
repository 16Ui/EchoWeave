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
from echoweave_runtime.lifecycle import LifecycleState, RuntimeHost
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
from echoweave_web.server import HttpServerComponent, HubWebhookServer
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


def _login_cookie(port: int, token: str = "secret") -> str:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(
        "POST",
        "/api/login",
        body=json.dumps({"token": token}),
        headers={"Content-Type": "application/json"},
    )
    response = conn.getresponse()
    response.read()
    cookie = response.getheader("Set-Cookie") or ""
    conn.close()
    assert response.status == 200
    assert "echoweave_session=" in cookie
    return cookie.split(";", 1)[0]


def _register_cookie(port: int, username: str = "admin", password: str = "password-123") -> str:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(
        "POST",
        "/api/register",
        body=json.dumps({"username": username, "password": password}),
        headers={"Content-Type": "application/json"},
    )
    response = conn.getresponse()
    body = response.read().decode("utf-8")
    cookie = response.getheader("Set-Cookie") or ""
    conn.close()
    assert response.status == 200, body
    assert "echoweave_session=" in cookie
    return cookie.split(";", 1)[0]


def test_web_auth_registers_and_persists_jwt_users() -> None:
    class DummyBackend:
        def handle(self, event: EchoWeaveEvent) -> EchoWeaveReply:
            return EchoWeaveReply(text="ok", platform=event.platform, conversation_id=event.conversation_id, target_id=event.conversation_id)

        def admin_status(self):
            return {"ok": True, "service": "EchoWeave"}

    with _local_tmp() as tmp:
        store = tmp / "users.json"
        server = HubWebhookServer(
            GenericWebhookAdapter(),
            DummyBackend(),
            webhook_token="secret",
            user_store_path=store,
        ).build_server("127.0.0.1", 0)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            cookie = _register_cookie(port)
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/api/status", headers={"Cookie": cookie})
            status = conn.getresponse()
            status.read()
            assert status.status == 200
            conn.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        server = HubWebhookServer(
            GenericWebhookAdapter(),
            DummyBackend(),
            webhook_token="secret",
            user_store_path=store,
        ).build_server("127.0.0.1", 0)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request(
                "POST",
                "/api/login",
                body=json.dumps({"username": "admin", "password": "password-123"}),
                headers={"Content-Type": "application/json"},
            )
            login = conn.getresponse()
            login.read()
            cookie = login.getheader("Set-Cookie") or ""
            assert login.status == 200
            assert "echoweave_session=" in cookie

            conn.request(
                "POST",
                "/api/login",
                body=json.dumps({"username": "admin", "password": "wrong-password"}),
                headers={"Content-Type": "application/json"},
            )
            wrong = conn.getresponse()
            wrong.read()
            assert wrong.status == 401
            conn.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


def test_webhook_health_and_token_auth() -> None:
    class DummyBackend:
        def handle(self, event: EchoWeaveEvent) -> EchoWeaveReply:
            return EchoWeaveReply(
                text=f"ok:{event.text}",
                platform=event.platform,
                conversation_id=event.conversation_id,
                target_id=event.reply_target_id or event.conversation_id,
            )

    gateway = HubWebhookServer(
        GenericWebhookAdapter(),
        DummyBackend(),
        webhook_token="secret",
    )
    web_component = HttpServerComponent(gateway, "127.0.0.1", 0)
    runtime = RuntimeHost().register(web_component)
    runtime.start()
    port = web_component.address[1]
    thread = threading.Thread(target=web_component.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/healthz")
        health = conn.getresponse()
        assert health.status == 200
        health.read()

        body = json.dumps({"session_id": "room", "sender_id": "user", "text": "hello"})
        conn.request("POST", "/", body=body, headers={"Content-Type": "application/json"})
        unauthorized = conn.getresponse()
        assert unauthorized.status == 401
        unauthorized.read()

        conn.request(
            "POST",
            "/",
            body=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer secret",
            },
        )
        authorized = conn.getresponse()
        payload = json.loads(authorized.read().decode("utf-8"))
        assert authorized.status == 200
        assert payload["reply"]["text"] == "ok:hello"

        conn.request("GET", "/?token=secret")
        query_token_panel = conn.getresponse()
        query_token_panel.read()
        assert query_token_panel.status == 302
        assert (query_token_panel.getheader("Location") or "").startswith("/login")
    finally:
        runtime.stop()
        thread.join(timeout=5)
        assert runtime.state is LifecycleState.STOPPED
        assert not thread.is_alive()
def test_webhook_admin_panel_and_approval_api() -> None:
    class DummyBackend:
        def handle(self, event: EchoWeaveEvent) -> EchoWeaveReply:
            return EchoWeaveReply(
                text=f"ok:{event.text}",
                platform=event.platform,
                conversation_id=event.conversation_id,
                target_id=event.reply_target_id or event.conversation_id,
            )

        def admin_status(self):
            return {
                "ok": True,
                "service": "EchoWeave",
                "recovery": self.recovery_status(),
                "approvals": {"pending": 1, "recent": []},
            }

        def recovery_status(self):
            return {
                "enabled": True,
                "backend_started": True,
                "running": True,
                "config": {"scan_interval_seconds": 30.0},
                "stats": {"in_flight": 1, "completed": 2, "failed": 0},
                "recent_results": [],
            }

        def scan_recovery(self, *, schedule=True):
            return {
                "ok": True,
                "enabled": True,
                "scanned_sessions": 3,
                "candidates": [{"turn_id": "orphan-1", "conversation_key": "web-user:room"}],
                "issues": [],
                "scheduled_scan": schedule,
            }

        def trace_overview(self, *, limit=50, event_limit_per_trace=120):
            return {
                "ok": True,
                "stats": {
                    "trace_count": 1,
                    "signal_count": 2,
                    "status_counts": {"completed": 1},
                },
                "traces": [
                    {
                        "trace_id": "trace-demo",
                        "turn_id": "turn-demo",
                        "conversation_key": "demo:run",
                        "status": "completed",
                        "attempt": 1,
                        "duration_ms": 12.5,
                        "event_count": 3,
                        "signal_count": 2,
                        "events": [
                            {
                                "type": "provider.retry_scheduled",
                                "category": "provider",
                                "status": "warning",
                                "title": "provider.retry_scheduled: demo",
                                "detail": "attempt=1",
                                "timestamp": "2026-01-01T00:00:00+00:00",
                            }
                        ],
                    }
                ][:limit],
                "issues": [],
                "event_limit": event_limit_per_trace,
                "requested_limit": limit,
            }

        def fault_eval_status(self):
            return {
                "ok": True,
                "available": True,
                "report": {
                    "run_id": "reliability-demo",
                    "passed": True,
                    "scenario_count": 4,
                    "passed_count": 4,
                    "overall_score": 1.0,
                    "scenarios": [],
                },
            }

        def run_reliability_demo(self):
            return {
                "ok": True,
                "conversation_key": "demo:reliability-demo",
                "report": self.fault_eval_status()["report"],
            }

        def list_approvals(self, limit: int = 50):
            return [{"id": "abc123", "status": "pending", "command": "python -m pip install"}]

        def audit_summary(self):
            return {"ok": True, "event_count": 2, "metrics": {"tool_call_success_rate": 1.0}, "suggestions": []}

        def generate_hardening_plan(self, *, feedback_log=None, eval_out=None):
            return {
                "ok": True,
                "feedback_written": 1 if feedback_log else 0,
                "eval_fixture_written": 1 if eval_out else 0,
            }

        def approve_approval(self, approval_id: str, *, actor_id: str = "web-admin"):
            return f"Approved {approval_id}"

        def admin_config(self):
            return {
                "provider": "demo",
                "model": None,
                "default_model_profile": "default",
                "ai_providers": {
                    "local-web": {
                        "type": "openai-compatible",
                        "base_url": "http://127.0.0.1:1234/v1",
                        "api_key_env": "LOCAL_LLM_API_KEY",
                        "default_model": "local-model",
                    }
                },
                "model_profiles": {"default": {"provider": "demo", "model": None}},
                "rag_enabled": False,
                "rag_backend": "pgvector_hybrid_bgem3",
                "rag_pgvector_table": "echoweave_rag_chunks",
                "rag_vector_weight": 0.65,
                "rag_bm25_weight": 0.35,
                "sandbox_root": "sandboxes",
                "approval_timeout_seconds": 3600,
                "orphan_recovery_enabled": True,
                "orphan_recovery_scan_interval_seconds": 30.0,
                "orphan_recovery_max_per_scan": 4,
                "orphan_recovery_max_attempts_per_turn": 3,
                "global_enabled_skills": [],
                "admins": [],
            }

        def web_capabilities(self, *, platform="web-user", conversation_id="web-coding", sender_id="web-admin"):
            return {
                "ok": True,
                "models": {
                    "current": "default",
                    "profiles": {"default": {"provider": "demo", "model": None}},
                },
                "rag": {
                    "enabled": False,
                    "backend": "pgvector_hybrid_bgem3",
                    "pgvector_configured": False,
                    "query_rewrite_enabled": False,
                    "rerank_enabled": False,
                },
                "skills": [{"name": "search_workspace", "description": "Search files", "enabled": True}],
            }

        def update_admin_config(self, patch):
            updated = self.admin_config()
            updated.update(patch)
            return updated

    server = HubWebhookServer(
        GenericWebhookAdapter(),
        DummyBackend(),
        webhook_token="secret",
    ).build_server("127.0.0.1", 0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        unauthorized = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        unauthorized.request("GET", "/api/recovery")
        unauthorized_get = unauthorized.getresponse()
        unauthorized_get.read()
        assert unauthorized_get.status == 401
        unauthorized.request("POST", "/api/recovery/scan", body="{}")
        unauthorized_post = unauthorized.getresponse()
        unauthorized_post.read()
        assert unauthorized_post.status == 401
        unauthorized.request("GET", "/api/traces")
        unauthorized_traces = unauthorized.getresponse()
        unauthorized_traces.read()
        assert unauthorized_traces.status == 401
        unauthorized.request("POST", "/api/demos/reliability", body="{}")
        unauthorized_demo = unauthorized.getresponse()
        unauthorized_demo.read()
        assert unauthorized_demo.status == 401
        unauthorized.close()

        cookie = _login_cookie(port)
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/", headers={"Cookie": cookie})
        user_panel = conn.getresponse()
        user_panel_body = user_panel.read().decode("utf-8")
        assert user_panel.status == 200
        assert "EchoWeave AI Coding 用户端" in user_panel_body
        assert "/api/command" in user_panel_body
        assert "model-select" in user_panel_body
        assert "skills-list" in user_panel_body
        assert "rag-toggle" in user_panel_body
        assert "事件流" in user_panel_body
        assert "/events" in user_panel_body

        conn.request("GET", "/admin", headers={"Cookie": cookie})
        panel = conn.getresponse()
        panel_body = panel.read().decode("utf-8")
        assert panel.status == 200
        assert "EchoWeave Admin 管理端" in panel_body
        assert "模型与 API Key" in panel_body
        assert "cfg-ai-providers" in panel_body
        assert "openJsonEditor('model_profiles')" in panel_body
        assert "json-modal" in panel_body
        assert "data-tip=\"新会话默认使用的模型配置" in panel_body
        assert "data-field=\"api_key\"" in panel_body
        assert "配置模型与密钥" in panel_body
        assert "故障恢复" in panel_body
        assert "cfg-orphan-recovery-enabled" in panel_body
        assert "/api/recovery/scan" in panel_body
        assert "Trace 与可靠性证据" in panel_body
        assert "trace-detail" in panel_body
        assert "/api/traces" in panel_body
        assert "/api/demos/reliability" in panel_body

        conn.request("GET", "/api/status", headers={"Cookie": cookie})
        status = conn.getresponse()
        status_payload = json.loads(status.read().decode("utf-8"))
        assert status.status == 200
        assert status_payload["approvals"]["pending"] == 1
        assert status_payload["recovery"]["stats"]["completed"] == 2

        conn.request("GET", "/api/recovery", headers={"Cookie": cookie})
        recovery = conn.getresponse()
        recovery_payload = json.loads(recovery.read().decode("utf-8"))
        assert recovery.status == 200
        assert recovery_payload["recovery"]["running"] is True
        assert recovery_payload["recovery"]["stats"]["in_flight"] == 1

        conn.request("POST", "/api/recovery/scan", body="{}", headers={"Cookie": cookie})
        recovery_scan = conn.getresponse()
        recovery_scan_payload = json.loads(recovery_scan.read().decode("utf-8"))
        assert recovery_scan.status == 200
        assert recovery_scan_payload["scheduled_scan"] is True
        assert recovery_scan_payload["candidates"][0]["turn_id"] == "orphan-1"

        conn.request("GET", "/api/traces?limit=10&event_limit=40", headers={"Cookie": cookie})
        traces = conn.getresponse()
        traces_payload = json.loads(traces.read().decode("utf-8"))
        assert traces.status == 200
        assert traces_payload["stats"]["trace_count"] == 1
        assert traces_payload["traces"][0]["trace_id"] == "trace-demo"
        assert traces_payload["event_limit"] == 40

        conn.request("GET", "/api/traces?limit=bad&event_limit=9999", headers={"Cookie": cookie})
        bounded_traces = conn.getresponse()
        bounded_payload = json.loads(bounded_traces.read().decode("utf-8"))
        assert bounded_traces.status == 200
        assert bounded_payload["requested_limit"] == 50
        assert bounded_payload["event_limit"] == 500

        conn.request("GET", "/api/evals/fault/latest", headers={"Cookie": cookie})
        fault_eval = conn.getresponse()
        fault_eval_payload = json.loads(fault_eval.read().decode("utf-8"))
        assert fault_eval.status == 200
        assert fault_eval_payload["report"]["overall_score"] == 1.0

        conn.request("POST", "/api/demos/reliability", body="{}", headers={"Cookie": cookie})
        demo = conn.getresponse()
        demo_payload = json.loads(demo.read().decode("utf-8"))
        assert demo.status == 200
        assert demo_payload["report"]["passed_count"] == 4

        conn.request("GET", "/api/approvals", headers={"Cookie": cookie})
        approvals = conn.getresponse()
        approvals_payload = json.loads(approvals.read().decode("utf-8"))
        assert approvals.status == 200
        assert approvals_payload["approvals"][0]["id"] == "abc123"

        conn.request("GET", "/api/audit", headers={"Cookie": cookie})
        audit = conn.getresponse()
        audit_payload = json.loads(audit.read().decode("utf-8"))
        assert audit.status == 200
        assert audit_payload["event_count"] == 2

        conn.request("GET", "/api/capabilities?conversation_id=web-coding", headers={"Cookie": cookie})
        capabilities = conn.getresponse()
        capabilities_payload = json.loads(capabilities.read().decode("utf-8"))
        assert capabilities.status == 200
        assert capabilities_payload["models"]["profiles"]["default"]["provider"] == "demo"
        assert capabilities_payload["skills"][0]["name"] == "search_workspace"

        conn.request("GET", "/api/config", headers={"Cookie": cookie})
        config = conn.getresponse()
        config_payload = json.loads(config.read().decode("utf-8"))
        assert config.status == 200
        assert config_payload["config"]["provider"] == "demo"
        assert config_payload["config"]["ai_providers"]["local-web"]["default_model"] == "local-model"

        conn.request(
            "POST",
            "/api/config",
            body=json.dumps(
                {
                    "provider": "deepseek",
                    "model": "deepseek-chat",
                    "ai_providers": {
                        "local-web": {
                            "type": "openai-compatible",
                            "base_url": "http://127.0.0.1:1234/v1",
                            "api_key_env": "LOCAL_LLM_API_KEY",
                            "default_model": "local-model",
                        }
                    },
                    "model_profiles": {"local": {"provider": "local-web", "model": "local-model"}},
                }
            ),
            headers={"Content-Type": "application/json", "Cookie": cookie},
        )
        updated_config = conn.getresponse()
        updated_payload = json.loads(updated_config.read().decode("utf-8"))
        assert updated_config.status == 200
        assert updated_payload["config"]["provider"] == "deepseek"
        assert updated_payload["config"]["ai_providers"]["local-web"]["base_url"] == "http://127.0.0.1:1234/v1"
        assert updated_payload["config"]["model_profiles"]["local"]["provider"] == "local-web"

        conn.request(
            "POST",
            "/api/command",
            body=json.dumps(
                {
                    "platform": "web-admin",
                    "conversation_id": "web-session",
                    "sender_id": "web-admin",
                    "text": "/status",
                }
            ),
            headers={"Content-Type": "application/json", "Cookie": cookie},
        )
        command = conn.getresponse()
        command_payload = json.loads(command.read().decode("utf-8"))
        assert command.status == 200
        assert command_payload["reply"]["text"] == "ok:/status"
        assert command_payload["reply"]["conversation_id"] == "web-session"

        conn.request("POST", "/api/approvals/abc123/approve", body="{}", headers={"Cookie": cookie})
        approved = conn.getresponse()
        approved_payload = json.loads(approved.read().decode("utf-8"))
        assert approved.status == 200
        assert approved_payload["result"] == "Approved abc123"

        conn.request(
            "POST",
            "/api/hardening",
            body=json.dumps({"feedback_log": "feedback.jsonl", "eval_out": "eval.json"}),
            headers={"Content-Type": "application/json", "Cookie": cookie},
        )
        hardening = conn.getresponse()
        hardening_payload = json.loads(hardening.read().decode("utf-8"))
        assert hardening.status == 200
        assert hardening_payload["feedback_written"] == 1
        assert hardening_payload["eval_fixture_written"] == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
def test_webhook_handles_feishu_verification() -> None:
    class DummyBackend:
        def handle(self, event: EchoWeaveEvent) -> EchoWeaveReply:
            return EchoWeaveReply(text="unused", platform=event.platform, conversation_id=event.conversation_id, target_id=event.reply_target_id or event.conversation_id)

    server = HubWebhookServer(FeishuAdapter(), DummyBackend(), webhook_token="secret").build_server("127.0.0.1", 0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        body = json.dumps({"type": "url_verification", "challenge": "challenge-token"})
        conn.request(
            "POST",
            "/",
            body=body,
            headers={"Content-Type": "application/json", "Authorization": "Bearer secret"},
        )
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))

        assert response.status == 200
        assert payload == {"challenge": "challenge-token"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
def test_webhook_handles_wechat_xml_sync_reply_and_get_verify() -> None:
    class DummyBackend:
        def handle(self, event: EchoWeaveEvent) -> EchoWeaveReply:
            return EchoWeaveReply(
                text=f"ok:{event.text}",
                platform=event.platform,
                conversation_id=event.conversation_id,
                target_id=event.reply_target_id or event.conversation_id,
                metadata={"event_raw": event.raw},
            )

    server = HubWebhookServer(WeChatOfficialAdapter(), DummyBackend(), webhook_token="secret").build_server("127.0.0.1", 0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/?token=secret&echostr=hello-wechat")
        verify = conn.getresponse()
        verify_body = verify.read().decode("utf-8")
        assert verify.status == 200
        assert verify_body == "hello-wechat"

        body = (
            "<xml>"
            "<ToUserName><![CDATA[gh_bot]]></ToUserName>"
            "<FromUserName><![CDATA[openid_user]]></FromUserName>"
            "<MsgType><![CDATA[text]]></MsgType>"
            "<Content><![CDATA[/status]]></Content>"
            "<MsgId>100</MsgId>"
            "</xml>"
        )
        conn.request(
            "POST",
            "/",
            body=body,
            headers={"Content-Type": "application/xml", "Authorization": "Bearer secret"},
        )
        response = conn.getresponse()
        response_body = response.read().decode("utf-8")

        assert response.status == 200
        assert "<ToUserName><![CDATA[openid_user]]></ToUserName>" in response_body
        assert "<Content><![CDATA[ok:/status]]></Content>" in response_body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
def test_read_chunked_body() -> None:
    body = b'{"raw_message":"hello chunked"}'
    chunked = b"%X\r\n" % len(body) + body + b"\r\n0\r\n\r\n"

    assert _read_chunked_body(io.BytesIO(chunked)) == body
