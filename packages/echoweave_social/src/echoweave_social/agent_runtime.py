from __future__ import annotations

import hashlib
import re
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from echoweave_agent_core import AgentCore, AgentCoreConfig, TurnRequest
from echoweave_ai.providers import create_ai_model_from_profile
from echoweave_runtime.app import build_registry
from echoweave_runtime.config import load_env
from echoweave_runtime.events import InboundMessage as SocialMessage
from echoweave_runtime.events import OutboundMessage as SocialReply
from echoweave_runtime.extensions.manager import build_extension_manager
from echoweave_runtime.extensions.skill_provider import LocalSkillProvider
from echoweave_runtime.session.store import SessionStore
from echoweave_runtime.tools.policy import PolicyVerdict, ShellCommandPolicy
from echoweave_harness.audit import record_audit
from echoweave_harness.policy import HarnessPolicy, get_harness_policy
from echoweave_social.state import SocialStateStore


_IDENTITY_MARKERS = (
    "你是谁",
    "你是什么",
    "你叫",
    "介绍一下你",
    "介绍下你",
    "什么模型",
    "哪个模型",
    "谁开发",
    "who are you",
    "what are you",
    "what model",
    "which model",
)

@dataclass(frozen=True)
class SocialAgentConfig:
    default_workspace: Path
    state_path: Path | None = None
    sandbox_root: Path | None = None
    provider: str = "demo"
    model: str | None = None
    model_profiles: dict[str, dict[str, Any]] | None = None
    default_model_profile: str | None = None
    rag_enabled: bool = False
    rag_backend: str = "pgvector_hybrid_bgem3"
    rag_pgvector_dsn: str | None = None
    rag_pgvector_table: str = "echoweave_rag_chunks"
    rag_embedding_model: str = "BAAI/bge-m3"
    rag_auto_index: bool = False
    rag_vector_weight: float = 0.65
    rag_bm25_weight: float = 0.35
    rag_query_rewrite_enabled: bool = False
    rag_query_rewrite_strategy: str = "local_multi_query"
    rag_query_rewrite_max_queries: int = 3
    rag_rerank_enabled: bool = False
    rag_rerank_strategy: str = "bm25"
    rag_rerank_candidate_multiplier: int = 4
    rag_rerank_original_score_weight: float = 0.65
    rag_rerank_bm25_weight: float = 0.35
    global_enabled_skills: tuple[str, ...] = ()
    session_enabled_skills: tuple[str, ...] = ()
    compact_keep_tail: int = 8
    tool_execution_mode: str = "sequential"
    approval_timeout_seconds: int = 3600
    harness_policy: HarnessPolicy | None = None


@dataclass(frozen=True, slots=True)
class SocialRecoveryContext:
    conversation_key: str
    workspace: Path
    session_path: Path


