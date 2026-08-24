from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from echoweave_runtime.extensions.base import (
    ALL_EXTENSION_HOOKS,
    ExtensionContext,
    ExtensionHook,
    ExtensionHookHandler,
    ExtensionToolSpec,
    McpProvider,
    MemoryProvider,
    RetrievalProvider,
    SkillProvider,
)
from echoweave_runtime.extensions.memory_provider import InMemoryContextProvider
from echoweave_runtime.extensions.mcp_provider import LocalMcpProvider
from echoweave_runtime.extensions.retrieval_provider import LexicalRetrievalProvider
from echoweave_runtime.extensions.skill_provider import FilteredSkillProvider, LocalSkillProvider
from echoweave_runtime.extensions.hybrid_rag_provider import HybridRagProviderConfig, HybridRagRetrievalProvider


ProviderKind = Literal["skill", "mcp", "retrieval", "memory"]
RetrievalProviderFactory = Callable[[Path, dict[str, Any]], RetrievalProvider]


@dataclass(frozen=True)
class RetrievalProviderRegistration:
    names: tuple[str, ...]
    factory: RetrievalProviderFactory
    description: str = ""


class RetrievalProviderRegistry:
    def __init__(self) -> None:
        self._registrations: dict[str, RetrievalProviderRegistration] = {}

    def register(self, registration: RetrievalProviderRegistration) -> None:
        for name in registration.names:
            self._registrations[_normalize_backend(name)] = registration

    def create(self, backend: str, cwd: Path, options: dict[str, Any]) -> RetrievalProvider:
        normalized = _normalize_backend(backend)
        registration = self._registrations.get(normalized)
        if registration is None:
            return MisconfiguredRetrievalProvider(f"Unknown RAG backend: {backend}")
        return registration.factory(cwd, options)

    def list_backends(self) -> list[dict[str, Any]]:
        seen: set[int] = set()
        backends: list[dict[str, Any]] = []
        for registration in self._registrations.values():
            ident = id(registration)
            if ident in seen:
                continue
            seen.add(ident)
            backends.append(
                {
                    "names": list(registration.names),
                    "description": registration.description,
                }
            )
        return backends


