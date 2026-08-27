from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from echoweave_ai import register_ai_providers_from_config
from echoweave_agent_core import OrphanRecoveryConfig
from echoweave_harness.audit import configure_audit, read_audit_events, record_audit
from echoweave_harness.feedback import suggest_harness_improvements, write_eval_fixtures, write_feedback_backlog
from echoweave_harness.metrics import compute_harness_metrics
from echoweave_harness.policy import configure_harness_policy
from echoweave_runtime.events import InboundMessage, OutboundMessage
from echoweave_social.access_control import AccessDecision, AccessPolicy, DEFAULT_ADMIN_ONLY_COMMANDS
from echoweave_social.agent_runtime import SocialAgentConfig, EchoWeaveSocialAgent
from echoweave_social.recovery import SocialRecoveryController


KEEP_EXISTING_API_KEY = "__ECHOWEAVE_KEEP_EXISTING_API_KEY__"


class AgentBackend(Protocol):
    def handle(self, event: InboundMessage) -> OutboundMessage:
        """Route one normalized platform event to the agent backend."""


@dataclass(frozen=True)
class EchoWeaveBackendConfig:
    default_workspace: Path
    config_path: Path | None = None
    state_path: Path | None = None
    sandbox_root: Path | None = None
    provider: str = "demo"
    model: str | None = None
    ai_providers: dict[str, dict[str, Any]] | None = None
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
    admins: tuple[str, ...] = ()
    allowed_users: tuple[str, ...] = ()
    allowed_groups: tuple[str, ...] = ()
    blocked_users: tuple[str, ...] = ()
    require_mention_in_group: bool = False
    bot_ids: tuple[str, ...] = ()
    admin_only_commands: tuple[str, ...] = DEFAULT_ADMIN_ONLY_COMMANDS
    approval_timeout_seconds: int = 3600
    orphan_recovery_enabled: bool = False
    orphan_recovery_scan_interval_seconds: float = 30.0
    orphan_recovery_max_per_scan: int = 4
    orphan_recovery_max_attempts_per_turn: int = 3
    harness_audit_enabled: bool = True
    harness_audit_path: Path | None = None
    harness_policy: dict[str, Any] | None = None