class EchoWeaveSocialAgent:
    """Small social-platform facade over the embedded EchoWeave runtime."""

    def __init__(self, config: SocialAgentConfig) -> None:
        self.config = config
        load_env(config.default_workspace)
        default_state = config.default_workspace / "echoweave-data" / "state.json"
        self.state = SocialStateStore(config.state_path or default_state)

    def handle(self, message: SocialMessage) -> SocialReply:
        text = message.text.strip()
        if not text:
            return self._reply(message, "Empty message ignored.")
        if text.startswith("/"):
            handled = self._handle_command(message, text)
            if handled is not None:
                return handled
        if self._is_identity_question(text):
            return self._identity_reply(message)
        return self._run_agent(message, text)

    def _handle_command(self, message: SocialMessage, text: str) -> SocialReply | None:
        body = text[1:].strip()
        command, _, payload = body.partition(" ")
        command = command.lower()
        payload = payload.strip()
        if command in {"help", "echoweave-help"}:
            return self._reply(message, self._help())
        if command in {"models", "model-list"}:
            return self._reply(message, self._models_status(message))
        if command == "model":
            return self._model_command(message, payload)
        if command in {"skills", "skill-list"}:
            return self._reply(message, self._skills_status(message))
        if command == "skill":
            return self._skill_command(message, payload)
        if command in {"approvals", "approval-list"}:
            return self._approvals_command(message)
        if command == "approve":
            return self._approve_command(message, payload)
        if command == "deny":
            return self._deny_command(message, payload)
        if command == "revoke":
            return self._revoke_command(message, payload)
        if command == "retry":
            return self._retry_command(message, payload)
        if command == "rag":
            return self._rag_command(message, payload)
        if command == "bind":
            return self._bind(message, payload)
        if command == "status":
            return self._status(message)
        if command == "new":
            self.state.reset_runtime_session(message.conversation_key)
            return self._reply(message, "Started a new EchoWeave runtime session for this social conversation.")
        if command in {"unbind", "sandbox"}:
            self.state.unbind_workspace(message.conversation_key)
            workspace = self._sandbox_for(message)
            return self._reply(message, f"Switched this conversation back to its sandbox:\n{workspace}")
        if command == "agent":
            if not payload:
                return self._reply(message, "Usage: /agent <prompt>")
            return self._run_agent(message, payload)
        return None

    def _bind(self, message: SocialMessage, raw_workspace: str) -> SocialReply:
        if not raw_workspace:
            return self._reply(message, "Usage: /bind <workspace>")
        workspace = Path(raw_workspace).expanduser().resolve()
        if not workspace.exists() or not workspace.is_dir():
            return self._reply(message, f"Workspace not found: {workspace}")
        self.state.bind_workspace(message.conversation_key, workspace)
        return self._reply(message, f"Workspace bound:\n{workspace}")

    def _status(self, message: SocialMessage) -> SocialReply:
        record = self.state.session(message.conversation_key)
        workspace = self._workspace_for(message)
        lines = [
            "EchoWeave social status",
            f"platform: {message.platform}",
            f"conversation: {message.conversation_id}",
            f"workspace_mode: {self._workspace_mode(message)}",
            f"workspace: {workspace}",
            f"model_profile: {self._selected_model_profile_name(message)}",
            f"provider: {self._selected_provider_model(message)[0]}",
            f"model: {self._selected_provider_model(message)[1] or '(default)'}",
            f"rag: {'on' if self._rag_enabled(message) else 'off'}",
            f"rag_backend: {self.config.rag_backend}",
            f"skills: {len(self._enabled_skills(message))} enabled",
            f"runtime_session_id: {record.get('runtime_session_id') or '(none)'}",
            f"runtime_session: {record.get('runtime_session') or '(none)'}",
        ]
        return self._reply(message, "\n".join(lines))

    def _run_agent(self, message: SocialMessage, prompt: str) -> SocialReply:
        workspace = self._workspace_for(message)
        session_store = SessionStore(workspace / "echoweave-data" / "sessions")
        session_path = self._session_path(message, session_store)
        snapshot = session_store.load_snapshot(session_path)
        pending_before = {
            item["id"]
            for item in self.state.list_pending_approvals(
                message.conversation_key,
                timeout_seconds=self.config.approval_timeout_seconds,
            )
        }
        provider_name, model_name = self._selected_provider_model(message)
        started = time.perf_counter()
        record_audit(
            "model",
            "call",
            status="start",
            conversation_id=message.conversation_key,
            actor_id=message.sender_id,
            workspace=workspace,
            metadata={"provider": provider_name, "model": model_name, "prompt_chars": len(prompt), "rag_enabled": self._rag_enabled(message)},
        )
        try:
            core = self._build_core(message, workspace, session_store)
            outcome = core.execute_turn(
                TurnRequest(
                    prompt=prompt,
                    session_path=session_path,
                    history=snapshot.history,
                    summary=snapshot.summary,
                    metadata={
                        "conversation_id": message.conversation_key,
                        "actor_id": message.sender_id,
                        "workspace": str(workspace),
                        "provider": provider_name,
                        "model": model_name,
                        "rag_enabled": self._rag_enabled(message),
                    },
                )
            )
            turn = outcome.require_result()
            reply = turn.text
        except Exception as exc:
            record_audit(
                "model",
                "call",
                status="error",
                conversation_id=message.conversation_key,
                actor_id=message.sender_id,
                workspace=workspace,
                latency_ms=(time.perf_counter() - started) * 1000,
                metadata={"provider": provider_name, "model": model_name, "reason": str(exc)},
            )
            profile_name = self._selected_model_profile_name(message)
            return self._reply(
                message,
                (
                    f"模型调用失败：{exc}\n\n"
                    f"当前模型 profile：{profile_name} ({provider_name}/{model_name or '(default)'})\n"
                    "请在管理端检查模型配置，或在启动 EchoWeave 前设置对应 API key 环境变量。"
                ),
                metadata={
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "model_profile": profile_name,
                    "provider": provider_name,
                    "model": model_name,
                },
            )
        record_audit(
            "model",
            "call",
            status="ok",
            conversation_id=message.conversation_key,
            actor_id=message.sender_id,
            workspace=workspace,
            latency_ms=(time.perf_counter() - started) * 1000,
            metadata={"provider": provider_name, "model": model_name, "reply_chars": len(reply or "")},
        )
        header = session_store.read_header(session_path)
        record = self.state.session(message.conversation_key)
        record["runtime_session"] = str(session_path)
        record["runtime_session_id"] = header.id
        record["workspace"] = str(workspace)
        self.state.save()
        rendered_reply = reply or "(EchoWeave returned an empty reply)"
        pending_after = self.state.list_pending_approvals(
            message.conversation_key,
            timeout_seconds=self.config.approval_timeout_seconds,
        )
        new_pending = [item for item in pending_after if item["id"] not in pending_before]
        if new_pending:
            rendered_reply = f"{rendered_reply}\n\n{_render_approval_notice(new_pending)}"
        return self._reply(
            message,
            rendered_reply,
            runtime_session_id=header.id,
            runtime_session_path=str(session_path),
            metadata={"workspace": str(workspace)},
        )

    def recovery_contexts(self) -> tuple[SocialRecoveryContext, ...]:
        contexts: list[SocialRecoveryContext] = []
        for conversation_key, record in self.state.session_records().items():
            raw_session = record.get("runtime_session")
            if not isinstance(raw_session, str) or not raw_session.strip():
                continue
            session_path = Path(raw_session).expanduser().resolve()
            if not session_path.exists() or not session_path.is_file():
                continue
            sessions_dir = session_path.parent
            if sessions_dir.name != "sessions" or sessions_dir.parent.name != "echoweave-data":
                continue
            workspace = sessions_dir.parent.parent.resolve()
            contexts.append(
                SocialRecoveryContext(
                    conversation_key=conversation_key,
                    workspace=workspace,
                    session_path=session_path,
                )
            )
        contexts.sort(key=lambda item: (str(item.workspace), item.conversation_key))
        return tuple(contexts)

    def build_recovery_core(self, session_path: Path) -> AgentCore:
        resolved = session_path.expanduser().resolve()
        matches = [
            item for item in self.recovery_contexts() if item.session_path == resolved
        ]
        if not matches:
            raise ValueError(f"no social conversation owns recovery session: {resolved}")
        if len(matches) > 1:
            owners = ", ".join(item.conversation_key for item in matches)
            raise ValueError(f"multiple social conversations own recovery session {resolved}: {owners}")
        context = matches[0]
        platform, separator, conversation_id = context.conversation_key.partition(":")
        if not separator or not platform or not conversation_id:
            raise ValueError(f"invalid social conversation key: {context.conversation_key}")
        message = SocialMessage(
            platform=platform,
            conversation_id=conversation_id,
            sender_id="automatic-recovery",
            text="automatic orphan recovery",
        )
        return self._build_core(
            message,
            context.workspace,
            SessionStore(context.session_path.parent),
        )

    def _build_core(
        self,
        message: SocialMessage,
        workspace: Path,
        session_store: SessionStore,
    ) -> AgentCore:
        extensions = build_extension_manager(
            workspace,
            rag_backend=self.config.rag_backend,
            rag_pgvector_dsn=self.config.rag_pgvector_dsn,
            rag_pgvector_table=self.config.rag_pgvector_table,
            rag_embedding_model=self.config.rag_embedding_model,
            rag_auto_index=self.config.rag_auto_index,
            rag_vector_weight=self.config.rag_vector_weight,
            rag_bm25_weight=self.config.rag_bm25_weight,
            rag_query_rewrite_enabled=self.config.rag_query_rewrite_enabled,
            rag_query_rewrite_strategy=self.config.rag_query_rewrite_strategy,
            rag_query_rewrite_max_queries=self.config.rag_query_rewrite_max_queries,
            rag_rerank_enabled=self.config.rag_rerank_enabled,
            rag_rerank_strategy=self.config.rag_rerank_strategy,
            rag_rerank_candidate_multiplier=self.config.rag_rerank_candidate_multiplier,
            rag_rerank_original_score_weight=self.config.rag_rerank_original_score_weight,
            rag_rerank_bm25_weight=self.config.rag_rerank_bm25_weight,
            enabled_skills=self._enabled_skills(message),
        )
        registry = build_registry(
            workspace,
            extensions=extensions,
            approval_callback=lambda command, reason, run_cwd=None: self._request_approval(
                message,
                command,
                reason,
                Path(run_cwd).resolve() if run_cwd is not None else workspace,
            ),
        )
        provider_name, model_name = self._selected_provider_model(message)
        model_client, capabilities = self._model(message)
        return AgentCore.from_config(
            AgentCoreConfig(
                model_client=model_client,
                tool_registry=registry,
                session_store=session_store,
                extensions=extensions,
                compact_keep_tail=self.config.compact_keep_tail,
                tool_execution_mode=self.config.tool_execution_mode,  # type: ignore[arg-type]
                provider_capabilities=capabilities,
                retrieval_enabled=self._rag_enabled(message),
                metadata={
                    "conversation_id": message.conversation_key,
                    "actor_id": message.sender_id,
                    "workspace": str(workspace),
                    "provider": provider_name,
                    "model": model_name,
                    "rag_enabled": self._rag_enabled(message),
                    "tool_execution_mode": self.config.tool_execution_mode,
                },
            )
        )

    def _model(self, message: SocialMessage):
        profile = self._selected_model_profile(message)
        return create_ai_model_from_profile(
            profile,
            default_provider=self.config.provider,
            default_model=self.config.model,
        )

    def _is_identity_question(self, text: str) -> bool:
        normalized = text.strip().lower()
        return any(marker in normalized for marker in _IDENTITY_MARKERS)

    def _identity_reply(self, message: SocialMessage) -> SocialReply:
        provider, selected_model = self._selected_provider_model(message)
        model = selected_model or "(default)"
        if provider == "demo":
            backend = "demo"
        elif provider == "anthropic":
            backend = f"Anthropic-compatible backend, model: {model}"
        else:
            backend = f"{provider}, model: {model}"
        return self._reply(
            message,
            "我是 EchoWeave，一个接入社交平台的本地编码代理。"
            f"当前后端配置为 {backend}。",
            metadata={"provider": provider, "model": selected_model},
        )

    def _model_command(self, message: SocialMessage, payload: str) -> SocialReply:
        name = payload.strip()
        if not name:
            return self._reply(message, self._models_status(message))
        profiles = self._model_profiles()
        if name not in profiles:
            return self._reply(message, f"Unknown model profile: {name}\n\n{self._models_status(message)}")
        record = self.state.session(message.conversation_key)
        record["model_profile"] = name
        record.pop("runtime_session", None)
        record.pop("runtime_session_id", None)
        self.state.save()
        provider, model = self._provider_model_from_profile(profiles[name])
        return self._reply(message, f"Model profile switched to {name}: {provider}/{model or '(default)'}")

    def _models_status(self, message: SocialMessage) -> str:
        current = self._selected_model_profile_name(message)
        lines = [f"Current model profile: {current}", "Available model profiles:"]
        for name, profile in self._model_profiles().items():
            marker = "*" if name == current else "-"
            provider, model = self._provider_model_from_profile(profile)
            lines.append(f"{marker} {name}: {provider}/{model or '(default)'}")
        lines.append("Use /model <profile> to switch this conversation.")
        return "\n".join(lines)

    def _rag_command(self, message: SocialMessage, payload: str) -> SocialReply:
        normalized = payload.strip().lower()
        record = self.state.session(message.conversation_key)
        if normalized in {"on", "enable", "enabled", "true", "1"}:
            record["rag_enabled"] = True
            self.state.save()
            if self.config.rag_backend.startswith("pgvector") and not self.config.rag_pgvector_dsn:
                return self._reply(
                    message,
                    "RAG enabled for this conversation, but pgvector DSN is missing.\n"
                    "Set rag_pgvector_dsn in config.local.json, then run /rag index.",
                )
            return self._reply(message, "RAG enabled for this conversation.")
        if normalized in {"off", "disable", "disabled", "false", "0"}:
            record["rag_enabled"] = False
            self.state.save()
            return self._reply(message, "RAG disabled for this conversation.")
        if normalized in {"index", "reindex"}:
            return self._index_rag_workspace(message)
        lines = [
            f"RAG is {'on' if self._rag_enabled(message) else 'off'}.",
            f"backend: {self.config.rag_backend}",
            f"embedding: {self.config.rag_embedding_model}",
            f"pgvector_table: {self.config.rag_pgvector_table}",
            f"pgvector_dsn: {'configured' if self.config.rag_pgvector_dsn else '(missing)'}",
            f"hybrid_weights: vector={self.config.rag_vector_weight}, bm25={self.config.rag_bm25_weight}",
            f"query_rewrite: {'on' if self.config.rag_query_rewrite_enabled else 'off'} ({self.config.rag_query_rewrite_strategy}, max={self.config.rag_query_rewrite_max_queries})",
            f"rerank: {'on' if self.config.rag_rerank_enabled else 'off'} ({self.config.rag_rerank_strategy}, candidates=x{self.config.rag_rerank_candidate_multiplier})",
            "Use /rag on or /rag off.",
        ]
        return self._reply(message, "\n".join(lines))

    def _index_rag_workspace(self, message: SocialMessage) -> SocialReply:
        workspace = self._workspace_for(message)
        extensions = build_extension_manager(
            workspace,
            rag_backend=self.config.rag_backend,
            rag_pgvector_dsn=self.config.rag_pgvector_dsn,
            rag_pgvector_table=self.config.rag_pgvector_table,
            rag_embedding_model=self.config.rag_embedding_model,
            rag_auto_index=False,
            rag_vector_weight=self.config.rag_vector_weight,
            rag_bm25_weight=self.config.rag_bm25_weight,
            rag_query_rewrite_enabled=self.config.rag_query_rewrite_enabled,
            rag_query_rewrite_strategy=self.config.rag_query_rewrite_strategy,
            rag_query_rewrite_max_queries=self.config.rag_query_rewrite_max_queries,
            rag_rerank_enabled=self.config.rag_rerank_enabled,
            rag_rerank_strategy=self.config.rag_rerank_strategy,
            rag_rerank_candidate_multiplier=self.config.rag_rerank_candidate_multiplier,
            rag_rerank_original_score_weight=self.config.rag_rerank_original_score_weight,
            rag_rerank_bm25_weight=self.config.rag_rerank_bm25_weight,
        )
        provider = extensions.get_provider("retrieval")
        index_workspace = getattr(provider, "index_workspace", None)
        if not callable(index_workspace):
            return self._reply(message, f"RAG backend does not support indexing: {self.config.rag_backend}")
        try:
            count = int(index_workspace())
        except Exception as exc:
            return self._reply(message, f"RAG indexing failed: {type(exc).__name__}: {exc}")
        return self._reply(message, f"RAG indexed {count} chunks for workspace:\n{workspace}")

    def _request_approval(self, message: SocialMessage, command: str, reason: str, run_cwd: Path) -> bool:
        approval_id = uuid.uuid4().hex[:8]
        record = {
            "status": "pending",
            "conversation_key": message.conversation_key,
            "platform": message.platform,
            "session_id": message.conversation_id,
            "sender_id": message.sender_id,
            "command": command,
            "reason": reason,
            "cwd": str(run_cwd),
            "created_at": _now_ts(),
        }
        self.state.save_approval(approval_id, record)
        record_audit(
            "approval",
            "request",
            status="pending",
            subject=approval_id,
            conversation_id=message.conversation_key,
            actor_id=message.sender_id,
            workspace=run_cwd,
            metadata={"command": command, "reason": reason},
        )
        return False

    def _approvals_command(self, message: SocialMessage) -> SocialReply:
        approvals = self.state.list_pending_approvals(
            message.conversation_key,
            timeout_seconds=self.config.approval_timeout_seconds,
        )
        if not approvals:
            return self._reply(message, "No pending approvals for this conversation.")
        lines = ["Pending approvals:"]
        for item in approvals:
            lines.append(
                f"{item['id']} - {item.get('command')} "
                f"(cwd: {item.get('cwd')}, reason: {item.get('reason')})"
            )
        lines.append("Use /approve <id> or /deny <id>.")
        return self._reply(message, "\n".join(lines))

    def _approve_command(self, message: SocialMessage, approval_id: str) -> SocialReply:
        result = self.approve_approval(approval_id, actor_id=message.sender_id, conversation_key=message.conversation_key)
        return self._reply(message, result)

    def approve_approval(
        self,
        approval_id: str,
        *,
        actor_id: str,
        conversation_key: str | None = None,
    ) -> str:
        approval_id = approval_id.strip()
        if not approval_id:
            return "Usage: /approve <id>"
        record = self.state.approval(approval_id, timeout_seconds=self.config.approval_timeout_seconds)
        if record is None:
            return f"Approval not found: {approval_id}"
        if record.get("status") != "pending":
            return f"Approval {approval_id} is already {record.get('status')}."
        if conversation_key is not None and record.get("conversation_key") != conversation_key:
            return f"Approval {approval_id} belongs to another conversation."

        command = str(record.get("command") or "")
        cwd = Path(str(record.get("cwd") or self.config.default_workspace)).expanduser().resolve()
        policy_result = ShellCommandPolicy(auto_approve=False).check(command)
        if policy_result.verdict == PolicyVerdict.DENY:
            record["status"] = "failed"
            record["error"] = f"blocked by shell policy: {policy_result.reason}"
            self.state.save_approval(approval_id, record)
            return f"Approval {approval_id} failed: {record['error']}"
        record["status"] = "approved"
        record["approved_by"] = actor_id
        record["approved_at"] = _now_ts()
        try:
            output = _run_approved_shell(command, cwd)
        except Exception as exc:
            record["status"] = "failed"
            record["error"] = f"{type(exc).__name__}: {exc}"
            self.state.save_approval(approval_id, record)
            return f"Approval {approval_id} failed: {record['error']}"
        record["result"] = output
        self.state.save_approval(approval_id, record)
        record_audit(
            "approval",
            "approve",
            status="ok",
            subject=approval_id,
            conversation_id=str(record.get("conversation_key") or ""),
            actor_id=actor_id,
            workspace=cwd,
            metadata={"command": command},
        )
        rendered = output.strip() or "Command exited with no output."
        return f"Approved {approval_id}. Command result:\n{rendered}"

    def _deny_command(self, message: SocialMessage, approval_id: str) -> SocialReply:
        return self._reply(message, self.deny_approval(approval_id, actor_id=message.sender_id, conversation_key=message.conversation_key))

    def deny_approval(
        self,
        approval_id: str,
        *,
        actor_id: str,
        conversation_key: str | None = None,
    ) -> str:
        approval_id = approval_id.strip()
        if not approval_id:
            return "Usage: /deny <id>"
        record = self.state.approval(approval_id, timeout_seconds=self.config.approval_timeout_seconds)
        if record is None:
            return f"Approval not found: {approval_id}"
        if record.get("status") != "pending":
            return f"Approval {approval_id} is already {record.get('status')}."
        if conversation_key is not None and record.get("conversation_key") != conversation_key:
            return f"Approval {approval_id} belongs to another conversation."
        record["status"] = "denied"
        record["denied_by"] = actor_id
        record["denied_at"] = _now_ts()
        self.state.save_approval(approval_id, record)
        record_audit("approval", "deny", status="denied", subject=approval_id, conversation_id=str(record.get("conversation_key") or ""), actor_id=actor_id, metadata={"command": record.get("command")})
        return f"Denied approval {approval_id}."

    def _revoke_command(self, message: SocialMessage, approval_id: str) -> SocialReply:
        return self._reply(message, self.revoke_approval(approval_id, actor_id=message.sender_id, conversation_key=message.conversation_key))

    def revoke_approval(
        self,
        approval_id: str,
        *,
        actor_id: str,
        conversation_key: str | None = None,
    ) -> str:
        approval_id = approval_id.strip()
        if not approval_id:
            return "Usage: /revoke <id>"
        record = self.state.approval(approval_id, timeout_seconds=self.config.approval_timeout_seconds)
        if record is None:
            return f"Approval not found: {approval_id}"
        if record.get("status") not in {"pending", "approved"}:
            return f"Approval {approval_id} is already {record.get('status')}."
        if conversation_key is not None and record.get("conversation_key") != conversation_key:
            return f"Approval {approval_id} belongs to another conversation."
        record["status"] = "revoked"
        record["revoked_by"] = actor_id
        record["revoked_at"] = _now_ts()
        self.state.save_approval(approval_id, record)
        record_audit("approval", "revoke", status="revoked", subject=approval_id, conversation_id=str(record.get("conversation_key") or ""), actor_id=actor_id, metadata={"command": record.get("command")})
        return f"Revoked approval {approval_id}."

    def _retry_command(self, message: SocialMessage, approval_id: str) -> SocialReply:
        return self._reply(message, self.retry_approval(approval_id, actor_id=message.sender_id, conversation_key=message.conversation_key))

    def retry_approval(
        self,
        approval_id: str,
        *,
        actor_id: str,
        conversation_key: str | None = None,
    ) -> str:
        approval_id = approval_id.strip()
        if not approval_id:
            return "Usage: /retry <id>"
        record = self.state.approval(approval_id, timeout_seconds=self.config.approval_timeout_seconds)
        if record is None:
            return f"Approval not found: {approval_id}"
        if record.get("status") == "pending":
            return f"Approval {approval_id} is still pending."
        if conversation_key is not None and record.get("conversation_key") != conversation_key:
            return f"Approval {approval_id} belongs to another conversation."
        new_id = uuid.uuid4().hex[:8]
        retry_record = {
            key: value
            for key, value in record.items()
            if key
            not in {
                "approved_at",
                "approved_by",
                "denied_at",
                "denied_by",
                "expired_at",
                "failed_at",
                "revoked_at",
                "revoked_by",
                "result",
                "error",
            }
        }
        retry_record["status"] = "pending"
        retry_record["created_at"] = _now_ts()
        retry_record["retried_from"] = approval_id
        retry_record["retried_by"] = actor_id
        self.state.save_approval(new_id, retry_record)
        record_audit("approval", "retry", status="pending", subject=new_id, conversation_id=str(record.get("conversation_key") or ""), actor_id=actor_id, metadata={"retried_from": approval_id, "command": record.get("command")})
        return f"Retried approval {approval_id} as {new_id}."

    def list_approvals(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.state.list_approvals(timeout_seconds=self.config.approval_timeout_seconds, limit=limit)

    def _model_profiles(self) -> dict[str, dict[str, Any]]:
        profiles = dict(self.config.model_profiles or {})
        if not profiles:
            profiles["default"] = {"provider": self.config.provider, "model": self.config.model}
        return profiles

    def _selected_model_profile(self, message: SocialMessage) -> dict[str, Any]:
        return self._model_profiles()[self._selected_model_profile_name(message)]

    def _selected_model_profile_name(self, message: SocialMessage) -> str:
        record = self.state.session(message.conversation_key)
        selected = record.get("model_profile")
        profiles = self._model_profiles()
        if isinstance(selected, str) and selected in profiles:
            if self._model_profile_allowed(selected):
                return selected
        default_name = self.config.default_model_profile
        if isinstance(default_name, str) and default_name in profiles and self._model_profile_allowed(default_name):
            return default_name
        policy = self.config.harness_policy or get_harness_policy()
        if policy.session_model_allowlist:
            for name in policy.session_model_allowlist:
                if name in profiles:
                    return name
        if "default" in profiles and self._model_profile_allowed("default"):
            return "default"
        return next(iter(profiles))

    def _model_profile_allowed(self, name: str) -> bool:
        policy = self.config.harness_policy or get_harness_policy()
        return not policy.session_model_allowlist or name in policy.session_model_allowlist

    def _selected_provider_model(self, message: SocialMessage) -> tuple[str, str | None]:
        profile = self._model_profiles()[self._selected_model_profile_name(message)]
        return self._provider_model_from_profile(profile)

    def _provider_model_from_profile(self, profile: dict[str, Any]) -> tuple[str, str | None]:
        provider = profile.get("provider") or self.config.provider
        model = profile["model"] if "model" in profile else self.config.model
        return str(provider), str(model) if model else None

    def _rag_enabled(self, message: SocialMessage) -> bool:
        policy = self.config.harness_policy or get_harness_policy()
        if policy.session_rag_enabled is not None:
            return policy.session_rag_enabled
        value = self.state.session(message.conversation_key).get("rag_enabled")
        if isinstance(value, bool):
            return value
        return self.config.rag_enabled

    def _skill_command(self, message: SocialMessage, payload: str) -> SocialReply:
        parts = payload.split()
        if not parts:
            return self._reply(message, self._skills_status(message))
        scope = "session"
        action = parts[0].lower()
        name_index = 1
        if action == "global" and len(parts) >= 3:
            scope = "global"
            action = parts[1].lower()
            name_index = 2
        if action not in {"on", "off", "enable", "disable"} or len(parts) <= name_index:
            return self._reply(
                message,
                "Usage:\n"
                "/skill on <name>\n"
                "/skill off <name>\n"
                "/skill global on <name>\n"
                "/skill global off <name>",
            )
        enabled = action in {"on", "enable"}
        name = parts[name_index].strip()
        available = {spec.name for spec in self._available_skills(message)}
        if name not in available:
            return self._reply(message, f"Unknown skill: {name}\n\n{self._skills_status(message)}")
        record = self.state.global_settings() if scope == "global" else self.state.session(message.conversation_key)
        key = "enabled_skills" if enabled else "disabled_skills"
        opposite = "disabled_skills" if enabled else "enabled_skills"
        selected = set(_as_str_list(record.get(key)))
        selected.add(name)
        record[key] = sorted(selected)
        opposite_values = set(_as_str_list(record.get(opposite)))
        opposite_values.discard(name)
        record[opposite] = sorted(opposite_values)
        self.state.save()
        label = "globally" if scope == "global" else "for this conversation"
        state = "enabled" if enabled else "disabled"
        return self._reply(message, f"Skill {name} {state} {label}.")

    def _skills_status(self, message: SocialMessage) -> str:
        enabled = self._enabled_skills(message)
        global_enabled = self._global_enabled_skills()
        session_record = self.state.session(message.conversation_key)
        session_enabled = set(_as_str_list(session_record.get("enabled_skills")))
        session_disabled = set(_as_str_list(session_record.get("disabled_skills")))
        lines = [
            "Skills:",
            "[x] enabled, [ ] disabled; G=global, S=session override",
        ]
        for spec in self._available_skills(message):
            checked = "[x]" if spec.name in enabled else "[ ]"
            marks: list[str] = []
            if spec.name in global_enabled:
                marks.append("G")
            if spec.name in session_enabled:
                marks.append("S+")
            if spec.name in session_disabled:
                marks.append("S-")
            suffix = f" ({', '.join(marks)})" if marks else ""
            lines.append(f"{checked} {spec.name}{suffix} - {spec.description}")
        lines.append("Use /skill on <name>, /skill off <name>, or /skill global on <name>.")
        return "\n".join(lines)

    def _available_skills(self, message: SocialMessage):
        return LocalSkillProvider(self._workspace_for(message)).list_skills()

    def _enabled_skills(self, message: SocialMessage) -> set[str]:
        enabled = self._global_enabled_skills()
        enabled.update(self.config.session_enabled_skills)
        session_record = self.state.session(message.conversation_key)
        enabled.update(_as_str_list(session_record.get("enabled_skills")))
        enabled.difference_update(_as_str_list(session_record.get("disabled_skills")))
        policy = self.config.harness_policy or get_harness_policy()
        if policy.session_skill_allowlist:
            enabled.intersection_update(policy.session_skill_allowlist)
        return enabled

    def _global_enabled_skills(self) -> set[str]:
        settings = self.state.global_settings()
        enabled = set(self.config.global_enabled_skills)
        enabled.update(_as_str_list(settings.get("enabled_skills")))
        enabled.difference_update(_as_str_list(settings.get("disabled_skills")))
        return enabled

    def _session_path(self, message: SocialMessage, session_store: SessionStore) -> Path:
        record = self.state.session(message.conversation_key)
        existing = record.get("runtime_session")
        if isinstance(existing, str) and existing:
            path = Path(existing)
            if path.exists() and path.is_file() and _is_relative_to(path, session_store.sessions_dir):
                return path
        path = session_store.create()
        header = session_store.read_header(path)
        record["runtime_session"] = str(path)
        record["runtime_session_id"] = header.id
        self.state.save()
        return path

    def _workspace_for(self, message: SocialMessage) -> Path:
        record = self.state.session(message.conversation_key)
        workspace = record.get("workspace") if record.get("workspace_bound") is True else None
        if isinstance(workspace, str) and workspace.strip():
            path = Path(workspace).expanduser().resolve()
            if path.exists() and path.is_dir():
                return path
        sandbox = self._sandbox_for(message)
        self._forget_external_session(record, sandbox)
        return sandbox

    def _workspace_mode(self, message: SocialMessage) -> str:
        record = self.state.session(message.conversation_key)
        if record.get("workspace_bound") is True:
            return "bound"
        return "sandbox"

    def _sandbox_for(self, message: SocialMessage) -> Path:
        root = (
            self.config.sandbox_root
            or self.config.default_workspace / "echoweave-data" / "sandboxes"
        ).expanduser().resolve()
        slug = _safe_workspace_slug(message.conversation_key)
        workspace = root / slug
        workspace.mkdir(parents=True, exist_ok=True)
        marker = workspace / "README.md"
        if not marker.exists():
            marker.write_text(
                "# EchoWeave Conversation Sandbox\n\n"
                "This directory is isolated for one social-platform conversation.\n"
                "Use /bind <workspace> only when you intentionally want the agent to work in a real repository.\n",
                encoding="utf-8",
            )
        record = self.state.session(message.conversation_key)
        if record.get("sandbox_workspace") != str(workspace):
            record["sandbox_workspace"] = str(workspace)
            self.state.save()
        return workspace

    def _forget_external_session(self, record: dict[str, Any], sandbox: Path) -> None:
        existing = record.get("runtime_session")
        if not isinstance(existing, str) or not existing:
            return
        sessions_root = sandbox / "echoweave-data" / "sessions"
        if _is_relative_to(Path(existing), sessions_root):
            return
        record.pop("runtime_session", None)
        record.pop("runtime_session_id", None)
        self.state.save()

    def _reply(
        self,
        message: SocialMessage,
        text: str,
        *,
        runtime_session_id: str | None = None,
        runtime_session_path: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SocialReply:
        return SocialReply(
            text=text,
            platform=message.platform,
            conversation_id=message.conversation_id,
            target_id=message.reply_target_id or message.conversation_id,
            runtime_session_id=runtime_session_id,
            runtime_session_path=runtime_session_path,
            metadata=metadata or {},
        )

    def _help(self) -> str:
        return (
            "EchoWeave social commands:\n"
            "/help - show help\n"
            "/models - list model profiles\n"
            "/model <profile> - switch this conversation to a model profile\n"
            "/approvals - list pending approvals\n"
            "/approve <id> - approve and execute a pending command\n"
            "/deny <id> - deny a pending command\n"
            "/revoke <id> - revoke a pending or approved command record\n"
            "/retry <id> - create a new pending approval from a resolved record\n"
            "/rag on|off - toggle retrieval-augmented context for this conversation\n"
            "/rag index - index current workspace for the configured RAG backend\n"
            "/status - show current binding and runtime session\n"
            "/bind <workspace> - bind this social conversation to a repository\n"
            "/unbind - return this conversation to its isolated sandbox\n"
            "/new - start a new EchoWeave runtime session\n"
            "/agent <prompt> - send a prompt to EchoWeave\n"
            "Any non-command message is treated as an agent prompt."
        )


def _safe_workspace_slug(conversation_key: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", conversation_key).strip(".-")
    digest = hashlib.sha256(conversation_key.encode("utf-8")).hexdigest()[:12]
    prefix = normalized[:80] or "conversation"
    return f"{prefix}-{digest}"


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]


def _now_ts() -> float:
    return time.time()


def _run_approved_shell(command: str, cwd: Path) -> str:
    if not command.strip():
        raise ValueError("approval command is empty")
    if not cwd.exists() or not cwd.is_dir():
        raise ValueError(f"approval cwd does not exist: {cwd}")
    process = subprocess.run(
        command,
        cwd=cwd,
        shell=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    output = ((process.stdout or "") + (process.stderr or "")).strip()
    if process.returncode != 0:
        if output:
            return f"{output}\nCommand exited with code {process.returncode}"
        return f"Command exited with code {process.returncode}"
    return output


def _render_approval_notice(approvals: list[dict[str, Any]]) -> str:
    lines = ["Pending approval required:"]
    for item in approvals:
        lines.append(f"{item['id']} - {item.get('command')}")
        lines.append(f"reason: {item.get('reason')}")
    lines.append("Admin can reply /approve <id> or /deny <id>.")
    return "\n".join(lines)