class ExtensionManager:
    def __init__(
        self,
        skill_provider: SkillProvider,
        mcp_provider: McpProvider,
        retrieval_provider: RetrievalProvider,
        memory_provider: MemoryProvider,
        hooks: dict[ExtensionHook, list[ExtensionHookHandler]] | None = None,
        tools: list[ExtensionToolSpec] | None = None,
    ) -> None:
        self._providers: dict[ProviderKind, Any] = {}
        self.register_skill_provider(skill_provider)
        self.register_mcp_provider(mcp_provider)
        self.register_retrieval_provider(retrieval_provider)
        self.register_memory_provider(memory_provider)
        self._hooks: dict[ExtensionHook, list[ExtensionHookHandler]] = {
            hook: list((hooks or {}).get(hook, []))
            for hook in ALL_EXTENSION_HOOKS
        }
        self._tools: list[ExtensionToolSpec] = list(tools or [])

    @property
    def skill_provider(self) -> SkillProvider:
        return cast(SkillProvider, self.get_provider("skill"))

    @skill_provider.setter
    def skill_provider(self, provider: SkillProvider) -> None:
        self.register_skill_provider(provider)

    @property
    def mcp_provider(self) -> McpProvider:
        return cast(McpProvider, self.get_provider("mcp"))

    @mcp_provider.setter
    def mcp_provider(self, provider: McpProvider) -> None:
        self.register_mcp_provider(provider)

    @property
    def retrieval_provider(self) -> RetrievalProvider:
        return cast(RetrievalProvider, self.get_provider("retrieval"))

    @retrieval_provider.setter
    def retrieval_provider(self, provider: RetrievalProvider) -> None:
        self.register_retrieval_provider(provider)

    @property
    def memory_provider(self) -> MemoryProvider:
        return cast(MemoryProvider, self.get_provider("memory"))

    @memory_provider.setter
    def memory_provider(self, provider: MemoryProvider) -> None:
        self.register_memory_provider(provider)

    def register_provider(self, kind: ProviderKind, provider: Any) -> None:
        self._providers[kind] = provider

    def get_provider(self, kind: ProviderKind) -> Any:
        if kind not in self._providers:
            raise ValueError(f"provider not registered: {kind}")
        return self._providers[kind]

    def list_providers(self) -> dict[ProviderKind, Any]:
        return dict(self._providers)

    def register_skill_provider(self, provider: SkillProvider) -> None:
        self.register_provider("skill", provider)

    def register_mcp_provider(self, provider: McpProvider) -> None:
        self.register_provider("mcp", provider)

    def register_retrieval_provider(self, provider: RetrievalProvider) -> None:
        self.register_provider("retrieval", provider)

    def register_memory_provider(self, provider: MemoryProvider) -> None:
        self.register_provider("memory", provider)

    def register_hook(self, hook: ExtensionHook, handler: ExtensionHookHandler) -> None:
        self._hooks[hook].append(handler)

    def emit_hook(self, hook: ExtensionHook, context: ExtensionContext) -> ExtensionContext:
        current = context
        for handler in self._hooks.get(hook, []):
            updated = handler(current)
            if updated is not None:
                current = updated
        return current

    def has_hook(self, hook: ExtensionHook) -> bool:
        return bool(self._hooks.get(hook))

    def register_tool(self, spec: ExtensionToolSpec) -> None:
        self._tools.append(spec)

    def list_tools(self) -> list[ExtensionToolSpec]:
        return list(self._tools)



def build_extension_manager(
    cwd: Path,
    *,
    rag_backend: str = "lexical",
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
    enabled_skills: set[str] | None = None,
    memory_exact_match_weight: float = 1.0,
    memory_token_overlap_weight: float = 1.0,
    memory_recency_weight: float = 0.15,
) -> ExtensionManager:
    skill_provider = LocalSkillProvider(cwd)
    if enabled_skills is not None:
        skill_provider = FilteredSkillProvider(skill_provider, enabled_skills)
    retrieval_provider = _build_retrieval_provider(
        cwd,
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
    )
    return ExtensionManager(
        skill_provider=skill_provider,
        mcp_provider=LocalMcpProvider(cwd),
        retrieval_provider=retrieval_provider,
        memory_provider=InMemoryContextProvider(
            cwd,
            exact_match_weight=memory_exact_match_weight,
            token_overlap_weight=memory_token_overlap_weight,
            recency_weight=memory_recency_weight,
        ),
    )


class MisconfiguredRetrievalProvider:
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def retrieve(self, query: str, top_k: int = 3):
        raise RuntimeError(self.reason)

    def index_workspace(self) -> int:
        raise RuntimeError(self.reason)


def _normalize_backend(name: str) -> str:
    return name.lower().replace("-", "_")


def _create_lexical_provider(cwd: Path, options: dict[str, Any]) -> RetrievalProvider:
    return LexicalRetrievalProvider(cwd)


