from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class EchoWeaveConfig:
    adapter: str = "generic"
    workspace: Path = Path(".")
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
    state_path: Path | None = None
    sandbox_root: Path | None = None
    host: str = "127.0.0.1"
    port: int = 8787
    webhook_token: str | None = None
    web_allow_url_token: bool = False
    web_session_ttl_seconds: int = 28800
    onebot_api_url: str | None = None
    onebot_access_token: str | None = None
    sse_enabled: bool = True
    log_file: Path | None = None
    admins: tuple[str, ...] = ()
    allowed_users: tuple[str, ...] = ()
    allowed_groups: tuple[str, ...] = ()
    blocked_users: tuple[str, ...] = ()
    require_mention_in_group: bool = False
    bot_ids: tuple[str, ...] = ()
    admin_only_commands: tuple[str, ...] = ()
    approval_timeout_seconds: int = 3600
    orphan_recovery_enabled: bool = False
    orphan_recovery_scan_interval_seconds: float = 30.0
    orphan_recovery_max_per_scan: int = 4
    orphan_recovery_max_attempts_per_turn: int = 3
    harness_audit_enabled: bool = True
    harness_audit_path: Path | None = None
    harness_policy: dict[str, Any] | None = None

    @staticmethod
    def load(path: str | None = None) -> "EchoWeaveConfig":
        data: dict[str, Any] = {}
        if path:
            config_path = Path(path).expanduser().resolve()
            with config_path.open("r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if not isinstance(loaded, dict):
                raise ValueError("EchoWeave config file must contain a JSON object.")
            data.update(loaded)

        env = {
            "adapter": _env("ADAPTER"),
            "workspace": _env("WORKSPACE"),
            "provider": _env("PROVIDER"),
            "model": _env("MODEL"),
            "ai_providers": _env("AI_PROVIDERS"),
            "default_model_profile": _env("DEFAULT_MODEL_PROFILE"),
            "rag_enabled": _env("RAG_ENABLED"),
            "rag_backend": _env("RAG_BACKEND"),
            "rag_pgvector_dsn": _env("RAG_PGVECTOR_DSN"),
            "rag_pgvector_table": _env("RAG_PGVECTOR_TABLE"),
            "rag_embedding_model": _env("RAG_EMBEDDING_MODEL"),
            "rag_auto_index": _env("RAG_AUTO_INDEX"),
            "rag_vector_weight": _env("RAG_VECTOR_WEIGHT"),
            "rag_bm25_weight": _env("RAG_BM25_WEIGHT"),
            "rag_query_rewrite_enabled": _env("RAG_QUERY_REWRITE_ENABLED"),
            "rag_query_rewrite_strategy": _env("RAG_QUERY_REWRITE_STRATEGY"),
            "rag_query_rewrite_max_queries": _env("RAG_QUERY_REWRITE_MAX_QUERIES"),
            "rag_rerank_enabled": _env("RAG_RERANK_ENABLED"),
            "rag_rerank_strategy": _env("RAG_RERANK_STRATEGY"),
            "rag_rerank_candidate_multiplier": _env("RAG_RERANK_CANDIDATE_MULTIPLIER"),
            "rag_rerank_original_score_weight": _env("RAG_RERANK_ORIGINAL_SCORE_WEIGHT"),
            "rag_rerank_bm25_weight": _env("RAG_RERANK_BM25_WEIGHT"),
            "global_enabled_skills": _env("GLOBAL_ENABLED_SKILLS"),
            "session_enabled_skills": _env("SESSION_ENABLED_SKILLS"),
            "state_path": _env("STATE_PATH"),
            "sandbox_root": _env("SANDBOX_ROOT"),
            "host": _env("HOST"),
            "port": _env("PORT"),
            "webhook_token": _env("WEBHOOK_TOKEN"),
            "web_allow_url_token": _env("WEB_ALLOW_URL_TOKEN"),
            "web_session_ttl_seconds": _env("WEB_SESSION_TTL_SECONDS"),
            "onebot_api_url": _env("ONEBOT_API_URL"),
            "onebot_access_token": _env("ONEBOT_ACCESS_TOKEN"),
            "sse_enabled": _env("SSE_ENABLED"),
            "log_file": _env("LOG_FILE"),
            "admins": _env("ADMINS"),
            "allowed_users": _env("ALLOWED_USERS"),
            "allowed_groups": _env("ALLOWED_GROUPS"),
            "blocked_users": _env("BLOCKED_USERS"),
            "require_mention_in_group": _env("REQUIRE_MENTION_IN_GROUP"),
            "bot_ids": _env("BOT_IDS"),
            "admin_only_commands": _env("ADMIN_ONLY_COMMANDS"),
            "approval_timeout_seconds": _env("APPROVAL_TIMEOUT_SECONDS"),
            "orphan_recovery_enabled": _env("ORPHAN_RECOVERY_ENABLED"),
            "orphan_recovery_scan_interval_seconds": _env(
                "ORPHAN_RECOVERY_SCAN_INTERVAL_SECONDS"
            ),
            "orphan_recovery_max_per_scan": _env("ORPHAN_RECOVERY_MAX_PER_SCAN"),
            "orphan_recovery_max_attempts_per_turn": _env(
                "ORPHAN_RECOVERY_MAX_ATTEMPTS_PER_TURN"
            ),
            "harness_audit_enabled": _env("HARNESS_AUDIT_ENABLED"),
            "harness_audit_path": _env("HARNESS_AUDIT_PATH"),
        }
        data.update({key: value for key, value in env.items() if value not in {None, ""}})
        return EchoWeaveConfig.from_mapping(data)

    @staticmethod
    def from_mapping(data: dict[str, Any]) -> "EchoWeaveConfig":
        return EchoWeaveConfig(
            adapter=str(data.get("adapter") or "generic"),
            workspace=_path(data.get("workspace")) or Path("."),
            provider=str(data.get("provider") or "demo"),
            model=_optional_str(data.get("model")),
            ai_providers=_dict_of_dicts(data.get("ai_providers")),
            model_profiles=_model_profiles(data.get("model_profiles")),
            default_model_profile=_optional_str(data.get("default_model_profile")),
            rag_enabled=_bool(data.get("rag_enabled"), default=False),
            rag_backend=str(data.get("rag_backend") or "pgvector_hybrid_bgem3"),
            rag_pgvector_dsn=_optional_str(data.get("rag_pgvector_dsn")),
            rag_pgvector_table=str(data.get("rag_pgvector_table") or "echoweave_rag_chunks"),
            rag_embedding_model=str(data.get("rag_embedding_model") or "BAAI/bge-m3"),
            rag_auto_index=_bool(data.get("rag_auto_index"), default=False),
            rag_vector_weight=_float(data.get("rag_vector_weight"), default=0.65),
            rag_bm25_weight=_float(data.get("rag_bm25_weight"), default=0.35),
            rag_query_rewrite_enabled=_bool(data.get("rag_query_rewrite_enabled"), default=False),
            rag_query_rewrite_strategy=str(data.get("rag_query_rewrite_strategy") or "local_multi_query"),
            rag_query_rewrite_max_queries=_int(data.get("rag_query_rewrite_max_queries"), default=3),
            rag_rerank_enabled=_bool(data.get("rag_rerank_enabled"), default=False),
            rag_rerank_strategy=str(data.get("rag_rerank_strategy") or "bm25"),
            rag_rerank_candidate_multiplier=_int(data.get("rag_rerank_candidate_multiplier"), default=4),
            rag_rerank_original_score_weight=_float(data.get("rag_rerank_original_score_weight"), default=0.65),
            rag_rerank_bm25_weight=_float(data.get("rag_rerank_bm25_weight"), default=0.35),
            global_enabled_skills=_str_tuple(data.get("global_enabled_skills")),
            session_enabled_skills=_str_tuple(data.get("session_enabled_skills")),
            state_path=_path(data.get("state_path")),
            sandbox_root=_path(data.get("sandbox_root")),
            host=str(data.get("host") or "127.0.0.1"),
            port=int(data.get("port") or 8787),
            webhook_token=_optional_str(data.get("webhook_token")),
            web_allow_url_token=_bool(data.get("web_allow_url_token"), default=False),
            web_session_ttl_seconds=_int(data.get("web_session_ttl_seconds"), default=28800),
            onebot_api_url=_optional_str(data.get("onebot_api_url")),
            onebot_access_token=_optional_str(data.get("onebot_access_token")),
            sse_enabled=_bool(data.get("sse_enabled"), default=True),
            log_file=_path(data.get("log_file")),
            admins=_str_tuple(data.get("admins")),
            allowed_users=_str_tuple(data.get("allowed_users")),
            allowed_groups=_str_tuple(data.get("allowed_groups")),
            blocked_users=_str_tuple(data.get("blocked_users")),
            require_mention_in_group=_bool(data.get("require_mention_in_group"), default=False),
            bot_ids=_str_tuple(data.get("bot_ids")),
            admin_only_commands=_str_tuple(data.get("admin_only_commands")),
            approval_timeout_seconds=_int(data.get("approval_timeout_seconds"), default=3600),
            orphan_recovery_enabled=_bool(data.get("orphan_recovery_enabled"), default=False),
            orphan_recovery_scan_interval_seconds=max(
                0.1,
                _float(data.get("orphan_recovery_scan_interval_seconds"), default=30.0),
            ),
            orphan_recovery_max_per_scan=max(
                1,
                _int(data.get("orphan_recovery_max_per_scan"), default=4),
            ),
            orphan_recovery_max_attempts_per_turn=max(
                2,
                _int(data.get("orphan_recovery_max_attempts_per_turn"), default=3),
            ),
            harness_audit_enabled=_bool(data.get("harness_audit_enabled"), default=True),
            harness_audit_path=_path(data.get("harness_audit_path")),
            harness_policy=data.get("harness_policy") if isinstance(data.get("harness_policy"), dict) else None,
        )


def _path(value: Any) -> Path | None:
    if value is None or value == "":
        return None
    return Path(str(value)).expanduser().resolve()


def _optional_str(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _bool(value: Any, *, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enable", "enabled"}
    return bool(value)


def _float(value: Any, *, default: float) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, *, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _model_profiles(value: Any) -> dict[str, dict[str, Any]] | None:
    if not isinstance(value, dict):
        return None
    profiles: dict[str, dict[str, Any]] = {}
    for name, raw_profile in value.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(raw_profile, dict):
            continue
        profile: dict[str, Any] = {
            "provider": _optional_str(raw_profile.get("provider")),
            "model": _optional_str(raw_profile.get("model")),
        }
        for key in (
            "base_url",
            "api_key_env",
            "api_key",
            "label",
            "description",
            "supports_stream",
        ):
            if key in raw_profile:
                profile[key] = raw_profile[key]
        profiles[name.strip()] = profile
    return profiles or None


def _dict_of_dicts(value: Any) -> dict[str, dict[str, Any]] | None:
    if isinstance(value, str) and value.strip():
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    if not isinstance(value, dict):
        return None
    result: dict[str, dict[str, Any]] = {}
    for key, item in value.items():
        if isinstance(key, str) and key.strip() and isinstance(item, dict):
            result[key.strip()] = dict(item)
    return result or None


def _str_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",")]
        return tuple(part for part in parts if part)
    if isinstance(value, list):
        return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())
    return ()


def _env(name: str) -> str | None:
    return os.getenv(f"ECHOWEAVE_{name}")
