from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import typer

from echoweave_social.adapters.astrbot_event import AstrBotEventAdapter
from echoweave_social.adapters.feishu import FeishuAdapter
from echoweave_social.adapters.generic_webhook import GenericWebhookAdapter
from echoweave_social.adapters.onebot_v11 import OneBotV11Adapter
from echoweave_social.adapters.wechat_official import WeChatOfficialAdapter
from echoweave_social.backend import EchoWeaveBackend, EchoWeaveBackendConfig


def setup_logging(log_file: Path | None = None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=handlers,
        force=True,
    )


def create_adapter(name: str):
    normalized = name.lower().replace("_", "-")
    if normalized in {"generic", "webhook"}:
        return GenericWebhookAdapter()
    if normalized in {"astrbot", "astrbot-event"}:
        return AstrBotEventAdapter()
    if normalized in {"feishu", "lark"}:
        return FeishuAdapter()
    if normalized in {"wechat", "wechat-official", "weixin", "wx", "mp"}:
        return WeChatOfficialAdapter()
    if normalized in {"onebot", "onebot-v11", "qq"}:
        return OneBotV11Adapter()
    raise typer.BadParameter(f"Unknown adapter: {name}")


def build_backend(
    cwd: str,
    provider: str,
    model: str | None,
    state_path: str | None,
    config_path: str | None = None,
    sandbox_root: str | None = None,
    ai_providers: dict[str, dict[str, Any]] | None = None,
    model_profiles: dict[str, dict[str, Any]] | None = None,
    default_model_profile: str | None = None,
    rag_enabled: bool = False,
    rag_backend: str = "pgvector_hybrid_bgem3",
    rag_pgvector_dsn: str | None = None,
    rag_pgvector_table: str = "echoweave_rag_chunks",
    rag_embedding_model: str = "BAAI/bge-m3",
    rag_auto_index: bool = False,
    rag_vector_weight: float = 0.65,
    rag_bm25_weight: float = 0.35,
    rag_query_rewrite_enabled: bool = False,
    rag_query_rewrite_strategy: str = "local_multi_query",
    rag_query_rewrite_max_queries: int = 3,
    rag_rerank_enabled: bool = False,
    rag_rerank_strategy: str = "bm25",
    rag_rerank_candidate_multiplier: int = 4,
    rag_rerank_original_score_weight: float = 0.65,
    rag_rerank_bm25_weight: float = 0.35,
    global_enabled_skills: tuple[str, ...] = (),
    session_enabled_skills: tuple[str, ...] = (),
    admins: tuple[str, ...] = (),
    allowed_users: tuple[str, ...] = (),
    allowed_groups: tuple[str, ...] = (),
    blocked_users: tuple[str, ...] = (),
    require_mention_in_group: bool = False,
    bot_ids: tuple[str, ...] = (),
    admin_only_commands: tuple[str, ...] = (),
    approval_timeout_seconds: int = 3600,
    orphan_recovery_enabled: bool = False,
    orphan_recovery_scan_interval_seconds: float = 30.0,
    orphan_recovery_max_per_scan: int = 4,
    orphan_recovery_max_attempts_per_turn: int = 3,
    harness_audit_enabled: bool = True,
    harness_audit_path: str | None = None,
    harness_policy: dict[str, Any] | None = None,
) -> EchoWeaveBackend:
    return EchoWeaveBackend(
        EchoWeaveBackendConfig(
            default_workspace=Path(cwd).resolve(),
            config_path=Path(config_path).resolve() if config_path else None,
            state_path=Path(state_path).resolve() if state_path else None,
            sandbox_root=Path(sandbox_root).resolve() if sandbox_root else None,
            provider=provider,
            model=model,
            ai_providers=ai_providers,
            model_profiles=model_profiles,
            default_model_profile=default_model_profile,
            rag_enabled=rag_enabled,
            rag_backend=rag_backend,
            rag_pgvector_dsn=rag_pgvector_dsn,
            rag_pgvector_table=rag_pgvector_table,
            rag_embedding_model=rag_embedding_model,
            rag_auto_index=rag_auto_index,
            rag_vector_weight=rag_vector_weight,
            rag_bm25_weight=rag_bm25_weight,
            rag_query_rewrite_enabled=rag_query_rewrite_enabled,
            rag_query_rewrite_strategy=rag_query_rewrite_strategy,
            rag_query_rewrite_max_queries=rag_query_rewrite_max_queries,
            rag_rerank_enabled=rag_rerank_enabled,
            rag_rerank_strategy=rag_rerank_strategy,
            rag_rerank_candidate_multiplier=rag_rerank_candidate_multiplier,
            rag_rerank_original_score_weight=rag_rerank_original_score_weight,
            rag_rerank_bm25_weight=rag_rerank_bm25_weight,
            global_enabled_skills=global_enabled_skills,
            session_enabled_skills=session_enabled_skills,
            admins=admins,
            allowed_users=allowed_users,
            allowed_groups=allowed_groups,
            blocked_users=blocked_users,
            require_mention_in_group=require_mention_in_group,
            bot_ids=bot_ids,
            admin_only_commands=admin_only_commands or (
                "approve",
                "approvals",
                "bind",
                "deny",
                "rag:index",
                "retry",
                "revoke",
                "skill:global",
            ),
            approval_timeout_seconds=approval_timeout_seconds,
            orphan_recovery_enabled=orphan_recovery_enabled,
            orphan_recovery_scan_interval_seconds=orphan_recovery_scan_interval_seconds,
            orphan_recovery_max_per_scan=orphan_recovery_max_per_scan,
            orphan_recovery_max_attempts_per_turn=orphan_recovery_max_attempts_per_turn,
            harness_audit_enabled=harness_audit_enabled,
            harness_audit_path=Path(harness_audit_path).resolve() if harness_audit_path else None,
            harness_policy=harness_policy,
        )
    )