class EchoWeaveBackend:
    """Bridge from EchoWeave events into the embedded agent runtime."""

    name = "agent-backend"

    def __init__(self, config: EchoWeaveBackendConfig) -> None:
        self._lifecycle_lock = threading.RLock()
        self._started = False
        self._recovery: SocialRecoveryController | None = None
        self._config = config
        self._apply_config(config)

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._started:
                return
            self._started = True
            self._start_recovery_locked()

    def stop(self) -> None:
        with self._lifecycle_lock:
            if not self._started:
                return
            self._stop_recovery_locked()
            self._started = False

    def _apply_config(self, config: EchoWeaveBackendConfig) -> None:
        audit_path = (
            config.harness_audit_path
            or config.default_workspace / "echoweave-data" / "audit.jsonl"
        )
        configure_audit(audit_path if config.harness_audit_enabled else None)
        register_ai_providers_from_config(config.ai_providers)
        harness_policy = configure_harness_policy(config.harness_policy)
        self._access_policy = AccessPolicy(
            admins=config.admins,
            allowed_users=config.allowed_users,
            allowed_groups=config.allowed_groups,
            blocked_users=config.blocked_users,
            require_mention_in_group=config.require_mention_in_group,
            bot_ids=config.bot_ids,
            admin_only_commands=config.admin_only_commands,
        )
        self._agent = EchoWeaveSocialAgent(
            SocialAgentConfig(
                default_workspace=config.default_workspace,
                state_path=config.state_path,
                sandbox_root=config.sandbox_root,
                provider=config.provider,
                model=config.model,
                model_profiles=config.model_profiles,
                default_model_profile=config.default_model_profile,
                rag_enabled=config.rag_enabled,
                rag_backend=config.rag_backend,
                rag_pgvector_dsn=config.rag_pgvector_dsn,
                rag_pgvector_table=config.rag_pgvector_table,
                rag_embedding_model=config.rag_embedding_model,
                rag_auto_index=config.rag_auto_index,
                rag_vector_weight=config.rag_vector_weight,
                rag_bm25_weight=config.rag_bm25_weight,
                rag_query_rewrite_enabled=config.rag_query_rewrite_enabled,
                rag_query_rewrite_strategy=config.rag_query_rewrite_strategy,
                rag_query_rewrite_max_queries=config.rag_query_rewrite_max_queries,
                rag_rerank_enabled=config.rag_rerank_enabled,
                rag_rerank_strategy=config.rag_rerank_strategy,
                rag_rerank_candidate_multiplier=config.rag_rerank_candidate_multiplier,
                rag_rerank_original_score_weight=config.rag_rerank_original_score_weight,
                rag_rerank_bm25_weight=config.rag_rerank_bm25_weight,
                global_enabled_skills=config.global_enabled_skills,
                session_enabled_skills=config.session_enabled_skills,
                approval_timeout_seconds=config.approval_timeout_seconds,
                harness_policy=harness_policy,
            )
        )

    def handle(self, event: InboundMessage) -> OutboundMessage:
        record_audit(
            "message",
            "inbound",
            status="ok",
            conversation_id=event.conversation_id,
            actor_id=event.sender_id,
            metadata={"platform": event.platform, "message_id": event.message_id, "text_chars": len(event.text)},
        )
        decision = self._access_policy.check(event)
        if not decision.allowed:
            record_audit(
                "access",
                "decision",
                status="denied",
                conversation_id=event.conversation_id,
                actor_id=event.sender_id,
                metadata={"reason": decision.reason, "reason_code": decision.reason_code},
            )
            return self._access_denied_reply(event, decision)
        reply = self._agent.handle(event)
        record_audit(
            "message",
            "reply",
            status="ok",
            conversation_id=event.conversation_id,
            actor_id=event.sender_id,
            metadata={"platform": event.platform, "reply_chars": len(reply.text)},
        )
        return replace(
            reply,
            metadata={
                **reply.metadata,
                "event_raw": event.raw,
                "runtime_session_id": reply.runtime_session_id,
                "runtime_session_path": reply.runtime_session_path,
            },
        )

    def admin_status(self) -> dict[str, object]:
        approvals = self._agent.list_approvals(limit=100)
        pending_count = sum(1 for item in approvals if item.get("status") == "pending")
        config = self.admin_config()
        return {
            "ok": True,
            "service": "EchoWeave",
            "config": config,
            "recovery": self.recovery_status(),
            "approvals": {
                "pending": pending_count,
                "recent": approvals[:20],
            },
        }

    def recovery_status(self) -> dict[str, object]:
        with self._lifecycle_lock:
            recovery = self._recovery
            enabled = self._config.orphan_recovery_enabled
            started = self._started
            if recovery is None:
                return {
                    "enabled": enabled,
                    "backend_started": started,
                    "running": False,
                    "config": self._recovery_config_payload(),
                    "stats": {},
                    "recent_results": [],
                }
            return {
                "enabled": enabled,
                "backend_started": started,
                **recovery.status(),
            }

    def scan_recovery(self, *, schedule: bool = True) -> dict[str, object]:
        with self._lifecycle_lock:
            recovery = self._recovery
            ephemeral = recovery is None
            if recovery is None:
                recovery = self._build_recovery_controller()
            result = recovery.scan_now(schedule=schedule and not ephemeral)
            return {
                "ok": True,
                "enabled": self._config.orphan_recovery_enabled,
                **result,
            }

    def admin_config(self) -> dict[str, object]:
        data = asdict(self._config)
        for key, value in list(data.items()):
            if isinstance(value, Path):
                data[key] = str(value)
            elif isinstance(value, tuple):
                data[key] = list(value)
        data["model_profiles"] = _redact_model_profiles(self._config.model_profiles)
        return data

    def web_capabilities(
        self,
        *,
        platform: str = "web-user",
        conversation_id: str = "web-coding",
        sender_id: str = "web-admin",
    ) -> dict[str, object]:
        message = InboundMessage(platform=platform, conversation_id=conversation_id, sender_id=sender_id, text="")
        profiles = self._agent._model_profiles()
        profile_items: dict[str, dict[str, object]] = {}
        for name, profile in profiles.items():
            profile_items[name] = {
                **_redact_model_profile(profile),
                "diagnostics": self._model_profile_diagnostics(profile),
            }
        skills = []
        enabled_skills = self._agent._enabled_skills(message)
        global_enabled = self._agent._global_enabled_skills()
        for spec in self._agent._available_skills(message):
            skills.append(
                {
                    "name": spec.name,
                    "description": spec.description,
                    "enabled": spec.name in enabled_skills,
                    "global_enabled": spec.name in global_enabled,
                }
            )
        return {
            "ok": True,
            "models": {
                "current": self._agent._selected_model_profile_name(message),
                "profiles": profile_items,
            },
            "rag": {
                "enabled": self._agent._rag_enabled(message),
                "backend": self._config.rag_backend,
                "pgvector_configured": bool(self._config.rag_pgvector_dsn),
                "query_rewrite_enabled": self._config.rag_query_rewrite_enabled,
                "rerank_enabled": self._config.rag_rerank_enabled,
            },
            "skills": skills,
        }

    def _model_profile_diagnostics(self, profile: dict[str, Any]) -> dict[str, object]:
        provider = str(profile.get("provider") or self._config.provider or "demo")
        model = profile["model"] if "model" in profile else self._config.model
        api_key_env = _profile_api_key_env(provider, profile, self._config.ai_providers)
        configured = True
        if api_key_env:
            configured = bool(profile.get("api_key") or os.getenv(api_key_env))
        return {
            "provider": provider,
            "model": str(model) if model else None,
            "api_key_env": api_key_env,
            "api_key_configured": configured,
            "base_url": profile.get("base_url") or _profile_base_url(provider, self._config.ai_providers),
        }

    def update_admin_config(self, patch: dict[str, object]) -> dict[str, object]:
        allowed = {
            "provider",
            "model",
            "ai_providers",
            "model_profiles",
            "default_model_profile",
            "rag_enabled",
            "rag_backend",
            "rag_pgvector_dsn",
            "rag_pgvector_table",
            "rag_embedding_model",
            "rag_auto_index",
            "rag_vector_weight",
            "rag_bm25_weight",
            "rag_query_rewrite_enabled",
            "rag_query_rewrite_strategy",
            "rag_query_rewrite_max_queries",
            "rag_rerank_enabled",
            "rag_rerank_strategy",
            "rag_rerank_candidate_multiplier",
            "rag_rerank_original_score_weight",
            "rag_rerank_bm25_weight",
            "global_enabled_skills",
            "session_enabled_skills",
            "sandbox_root",
            "admins",
            "allowed_users",
            "allowed_groups",
            "blocked_users",
            "require_mention_in_group",
            "bot_ids",
            "admin_only_commands",
            "approval_timeout_seconds",
            "orphan_recovery_enabled",
            "orphan_recovery_scan_interval_seconds",
            "orphan_recovery_max_per_scan",
            "orphan_recovery_max_attempts_per_turn",
            "harness_audit_enabled",
            "harness_audit_path",
            "harness_policy",
        }
        updates: dict[str, object] = {}
        for key, value in patch.items():
            if key not in allowed:
                continue
            if key == "sandbox_root":
                updates[key] = Path(str(value)).expanduser().resolve() if value else None
            elif key == "harness_audit_path":
                updates[key] = Path(str(value)).expanduser().resolve() if value else None
            elif key == "model_profiles":
                updates[key] = _merge_model_profiles(value, self._config.model_profiles)
            elif key in {
                "global_enabled_skills",
                "session_enabled_skills",
                "admins",
                "allowed_users",
                "allowed_groups",
                "blocked_users",
                "bot_ids",
                "admin_only_commands",
            }:
                updates[key] = tuple(str(item).strip() for item in value if str(item).strip()) if isinstance(value, list) else ()
            elif key == "approval_timeout_seconds":
                updates[key] = max(1, int(value or 3600))
            elif key == "orphan_recovery_scan_interval_seconds":
                updates[key] = max(0.1, float(value or 30.0))
            elif key == "orphan_recovery_max_per_scan":
                updates[key] = max(1, int(value or 4))
            elif key == "orphan_recovery_max_attempts_per_turn":
                updates[key] = max(2, int(value or 3))
            elif key in {"rag_query_rewrite_max_queries", "rag_rerank_candidate_multiplier"}:
                updates[key] = max(1, int(value or 1))
            elif key in {"rag_vector_weight", "rag_bm25_weight", "rag_rerank_original_score_weight", "rag_rerank_bm25_weight"}:
                updates[key] = float(value)
            else:
                updates[key] = value
        if updates:
            with self._lifecycle_lock:
                restart_recovery = self._started
                if restart_recovery:
                    self._stop_recovery_locked()
                self._config = replace(self._config, **updates)
                self._apply_config(self._config)
                if restart_recovery:
                    self._start_recovery_locked()
                self._persist_config(updates)
        return self.admin_config()

    def _build_recovery_controller(self) -> SocialRecoveryController:
        return SocialRecoveryController(
            self._agent,
            OrphanRecoveryConfig(
                scan_interval_seconds=self._config.orphan_recovery_scan_interval_seconds,
                max_concurrent_recoveries=1,
                max_recoveries_per_scan=self._config.orphan_recovery_max_per_scan,
                max_attempts_per_turn=self._config.orphan_recovery_max_attempts_per_turn,
            ),
        )

    def _start_recovery_locked(self) -> None:
        if (
            not self._started
            or not self._config.orphan_recovery_enabled
            or self._recovery is not None
        ):
            return
        recovery = self._build_recovery_controller()
        recovery.start()
        self._recovery = recovery

    def _stop_recovery_locked(self) -> None:
        recovery = self._recovery
        self._recovery = None
        if recovery is not None:
            recovery.stop()

    def _recovery_config_payload(self) -> dict[str, object]:
        return {
            "scan_interval_seconds": self._config.orphan_recovery_scan_interval_seconds,
            "max_concurrent_recoveries": 1,
            "max_recoveries_per_scan": self._config.orphan_recovery_max_per_scan,
            "max_attempts_per_turn": self._config.orphan_recovery_max_attempts_per_turn,
        }

    def _persist_config(self, updates: dict[str, object]) -> None:
        if self._config.config_path is None:
            return
        path = self._config.config_path
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, json.JSONDecodeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        for key, value in updates.items():
            data[key] = _jsonable_config_value(value)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def list_approvals(self, limit: int = 50) -> list[dict[str, object]]:
        return self._agent.list_approvals(limit=limit)

    def approve_approval(self, approval_id: str, *, actor_id: str = "web-admin") -> str:
        return self._agent.approve_approval(approval_id, actor_id=actor_id)

    def deny_approval(self, approval_id: str, *, actor_id: str = "web-admin") -> str:
        return self._agent.deny_approval(approval_id, actor_id=actor_id)

    def revoke_approval(self, approval_id: str, *, actor_id: str = "web-admin") -> str:
        return self._agent.revoke_approval(approval_id, actor_id=actor_id)

    def retry_approval(self, approval_id: str, *, actor_id: str = "web-admin") -> str:
        return self._agent.retry_approval(approval_id, actor_id=actor_id)

    def audit_summary(self) -> dict[str, object]:
        audit_path = self._config.harness_audit_path or self._config.default_workspace / "echoweave-data" / "audit.jsonl"
        events = read_audit_events(audit_path)
        metrics = compute_harness_metrics(events)
        suggestions = suggest_harness_improvements(events)
        return {
            "ok": True,
            "audit_log": str(audit_path),
            "event_count": len(events),
            "metrics": metrics.to_dict(),
            "suggestions": [item.to_dict() for item in suggestions],
        }

    def generate_hardening_plan(
        self,
        *,
        feedback_log: str | None = None,
        eval_out: str | None = None,
    ) -> dict[str, object]:
        audit_path = self._config.harness_audit_path or self._config.default_workspace / "echoweave-data" / "audit.jsonl"
        events = read_audit_events(audit_path)
        suggestions = suggest_harness_improvements(events)
        feedback_written = write_feedback_backlog(feedback_log, suggestions, source_audit_log=str(audit_path)) if feedback_log else 0
        eval_written = write_eval_fixtures(eval_out, suggestions) if eval_out else 0
        return {
            "ok": True,
            "audit_log": str(audit_path),
            "suggestions": [item.to_dict() for item in suggestions],
            "feedback_written": feedback_written,
            "eval_fixture_written": eval_written,
            "feedback_log": feedback_log,
            "eval_out": eval_out,
        }

    def _access_denied_reply(self, event: InboundMessage, decision: AccessDecision) -> OutboundMessage:
        text = "" if decision.silent else f"Access denied: {decision.reason}"
        return OutboundMessage(
            text=text,
            platform=event.platform,
            conversation_id=event.conversation_id,
            target_id=event.reply_target_id or event.conversation_id,
            metadata={
                "access": {
                    "allowed": False,
                    "reason": decision.reason,
                    "reason_code": decision.reason_code,
                    "silent": decision.silent,
                }
            },
        )


