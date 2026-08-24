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


def test_generic_webhook_routes_to_echoweave_backend() -> None:
    with _local_tmp() as tmp_path:
        adapter = GenericWebhookAdapter()
        sandbox_root = tmp_path / "sandboxes"
        backend = EchoWeaveBackend(
            EchoWeaveBackendConfig(
                default_workspace=tmp_path / "real-workspace",
                state_path=tmp_path / "state.json",
                sandbox_root=sandbox_root,
            )
        )

        event = adapter.event_from_payload(
            {
                "platform": "wechat-demo",
                "session_id": "room-1",
                "sender_id": "user-1",
                "text": "hello hub",
            }
        )
        reply = backend.handle(event)
        outbound = adapter.payload_from_reply(reply)

        assert reply.text == "echo: hello hub"
        assert outbound["ok"] is True
        assert outbound["reply"]["metadata"]["runtime_session_id"]
        assert outbound["reply"]["metadata"]["workspace"].startswith(str(sandbox_root))
def test_unbound_conversations_get_isolated_sandboxes() -> None:
    with _local_tmp() as tmp_path:
        real_workspace = tmp_path / "real-workspace"
        real_workspace.mkdir()
        (real_workspace / "secret.txt").write_text("do not scan me", encoding="utf-8")
        sandbox_root = tmp_path / "sandboxes"
        backend = EchoWeaveBackend(
            EchoWeaveBackendConfig(
                default_workspace=real_workspace,
                state_path=tmp_path / "state.json",
                sandbox_root=sandbox_root,
            )
        )

        first = backend.handle(
            EchoWeaveEvent(
                platform="onebot-v11",
                conversation_id="private:1",
                sender_id="1",
                text="/status",
            )
        )
        second = backend.handle(
            EchoWeaveEvent(
                platform="onebot-v11",
                conversation_id="private:2",
                sender_id="2",
                text="/status",
            )
        )

        assert "workspace_mode: sandbox" in first.text
        assert "workspace_mode: sandbox" in second.text
        assert str(sandbox_root) in first.text
        assert str(sandbox_root) in second.text
        assert str(real_workspace) not in first.text
        assert str(real_workspace) not in second.text
        assert first.text != second.text
def test_model_profiles_and_rag_are_conversation_scoped() -> None:
    with _local_tmp() as tmp_path:
        backend = EchoWeaveBackend(
            EchoWeaveBackendConfig(
                default_workspace=tmp_path,
                state_path=tmp_path / "state.json",
                sandbox_root=tmp_path / "sandboxes",
                provider="demo",
                model_profiles={
                    "fast": {"provider": "demo", "model": None},
                    "deepseek": {"provider": "deepseek", "model": "deepseek-chat"},
                },
                default_model_profile="fast",
                rag_enabled=False,
            )
        )

        models = backend.handle(
            EchoWeaveEvent(platform="onebot-v11", conversation_id="private:1", sender_id="1", text="/models")
        )
        switched = backend.handle(
            EchoWeaveEvent(platform="onebot-v11", conversation_id="private:1", sender_id="1", text="/model deepseek")
        )
        rag_on = backend.handle(
            EchoWeaveEvent(platform="onebot-v11", conversation_id="private:1", sender_id="1", text="/rag on")
        )
        status = backend.handle(
            EchoWeaveEvent(platform="onebot-v11", conversation_id="private:1", sender_id="1", text="/status")
        )
        other_status = backend.handle(
            EchoWeaveEvent(platform="onebot-v11", conversation_id="private:2", sender_id="2", text="/status")
        )

        assert "fast: demo/(default)" in models.text
        assert "deepseek: deepseek/deepseek-chat" in models.text
        assert "Model profile switched to deepseek" in switched.text
        assert "RAG enabled" in rag_on.text
        assert "model_profile: deepseek" in status.text
        assert "rag: on" in status.text
        assert "model_profile: fast" in other_status.text
        assert "rag: off" in other_status.text


