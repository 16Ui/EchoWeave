from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from echoweave_runtime.extensions.base import RetrievalChunk
from echoweave_runtime.rag.bm25 import bm25_scores, normalize_scores
from echoweave_runtime.rag.chunking import DocumentChunk, chunk_fixed_window, chunk_markdown_by_heading, iter_supported_files
from echoweave_runtime.rag.extractors import extract_text
from echoweave_runtime.rag.model import RagIndexOptions, RagSearchOptions


BGEM3_DIMENSIONS = 1024


@dataclass(frozen=True)
class PgVectorHybridConfig:
    dsn: str
    table: str = "echoweave_rag_chunks"
    embedding_model: str = "BAAI/bge-m3"
    dimensions: int = BGEM3_DIMENSIONS
    vector_weight: float = 0.65
    bm25_weight: float = 0.35
    vector_candidates: int = 40
    lexical_candidates: int = 40
    auto_index: bool = False


class BgeM3Embedder:
    def __init__(self, model_name: str = "BAAI/bge-m3") -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("BGE-M3 embeddings require optional dependency: sentence-transformers") from exc
        self._model = SentenceTransformer(model_name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._model.encode(texts, normalize_embeddings=True)
        return [list(map(float, vector)) for vector in vectors]


class PgVectorHybridRagModel:
    name = "pgvector-hybrid-bgem3"

    def __init__(self, config: PgVectorHybridConfig, embedder: Any | None = None) -> None:
        self.config = config
        self.embedder = embedder or BgeM3Embedder(config.embedding_model)

    def index_workspace(self, options: RagIndexOptions) -> int:
        chunks = collect_chunks(
            options.workspace,
            markdown_max_chars=options.markdown_max_chars,
            fixed_max_chars=options.fixed_max_chars,
            overlap=options.overlap,
        )
        self.ensure_schema()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"DELETE FROM {self._table()} WHERE workspace_id = %s", (options.workspace_id,))
                inserted = 0
                for batch in _batched(chunks, 32):
                    vectors = self.embedder.embed([chunk.text for chunk in batch])
                    for chunk, vector in zip(batch, vectors):
                        cur.execute(
                            f"""
                            INSERT INTO {self._table()}
                              (id, workspace_id, source, chunk_index, title_path, content, embedding, metadata)
                            VALUES
                              (%s, %s, %s, %s, %s, %s, %s::vector, %s::jsonb)
                            ON CONFLICT (id) DO UPDATE SET
                              title_path = EXCLUDED.title_path,
                              content = EXCLUDED.content,
                              embedding = EXCLUDED.embedding,
                              metadata = EXCLUDED.metadata,
                              updated_at = now()
                            """,
                            (
                                _chunk_id(options.workspace_id, chunk),
                                options.workspace_id,
                                chunk.source,
                                chunk.chunk_index,
                                list(chunk.title_path),
                                chunk.text,
                                _vector_literal(vector),
                                json.dumps({**chunk.metadata, **options.metadata}, ensure_ascii=False),
                            ),
                        )
                        inserted += 1
                conn.commit()
                return inserted

    def search(self, query: str, *, workspace_id: str, options: RagSearchOptions) -> list[RetrievalChunk]:
        if not query.strip() or options.top_k <= 0:
            return []
        self.ensure_schema()
        query_vector = self.embedder.embed([query])[0]
        candidates = self._candidate_rows(query, workspace_id=workspace_id, query_vector=query_vector)
        if not candidates:
            return []

        bm25 = bm25_scores(query, [str(item["content"]) for item in candidates])
        normalized_bm25 = normalize_scores(bm25)
        vector_scores = normalize_scores([float(item.get("vector_score") or 0.0) for item in candidates])

        ranked: list[dict[str, Any]] = []
        vector_weight = options.vector_weight if options.vector_weight >= 0 else self.config.vector_weight
        bm25_weight = options.bm25_weight if options.bm25_weight >= 0 else self.config.bm25_weight
        for item, bm25_score, vector_score in zip(candidates, normalized_bm25, vector_scores):
            score = vector_weight * vector_score + bm25_weight * bm25_score
            ranked.append({**item, "score": score})
        ranked.sort(key=lambda item: float(item["score"]), reverse=True)
        return [
            RetrievalChunk(
                source=f"{item['source']}#chunk-{item['chunk_index']}",
                text=str(item["content"]),
                score=float(item["score"]),
            )
            for item in ranked[: options.top_k]
        ]

    def ensure_schema(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._table()} (
                      id text PRIMARY KEY,
                      workspace_id text NOT NULL,
                      source text NOT NULL,
                      chunk_index integer NOT NULL,
                      title_path text[] NOT NULL DEFAULT '{{}}',
                      content text NOT NULL,
                      embedding vector({self.config.dimensions}) NOT NULL,
                      metadata jsonb NOT NULL DEFAULT '{{}}'::jsonb,
                      content_tsv tsvector GENERATED ALWAYS AS
                        (to_tsvector('simple', coalesce(content, ''))) STORED,
                      updated_at timestamptz NOT NULL DEFAULT now()
                    )
                    """
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS {self.config.table}_workspace_idx ON {self._table()} (workspace_id)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS {self.config.table}_tsv_idx ON {self._table()} USING gin (content_tsv)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS {self.config.table}_embedding_idx "
                    f"ON {self._table()} USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
                )
                conn.commit()

    def _candidate_rows(self, query: str, *, workspace_id: str, query_vector: list[float]) -> list[dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    WITH vector_hits AS (
                      SELECT id, source, chunk_index, content, 1 - (embedding <=> %s::vector) AS vector_score
                      FROM {self._table()}
                      WHERE workspace_id = %s
                      ORDER BY embedding <=> %s::vector
                      LIMIT %s
                    ),
                    lexical_hits AS (
                      SELECT id, source, chunk_index, content, 0.0 AS vector_score
                      FROM {self._table()}
                      WHERE workspace_id = %s
                        AND content_tsv @@ plainto_tsquery('simple', %s)
                      LIMIT %s
                    )
                    SELECT id, source, chunk_index, content, max(vector_score) AS vector_score
                    FROM (
                      SELECT * FROM vector_hits
                      UNION ALL
                      SELECT * FROM lexical_hits
                    ) merged
                    GROUP BY id, source, chunk_index, content
                    """,
                    (
                        _vector_literal(query_vector),
                        workspace_id,
                        _vector_literal(query_vector),
                        self.config.vector_candidates,
                        workspace_id,
                        query,
                        self.config.lexical_candidates,
                    ),
                )
                names = [desc[0] for desc in cur.description]
                return [dict(zip(names, row)) for row in cur.fetchall()]

    def _connect(self):
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("pgvector RAG requires optional dependency: psycopg[binary]") from exc
        return psycopg.connect(self.config.dsn)

    def _table(self) -> str:
        if not self.config.table.replace("_", "").isalnum():
            raise ValueError("RAG table name must contain only letters, numbers, and underscores")
        return self.config.table


