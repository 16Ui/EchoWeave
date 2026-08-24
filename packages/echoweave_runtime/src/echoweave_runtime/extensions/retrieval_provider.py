from __future__ import annotations

from pathlib import Path
from typing import Any
import time

from echoweave_runtime.governance import record_runtime_audit
from echoweave_runtime.extensions.base import RetrievalChunk
from echoweave_runtime.rag.retriever import collect_workspace_documents, retrieve_top_chunks


class LexicalRetrievalProvider:
    """默认 Retrieval Provider：在工作区做轻量词法检索并返回片段命中。"""

    def __init__(
        self,
        cwd: Path,
        include_globs: tuple[str, ...] = ("**/*.md", "**/*.txt", "**/*.py"),
        max_files: int = 200,
        chunk_size: int = 800,
        overlap: int = 120,
    ) -> None:
        self.cwd = cwd.resolve()
        self.include_globs = include_globs
        self.max_files = max_files
        self.chunk_size = chunk_size
        self.overlap = overlap

    def retrieve(self, query: str, top_k: int = 3) -> list[RetrievalChunk]:
        """执行检索并返回统一 RetrievalChunk；空查询或 top_k<=0 直接返回空。"""
        if not query.strip() or top_k <= 0:
            return []
        started = time.perf_counter()
        try:
            documents = collect_workspace_documents(
                self.cwd,
                include_globs=self.include_globs,
                max_files=self.max_files,
                chunk_size=self.chunk_size,
                overlap=self.overlap,
            )
            hits = retrieve_top_chunks(query, documents, top_k=top_k)
            results = [
                RetrievalChunk(
                    source=f"{hit['source']}#chunk-{hit['chunk_index']}",
                    text=str(hit["text"]),
                    score=float(hit["score"]),
                )
                for hit in hits
            ]
        except Exception as exc:
            record_runtime_audit("rag", "retrieve", status="error", workspace=self.cwd, latency_ms=(time.perf_counter() - started) * 1000, metadata={"query": query, "reason": str(exc), "backend": "lexical"})
            raise
        record_runtime_audit("rag", "retrieve", status="ok", workspace=self.cwd, latency_ms=(time.perf_counter() - started) * 1000, metadata={"query": query, "backend": "lexical", "document_count": len(documents), "result_count": len(results), "sources": [{"source": item.source, "score": item.score} for item in results]})
        return results