def _jsonable_config_value(value: object) -> object:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return list(value)
    return value


def _redact_model_profiles(value: dict[str, dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for name, raw in value.items():
        if isinstance(name, str) and isinstance(raw, dict):
            result[name] = _redact_model_profile(raw)
    return result


def _redact_model_profile(profile: dict[str, Any]) -> dict[str, Any]:
    result = dict(profile)
    api_key = result.pop("api_key", None)
    result["api_key_configured"] = bool(api_key)
    return result


def _merge_model_profiles(
    value: object,
    existing: dict[str, dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    current = existing or {}
    result: dict[str, dict[str, Any]] = {}
    for name, raw in value.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(raw, dict):
            continue
        item = dict(raw)
        clear_api_key = bool(item.pop("clear_api_key", False))
        keep_configured_api_key = item.pop("api_key_configured", None) is True
        api_key = item.get("api_key")
        old_api_key = current.get(name, {}).get("api_key") if isinstance(current.get(name), dict) else None
        if clear_api_key:
            item.pop("api_key", None)
        elif api_key == KEEP_EXISTING_API_KEY or (api_key is None and keep_configured_api_key):
            if isinstance(old_api_key, str) and old_api_key:
                item["api_key"] = old_api_key
            else:
                item.pop("api_key", None)
        elif isinstance(api_key, str) and api_key:
            item["api_key"] = api_key
        else:
            item.pop("api_key", None)
        result[name.strip()] = item
    return result


def _profile_api_key_env(
    provider: str,
    profile: dict[str, Any],
    ai_providers: dict[str, dict[str, Any]] | None,
) -> str | None:
    if provider in {"demo"}:
        return None
    if provider == "ollama":
        return str(profile["api_key_env"]) if isinstance(profile.get("api_key_env"), str) and profile.get("api_key_env") else None
    if isinstance(profile.get("api_key_env"), str) and profile.get("api_key_env"):
        return str(profile["api_key_env"])
    configured = (ai_providers or {}).get(provider)
    if isinstance(configured, dict) and configured.get("api_key_env"):
        return str(configured["api_key_env"])
    return {
        "deepseek": "DEEPSEEK_API_KEY",
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "siliconflow": "SILICONFLOW_API_KEY",
        "openai-compatible": "OPENAI_API_KEY",
    }.get(provider, "OPENAI_API_KEY")


def _profile_base_url(provider: str, ai_providers: dict[str, dict[str, Any]] | None) -> str | None:
    configured = (ai_providers or {}).get(provider)
    if isinstance(configured, dict) and configured.get("base_url"):
        return str(configured["base_url"])
    return {
        "deepseek": "https://api.deepseek.com",
        "openrouter": "https://openrouter.ai/api/v1",
        "siliconflow": "https://api.siliconflow.cn/v1",
        "ollama": "http://127.0.0.1:11434/v1",
    }.get(provider)