def collect_chunks(
    workspace: Path,
    *,
    markdown_max_chars: int = 1800,
    fixed_max_chars: int = 1200,
    overlap: int = 180,
) -> list[DocumentChunk]:
    root = workspace.resolve()
    chunks: list[DocumentChunk] = []
    for path in iter_supported_files(root):
        rel = path.relative_to(root).as_posix()
        try:
            text = extract_text(path)
        except RuntimeError:
            continue
        if path.suffix.lower() in {".md", ".markdown"}:
            file_chunks = chunk_markdown_by_heading(
                text,
                source=rel,
                max_chars=markdown_max_chars,
                overlap=overlap,
            )
        else:
            file_chunks = chunk_fixed_window(
                text,
                source=rel,
                max_chars=fixed_max_chars,
                overlap=overlap,
                metadata={"source_type": path.suffix.lower().lstrip(".")},
            )
        chunks.extend(file_chunks)
    return chunks


def _chunk_id(workspace_id: str, chunk: DocumentChunk) -> str:
    raw = f"{workspace_id}\0{chunk.source}\0{chunk.chunk_index}\0{chunk.text}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in vector) + "]"


def _batched(items: list[DocumentChunk], size: int) -> list[list[DocumentChunk]]:
    return [items[index : index + size] for index in range(0, len(items), size)]
