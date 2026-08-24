from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from echoweave_runtime.governance import record_runtime_audit
from echoweave_runtime.extensions.base import RetrievalChunk
from echoweave_runtime.rag.model import RagIndexOptions, RagSearchOptions
from echoweave_runtime.rag.pipeline import (
    QueryRewriter,
    RagPipelineConfig,
    Reranker,
    build_query_rewriter,
    build_reranker,
    merge_retrieval_chunks,
)
from echoweave_runtime.rag.pgvector_hybrid import PgVectorHybridConfig, PgVectorHybridRagModel


@dataclass(frozen=True)
class HybridRagProviderConfig:
    dsn: str
    table: str = "echoweave_rag_chunks"
    embedding_model: str = "BAAI/bge-m3"
    auto_index: bool = False
    vector_weight: float = 0.65
    bm25_weight: float = 0.35
    query_rewrite_enabled: bool = False
    query_rewrite_strategy: str = "local_multi_query"
    query_rewrite_max_queries: int = 3
    rerank_enabled: bool = False
    rerank_strategy: str = "bm25"
    rerank_candidate_multiplier: int = 4
    rerank_original_score_weight: float = 0.65
    rerank_bm25_weight: float = 0.35


class HybridRagRetrievalProvider:
    """RetrievalProvider adapter over the default pgvector/BGE-M3 hybrid RAG model."""

    def __init__(
        self,
        cwd: Path,
        config: HybridRagProviderConfig,
        *,
        query_rewriter: QueryRewriter | None = None,
        reranker: Reranker | None = None,
        rag_model: Any | None = None,
    ) -> None:
        self.cwd = cwd.resolve()
        self.config = config
        self.workspace_id = workspace_id_for(self.cwd)
        pipeline_config = RagPipelineConfig(
            query_rewrite_enabled=config.query_rewrite_enabled,
            query_rewrite_strategy=config.query_rewrite_strategy,
            query_rewrite_max_queries=config.query_rewrite_max_queries,
            rerank_enabled=config.rerank_enabled,
            rerank_strategy=config.rerank_strategy,
            rerank_candidate_multiplier=config.rerank_candidate_multiplier,
            rerank_original_score_weight=config.rerank_original_score_weight,
            rerank_bm25_weight=config.rerank_bm25_weight,
        )
        self.query_rewriter = query_rewriter or build_query_rewriter(pipeline_config)
        self.reranker = reranker or build_reranker(pipeline_config)
        self.model = rag_model or (
            PgVectorHybridRagModel(
                PgVectorHybridConfig(
                    dsn=config.dsn,
                    table=config.table,
                    embedding_model=config.embedding_model,
                    auto_index=config.auto_index,
                    vector_weight=config.vector_weight,
                    bm25_weight=config.bm25_weight,
                )
            )
        )
        self._indexed = False

    def retrieve(self, query: str, top_k: int = 3) -> list[RetrievalChunk]:
        if not query.strip() or top_k <= 0:
            return []
        if self.config.auto_index and not self._indexed:
            self.index_workspace()
        candidate_top_k = top_k
        if self.config.rerank_enabled:
            candidate_top_k = top_k * max(1, self.config.rerank_candidate_multiplier)
        candidates: list[RetrievalChunk] = []
        rewritten_queries = self.query_rewriter.rewrite(query)
        started = time.perf_counter()
        try:
            for rewritten_query in rewritten_queries:
                candidates.extend(
                    self.model.search(
                        rewritten_query,
                        workspace_id=self.workspace_id,
                        options=RagSearchOptions(
                            top_k=candidate_top_k,
                            vector_weight=self.config.vector_weight,
                            bm25_weight=self.config.bm25_weight,
                        ),
                    )
                )
            merged = merge_retrieval_chunks(candidates)
            results = self.reranker.rerank(query, merged, top_k=top_k)
        except Exception as exc:
            record_runtime_audit(
                "rag",
                "retrieve",
                status="error",
                workspace=self.cwd,
                latency_ms=(time.perf_counter() - started) * 1000,
                metadata={"query": query, "rewritten_queries": rewritten_queries, "reason": str(exc), "backend": "pgvector_hybrid"},
            )
            raise
        record_runtime_audit(
            "rag",
            "retrieve",
            status="ok",
            workspace=self.cwd,
            latency_ms=(time.perf_counter() - started) * 1000,
            metadata={
                "query": query,
                "rewritten_queries": rewritten_queries,
                "candidate_count": len(candidates),
                "result_count": len(results),
                "backend": "pgvector_hybrid",
                "sources": [{"source": item.source, "score": item.score} for item in results],
            },
        )
        return results

    def index_workspace(self) -> int:
        inserted = self.model.index_workspace(
            RagIndexOptions(
                workspace=self.cwd,
                workspace_id=self.workspace_id,
            )
        )
        self._indexed = True
        return inserted


def workspace_id_for(path: Path) -> str:
    resolved = str(path.expanduser().resolve()).lower()
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()