def _create_pgvector_provider(cwd: Path, options: dict[str, Any]) -> RetrievalProvider:
    dsn = options.get("rag_pgvector_dsn")
    if not dsn:
        return MisconfiguredRetrievalProvider("pgvector RAG is selected but rag_pgvector_dsn is not configured")
    try:
        return HybridRagRetrievalProvider(
            cwd,
            HybridRagProviderConfig(
                dsn=str(dsn),
                table=str(options.get("rag_pgvector_table") or "echoweave_rag_chunks"),
                embedding_model=str(options.get("rag_embedding_model") or "BAAI/bge-m3"),
                auto_index=bool(options.get("rag_auto_index", False)),
                vector_weight=float(options.get("rag_vector_weight", 0.65)),
                bm25_weight=float(options.get("rag_bm25_weight", 0.35)),
                query_rewrite_enabled=bool(options.get("rag_query_rewrite_enabled", False)),
                query_rewrite_strategy=str(options.get("rag_query_rewrite_strategy") or "local_multi_query"),
                query_rewrite_max_queries=int(options.get("rag_query_rewrite_max_queries", 3)),
                rerank_enabled=bool(options.get("rag_rerank_enabled", False)),
                rerank_strategy=str(options.get("rag_rerank_strategy") or "bm25"),
                rerank_candidate_multiplier=int(options.get("rag_rerank_candidate_multiplier", 4)),
                rerank_original_score_weight=float(options.get("rag_rerank_original_score_weight", 0.65)),
                rerank_bm25_weight=float(options.get("rag_rerank_bm25_weight", 0.35)),
            ),
        )
    except RuntimeError as exc:
        return MisconfiguredRetrievalProvider(str(exc))


DEFAULT_RETRIEVAL_PROVIDER_REGISTRY = RetrievalProviderRegistry()
DEFAULT_RETRIEVAL_PROVIDER_REGISTRY.register(
    RetrievalProviderRegistration(
        names=("lexical", "local", "simple"),
        factory=_create_lexical_provider,
        description="Local lexical retrieval over workspace files.",
    )
)
DEFAULT_RETRIEVAL_PROVIDER_REGISTRY.register(
    RetrievalProviderRegistration(
        names=("pgvector", "pgvector_hybrid", "pgvector_hybrid_bgem3", "hybrid"),
        factory=_create_pgvector_provider,
        description="PostgreSQL pgvector + BGE-M3 hybrid vector/BM25 retrieval.",
    )
)


def register_retrieval_provider(registration: RetrievalProviderRegistration) -> None:
    DEFAULT_RETRIEVAL_PROVIDER_REGISTRY.register(registration)


def list_retrieval_backends() -> list[dict[str, Any]]:
    return DEFAULT_RETRIEVAL_PROVIDER_REGISTRY.list_backends()


def _build_retrieval_provider(
    cwd: Path,
    *,
    rag_backend: str,
    rag_pgvector_dsn: str | None,
    rag_pgvector_table: str,
    rag_embedding_model: str,
    rag_auto_index: bool,
    rag_vector_weight: float,
    rag_bm25_weight: float,
    rag_query_rewrite_enabled: bool,
    rag_query_rewrite_strategy: str,
    rag_query_rewrite_max_queries: int,
    rag_rerank_enabled: bool,
    rag_rerank_strategy: str,
    rag_rerank_candidate_multiplier: int,
    rag_rerank_original_score_weight: float,
    rag_rerank_bm25_weight: float,
):
    return DEFAULT_RETRIEVAL_PROVIDER_REGISTRY.create(
        rag_backend,
        cwd,
        {
            "rag_pgvector_dsn": rag_pgvector_dsn,
            "rag_pgvector_table": rag_pgvector_table,
            "rag_embedding_model": rag_embedding_model,
            "rag_auto_index": rag_auto_index,
            "rag_vector_weight": rag_vector_weight,
            "rag_bm25_weight": rag_bm25_weight,
            "rag_query_rewrite_enabled": rag_query_rewrite_enabled,
            "rag_query_rewrite_strategy": rag_query_rewrite_strategy,
            "rag_query_rewrite_max_queries": rag_query_rewrite_max_queries,
            "rag_rerank_enabled": rag_rerank_enabled,
            "rag_rerank_strategy": rag_rerank_strategy,
            "rag_rerank_candidate_multiplier": rag_rerank_candidate_multiplier,
            "rag_rerank_original_score_weight": rag_rerank_original_score_weight,
            "rag_rerank_bm25_weight": rag_rerank_bm25_weight,
        },
    )
