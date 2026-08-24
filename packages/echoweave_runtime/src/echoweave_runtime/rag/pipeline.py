from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from echoweave_runtime.extensions.base import RetrievalChunk
from echoweave_runtime.rag.bm25 import bm25_scores, normalize_scores
from echoweave_runtime.rag.retriever import tokenize


class QueryRewriter(Protocol):
    name: str

    def rewrite(self, query: str) -> list[str]:
        ...


class Reranker(Protocol):
    name: str

    def rerank(self, query: str, chunks: list[RetrievalChunk], *, top_k: int) -> list[RetrievalChunk]:
        ...


@dataclass(frozen=True)
class RagPipelineConfig:
    query_rewrite_enabled: bool = False
    query_rewrite_strategy: str = "local_multi_query"
    query_rewrite_max_queries: int = 3
    rerank_enabled: bool = False
    rerank_strategy: str = "bm25"
    rerank_candidate_multiplier: int = 4
    rerank_original_score_weight: float = 0.65
    rerank_bm25_weight: float = 0.35


class NoopQueryRewriter:
    name = "none"

    def rewrite(self, query: str) -> list[str]:
        return [query.strip()] if query.strip() else []


class LocalMultiQueryRewriter:
    """Deterministic fallback rewriter.

    This is intentionally small and dependency-free. It provides the slot and a
    useful baseline while leaving room for LLM/BGE-based rewriters later.
    """

    name = "local_multi_query"

    def __init__(self, max_queries: int = 3) -> None:
        self.max_queries = max(1, max_queries)

    def rewrite(self, query: str) -> list[str]:
        original = query.strip()
        if not original:
            return []
        candidates = [
            original,
            _strip_question_words(original),
            " ".join(tokenize(original)),
            _compact_symbol_noise(original),
        ]
        return _dedupe_nonempty(candidates)[: self.max_queries]


class NoopReranker:
    name = "none"

    def rerank(self, query: str, chunks: list[RetrievalChunk], *, top_k: int) -> list[RetrievalChunk]:
        return sorted(chunks, key=lambda chunk: chunk.score, reverse=True)[:top_k]


class Bm25Reranker:
    name = "bm25"

    def __init__(self, *, original_score_weight: float = 0.65, bm25_weight: float = 0.35) -> None:
        self.original_score_weight = max(0.0, original_score_weight)
        self.bm25_weight = max(0.0, bm25_weight)

    def rerank(self, query: str, chunks: list[RetrievalChunk], *, top_k: int) -> list[RetrievalChunk]:
        if top_k <= 0:
            return []
        if not chunks:
            return []
        original_scores = normalize_scores([chunk.score for chunk in chunks])
        lexical_scores = normalize_scores(bm25_scores(query, [chunk.text for chunk in chunks]))
        ranked: list[RetrievalChunk] = []
        for chunk, original_score, lexical_score in zip(chunks, original_scores, lexical_scores):
            score = self.original_score_weight * original_score + self.bm25_weight * lexical_score
            ranked.append(RetrievalChunk(source=chunk.source, text=chunk.text, score=score))
        ranked.sort(key=lambda chunk: (chunk.score, chunk.source), reverse=True)
        return ranked[:top_k]


def build_query_rewriter(config: RagPipelineConfig) -> QueryRewriter:
    if not config.query_rewrite_enabled:
        return NoopQueryRewriter()
    normalized = config.query_rewrite_strategy.lower().replace("-", "_")
    if normalized in {"local", "local_multi_query", "multi_query", "heuristic"}:
        return LocalMultiQueryRewriter(config.query_rewrite_max_queries)
    if normalized in {"none", "noop", "off"}:
        return NoopQueryRewriter()
    raise ValueError(f"Unknown query rewrite strategy: {config.query_rewrite_strategy}")


def build_reranker(config: RagPipelineConfig) -> Reranker:
    if not config.rerank_enabled:
        return NoopReranker()
    normalized = config.rerank_strategy.lower().replace("-", "_")
    if normalized in {"bm25", "lexical", "local_bm25"}:
        return Bm25Reranker(
            original_score_weight=config.rerank_original_score_weight,
            bm25_weight=config.rerank_bm25_weight,
        )
    if normalized in {"none", "noop", "off"}:
        return NoopReranker()
    raise ValueError(f"Unknown rerank strategy: {config.rerank_strategy}")


def merge_retrieval_chunks(chunks: list[RetrievalChunk]) -> list[RetrievalChunk]:
    merged: dict[tuple[str, str], RetrievalChunk] = {}
    for chunk in chunks:
        key = (chunk.source, chunk.text)
        previous = merged.get(key)
        if previous is None or chunk.score > previous.score:
            merged[key] = chunk
    return sorted(merged.values(), key=lambda chunk: (chunk.score, chunk.source), reverse=True)


def _strip_question_words(query: str) -> str:
    text = query
    for word in ("如何", "怎么", "怎样", "为什么", "请问", "帮我", "please", "how to", "what is", "why"):
        text = re.sub(re.escape(word), " ", text, flags=re.IGNORECASE)
    return _compact_symbol_noise(text)


def _compact_symbol_noise(query: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[?？!！,，。；;:：]+", " ", query)).strip()


def _dedupe_nonempty(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        normalized = item.strip()
        key = normalized.lower()
        if normalized and key not in seen:
            seen.add(key)
            result.append(normalized)
    return result