def test_web_capabilities_include_model_profile_diagnostics(monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with _local_tmp() as tmp_path:
        backend = EchoWeaveBackend(
            EchoWeaveBackendConfig(
                default_workspace=tmp_path,
                state_path=tmp_path / "state.json",
                model_profiles={
                    "demo-echo": {"provider": "demo", "model": None, "label": "Demo / 本地 Echo"},
                    "deepseek-chat": {"provider": "deepseek", "model": "deepseek-chat", "label": "DeepSeek Chat"},
                    "ollama-qwen-coder": {"provider": "ollama", "model": "qwen2.5-coder:7b"},
                },
                default_model_profile="demo-echo",
            )
        )

        capabilities = backend.web_capabilities()
        profiles = capabilities["models"]["profiles"]

        assert profiles["demo-echo"]["diagnostics"]["api_key_env"] is None
        assert profiles["demo-echo"]["diagnostics"]["api_key_configured"] is True
        assert profiles["deepseek-chat"]["diagnostics"]["api_key_env"] == "DEEPSEEK_API_KEY"
        assert profiles["deepseek-chat"]["diagnostics"]["api_key_configured"] is False
        assert profiles["ollama-qwen-coder"]["diagnostics"]["api_key_env"] is None
        assert profiles["ollama-qwen-coder"]["diagnostics"]["base_url"] == "http://127.0.0.1:11434/v1"


def test_missing_real_model_api_key_returns_readable_reply(monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with _local_tmp() as tmp_path:
        backend = EchoWeaveBackend(
            EchoWeaveBackendConfig(
                default_workspace=tmp_path,
                state_path=tmp_path / "state.json",
                model_profiles={"deepseek-chat": {"provider": "deepseek", "model": "deepseek-chat"}},
                default_model_profile="deepseek-chat",
            )
        )

        reply = backend.handle(
            EchoWeaveEvent(platform="web-user", conversation_id="web-coding", sender_id="web-admin", text="你好")
        )

        assert "模型调用失败" in reply.text
        assert "DEEPSEEK_API_KEY" in reply.text
        assert "The api_key client option must be set" not in reply.text
        assert reply.metadata["error_type"] == "ValueError"


def test_old_default_profile_state_falls_back_to_configured_default() -> None:
    with _local_tmp() as tmp_path:
        agent = EchoWeaveSocialAgent(
            SocialAgentConfig(
                default_workspace=tmp_path,
                state_path=tmp_path / "state.json",
                provider="deepseek",
                model="deepseek-chat",
                model_profiles={
                    "demo-echo": {"provider": "demo", "model": None},
                    "deepseek-chat": {"provider": "deepseek", "model": "deepseek-chat"},
                },
                default_model_profile="deepseek-chat",
            )
        )
        message = SocialMessage(platform="web-user", session_id="web-coding", sender_id="web-admin", text="/status")
        record = agent.state.session(message.conversation_key)
        record["model_profile"] = "default"
        agent.state.save()

        assert agent._selected_model_profile_name(message) == "deepseek-chat"
def test_openai_compatible_profiles_use_profile_api_settings(monkeypatch) -> None:
    created: list[dict[str, str | None]] = []

    class FakeOpenAIModelClient:
        def __init__(self, model: str, *, api_key: str | None = None, base_url: str | None = None) -> None:
            created.append({"model": model, "api_key": api_key, "base_url": base_url})

    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret")
    monkeypatch.setattr("echoweave_ai.providers.OpenAIModelClient", FakeOpenAIModelClient)
    with _local_tmp() as tmp_path:
        agent = EchoWeaveSocialAgent(
            SocialAgentConfig(
                default_workspace=tmp_path,
                state_path=tmp_path / "state.json",
                model_profiles={
                    "default": {"provider": "deepseek", "model": "deepseek-chat"},
                    "local": {
                        "provider": "openai-compatible",
                        "model": "local-model",
                        "base_url": "http://127.0.0.1:11434/v1",
                        "api_key_env": "LOCAL_LLM_KEY",
                    },
                },
            )
        )

        agent._model(SocialMessage(platform="onebot-v11", session_id="private:1", sender_id="1", text="hello"))

        assert created == [
            {
                "model": "deepseek-chat",
                "api_key": "deepseek-secret",
                "base_url": "https://api.deepseek.com",
            }
        ]
def test_access_policy_blocks_disallowed_and_blocked_users() -> None:
    with _local_tmp() as tmp_path:
        backend = EchoWeaveBackend(
            EchoWeaveBackendConfig(
                default_workspace=tmp_path,
                state_path=tmp_path / "state.json",
                allowed_users=("1001",),
                blocked_users=("9999",),
            )
        )

        allowed = backend.handle(
            EchoWeaveEvent(platform="onebot-v11", conversation_id="private:1001", sender_id="1001", text="/status")
        )
        disallowed = backend.handle(
            EchoWeaveEvent(platform="onebot-v11", conversation_id="private:1002", sender_id="1002", text="/status")
        )
        blocked = backend.handle(
            EchoWeaveEvent(platform="onebot-v11", conversation_id="private:9999", sender_id="9999", text="/status")
        )

        assert "EchoWeave social status" in allowed.text
        assert disallowed.text.startswith("Access denied")
        assert disallowed.metadata["access"]["reason_code"] == "user_not_allowed"
        assert blocked.text.startswith("Access denied")
        assert blocked.metadata["access"]["reason_code"] == "blocked_user"
def test_access_policy_restricts_groups_and_admin_commands() -> None:
    with _local_tmp() as tmp_path:
        repo = tmp_path / "repo"
        repo.mkdir()
        backend = EchoWeaveBackend(
            EchoWeaveBackendConfig(
                default_workspace=tmp_path,
                state_path=tmp_path / "state.json",
                allowed_groups=("123",),
                admins=("42",),
            )
        )

        wrong_group = backend.handle(
            EchoWeaveEvent(platform="onebot-v11", conversation_id="group:999", sender_id="42", text="/status")
        )
        non_admin_bind = backend.handle(
            EchoWeaveEvent(platform="onebot-v11", conversation_id="group:123", sender_id="43", text=f"/bind {repo}")
        )
        admin_bind = backend.handle(
            EchoWeaveEvent(platform="onebot-v11", conversation_id="group:123", sender_id="42", text=f"/bind {repo}")
        )

        assert wrong_group.metadata["access"]["reason_code"] == "group_not_allowed"
        assert non_admin_bind.metadata["access"]["reason_code"] == "admin_required"
        assert "Workspace bound" in admin_bind.text
def test_access_policy_can_require_group_mention_for_prompts() -> None:
    with _local_tmp() as tmp_path:
        backend = EchoWeaveBackend(
            EchoWeaveBackendConfig(
                default_workspace=tmp_path,
                state_path=tmp_path / "state.json",
                require_mention_in_group=True,
                bot_ids=("777",),
            )
        )

        ignored = backend.handle(
            EchoWeaveEvent(
                platform="onebot-v11",
                conversation_id="group:123",
                sender_id="43",
                text="hello",
                raw={"message": [{"type": "text", "data": {"text": "hello"}}]},
            )
        )
        command = backend.handle(
            EchoWeaveEvent(platform="onebot-v11", conversation_id="group:123", sender_id="43", text="/status")
        )
        mentioned = backend.handle(
            EchoWeaveEvent(
                platform="onebot-v11",
                conversation_id="group:123",
                sender_id="43",
                text="hello",
                raw={
                    "message": [
                        {"type": "at", "data": {"qq": "777"}},
                        {"type": "text", "data": {"text": " hello"}},
                    ]
                },
            )
        )

        assert ignored.text == ""
        assert ignored.metadata["access"]["reason_code"] == "mention_required"
        assert "EchoWeave social status" in command.text
        assert mentioned.text == "echo: hello"
def test_shell_approval_flow_records_and_executes_pending_command() -> None:
    with _local_tmp() as tmp_path:
        state_path = tmp_path / "state.json"
        agent = EchoWeaveSocialAgent(
            SocialAgentConfig(
                default_workspace=tmp_path,
                state_path=state_path,
                sandbox_root=tmp_path / "sandboxes",
            )
        )
        model = SequenceModelClient(
            [
                tool_response("approval-1", "bash", {"command": "python -m pip install"}),
                AgentResponse(text="waiting for approval"),
            ]
        )
        agent._model = lambda _message: (model, None)  # type: ignore[method-assign]
        message = SocialMessage(
            platform="onebot-v11",
            session_id="private:42",
            sender_id="42",
            text="run a command that needs approval",
        )

        reply = agent.handle(message)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        approvals = state["approvals"]
        approval_id = next(iter(approvals))

        assert "Pending approval required" in reply.text
        assert approval_id in reply.text
        assert approvals[approval_id]["status"] == "pending"
        assert approvals[approval_id]["command"] == "python -m pip install"

        list_reply = agent.handle(
            SocialMessage(platform="onebot-v11", session_id="private:42", sender_id="42", text="/approvals")
        )
        approve_reply = agent.handle(
            SocialMessage(platform="onebot-v11", session_id="private:42", sender_id="42", text=f"/approve {approval_id}")
        )
        final_state = json.loads(state_path.read_text(encoding="utf-8"))

        assert approval_id in list_reply.text
        assert f"Approved {approval_id}" in approve_reply.text
        assert final_state["approvals"][approval_id]["status"] == "approved"
        assert "result" in final_state["approvals"][approval_id]
def test_shell_approval_flow_can_deny_pending_command() -> None:
    with _local_tmp() as tmp_path:
        state_path = tmp_path / "state.json"
        agent = EchoWeaveSocialAgent(
            SocialAgentConfig(
                default_workspace=tmp_path,
                state_path=state_path,
                sandbox_root=tmp_path / "sandboxes",
            )
        )
        model = SequenceModelClient(
            [
                tool_response("approval-1", "bash", {"command": "python -m pip install"}),
                AgentResponse(text="waiting for approval"),
            ]
        )
        agent._model = lambda _message: (model, None)  # type: ignore[method-assign]

        agent.handle(
            SocialMessage(
                platform="onebot-v11",
                session_id="private:42",
                sender_id="42",
                text="run a command that needs approval",
            )
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        approval_id = next(iter(state["approvals"]))
        deny_reply = agent.handle(
            SocialMessage(platform="onebot-v11", session_id="private:42", sender_id="42", text=f"/deny {approval_id}")
        )
        final_state = json.loads(state_path.read_text(encoding="utf-8"))

        assert f"Denied approval {approval_id}" in deny_reply.text
        assert final_state["approvals"][approval_id]["status"] == "denied"
def test_shell_approval_flow_can_revoke_retry_and_expire() -> None:
    with _local_tmp() as tmp_path:
        state_path = tmp_path / "state.json"
        agent = EchoWeaveSocialAgent(
            SocialAgentConfig(
                default_workspace=tmp_path,
                state_path=state_path,
                approval_timeout_seconds=1,
            )
        )
        agent.state.save_approval(
            "oldone",
            {
                "status": "pending",
                "conversation_key": "onebot-v11:private:42",
                "command": "python -m pip install",
                "reason": "test approval",
                "cwd": str(tmp_path),
                "created_at": 1,
            },
        )
        list_reply = agent.handle(
            SocialMessage(platform="onebot-v11", session_id="private:42", sender_id="42", text="/approvals")
        )
        retry_reply = agent.handle(
            SocialMessage(platform="onebot-v11", session_id="private:42", sender_id="42", text="/retry oldone")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        retried_id = next(key for key in state["approvals"] if key != "oldone")
        revoke_reply = agent.handle(
            SocialMessage(platform="onebot-v11", session_id="private:42", sender_id="42", text=f"/revoke {retried_id}")
        )
        final_state = json.loads(state_path.read_text(encoding="utf-8"))

        assert "No pending approvals" in list_reply.text
        assert state["approvals"]["oldone"]["status"] == "expired"
        assert f"Retried approval oldone as {retried_id}" in retry_reply.text
        assert f"Revoked approval {retried_id}" in revoke_reply.text
        assert final_state["approvals"][retried_id]["status"] == "revoked"
def test_identity_question_uses_echoweave_product_identity() -> None:
    with _local_tmp() as tmp_path:
        backend = EchoWeaveBackend(
            EchoWeaveBackendConfig(
                default_workspace=tmp_path,
                state_path=tmp_path / "state.json",
                provider="deepseek",
                model="deepseek-chat",
            )
        )
        reply = backend.handle(
            EchoWeaveEvent(
                platform="onebot-v11",
                conversation_id="private:1",
                sender_id="1",
                text="你是谁？",
            )
        )

        assert "我是 EchoWeave" in reply.text
        assert "deepseek-chat" in reply.text
        assert "Claude" not in reply.text
        assert "Anthropic 开发" not in reply.text
def test_backend_admin_config_can_update_runtime_settings() -> None:
    with _local_tmp() as tmp_path:
        config_path = tmp_path / "config.local.json"
        config_path.write_text(json.dumps({"provider": "demo", "webhook_token": "keep-me"}), encoding="utf-8")
        backend = EchoWeaveBackend(
            EchoWeaveBackendConfig(
                default_workspace=tmp_path,
                config_path=config_path,
                state_path=tmp_path / "state.json",
                provider="demo",
                model_profiles={"default": {"provider": "demo", "model": None}},
            )
        )

        updated = backend.update_admin_config(
            {
                "provider": "deepseek",
                "model": "deepseek-chat",
                "default_model_profile": "deepseek",
                "model_profiles": {"deepseek": {"provider": "deepseek", "model": "deepseek-chat"}},
                "rag_enabled": True,
                "rag_vector_weight": 0.8,
                "global_enabled_skills": ["search_workspace"],
                "approval_timeout_seconds": 42,
            }
        )
        status = backend.handle(
            EchoWeaveEvent(platform="onebot-v11", conversation_id="private:1", sender_id="1", text="/status")
        )

        assert updated["provider"] == "deepseek"
        assert updated["approval_timeout_seconds"] == 42
        persisted = json.loads(config_path.read_text(encoding="utf-8"))
        assert persisted["provider"] == "deepseek"
        assert persisted["webhook_token"] == "keep-me"
        assert "model_profile: deepseek" in status.text
        assert "provider: deepseek" in status.text
        assert "rag: on" in status.text


def test_backend_admin_config_registers_ai_provider_for_profiles(monkeypatch) -> None:
    created: list[dict[str, str | None]] = []

    class FakeOpenAIModelClient:
        def __init__(self, model: str, *, api_key: str | None = None, base_url: str | None = None) -> None:
            created.append({"model": model, "api_key": api_key, "base_url": base_url})

    monkeypatch.setenv("LOCAL_WEB_KEY", "web-secret")
    monkeypatch.setattr("echoweave_ai.providers.OpenAIModelClient", FakeOpenAIModelClient)
    with _local_tmp() as tmp_path:
        backend = EchoWeaveBackend(
            EchoWeaveBackendConfig(
                default_workspace=tmp_path,
                state_path=tmp_path / "state.json",
                provider="demo",
                model_profiles={"default": {"provider": "demo", "model": None}},
            )
        )

        backend.update_admin_config(
            {
                "ai_providers": {
                    "local-web": {
                        "type": "openai-compatible",
                        "base_url": "http://127.0.0.1:1234/v1",
                        "api_key_env": "LOCAL_WEB_KEY",
                        "default_model": "local-default",
                    }
                },
                "default_model_profile": "local",
                "model_profiles": {"local": {"provider": "local-web", "model": "local-model"}},
            }
        )
        backend._agent._model(
            SocialMessage(platform="web-user", session_id="web-coding", sender_id="web-admin", text="hello")
        )

        assert created == [
            {"model": "local-model", "api_key": "web-secret", "base_url": "http://127.0.0.1:1234/v1"}
        ]


def test_backend_admin_config_redacts_and_preserves_inline_model_api_key(monkeypatch) -> None:
    created: list[dict[str, str | None]] = []

    class FakeOpenAIModelClient:
        def __init__(self, model: str, *, api_key: str | None = None, base_url: str | None = None) -> None:
            created.append({"model": model, "api_key": api_key, "base_url": base_url})

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr("echoweave_ai.providers.OpenAIModelClient", FakeOpenAIModelClient)
    with _local_tmp() as tmp_path:
        config_path = tmp_path / "config.local.json"
        config_path.write_text(json.dumps({"provider": "demo"}), encoding="utf-8")
        backend = EchoWeaveBackend(
            EchoWeaveBackendConfig(
                default_workspace=tmp_path,
                config_path=config_path,
                state_path=tmp_path / "state.json",
                provider="demo",
                model_profiles={"demo-echo": {"provider": "demo", "model": None}},
            )
        )

        updated = backend.update_admin_config(
            {
                "default_model_profile": "deepseek-chat",
                "model_profiles": {
                    "deepseek-chat": {
                        "provider": "deepseek",
                        "model": "deepseek-chat",
                        "api_key": "inline-secret",
                    }
                },
            }
        )

        assert "api_key" not in updated["model_profiles"]["deepseek-chat"]
        assert updated["model_profiles"]["deepseek-chat"]["api_key_configured"] is True
        persisted = json.loads(config_path.read_text(encoding="utf-8"))
        assert persisted["model_profiles"]["deepseek-chat"]["api_key"] == "inline-secret"

        backend.update_admin_config({"model_profiles": updated["model_profiles"]})
        persisted_after_redacted_save = json.loads(config_path.read_text(encoding="utf-8"))
        assert persisted_after_redacted_save["model_profiles"]["deepseek-chat"]["api_key"] == "inline-secret"

        backend._agent._model(
            SocialMessage(platform="web-user", session_id="web-coding", sender_id="web-admin", text="hello")
        )

        assert created == [
            {"model": "deepseek-chat", "api_key": "inline-secret", "base_url": "https://api.deepseek.com"}
        ]
