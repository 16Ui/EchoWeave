from __future__ import annotations

import argparse

from echoweave_runtime.rag.pgvector_hybrid import BGEM3_DIMENSIONS


def init_pgvector_schema(dsn: str, table: str = "echoweave_rag_chunks", dimensions: int = BGEM3_DIMENSIONS) -> None:
    if not table.replace("_", "").isalnum():
        raise ValueError("RAG table name must contain only letters, numbers, and underscores")
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("RAG database initialization requires psycopg[binary]") from exc

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {table} (
                  id text PRIMARY KEY,
                  workspace_id text NOT NULL,
                  source text NOT NULL,
                  chunk_index integer NOT NULL,
                  title_path text[] NOT NULL DEFAULT '{{}}',
                  content text NOT NULL,
                  embedding vector({dimensions}) NOT NULL,
                  metadata jsonb NOT NULL DEFAULT '{{}}'::jsonb,
                  content_tsv tsvector GENERATED ALWAYS AS
                    (to_tsvector('simple', coalesce(content, ''))) STORED,
                  updated_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute(f"CREATE INDEX IF NOT EXISTS {table}_workspace_idx ON {table} (workspace_id)")
            cur.execute(f"CREATE INDEX IF NOT EXISTS {table}_tsv_idx ON {table} USING gin (content_tsv)")
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS {table}_embedding_idx "
                f"ON {table} USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
            )
            conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize EchoWeave pgvector RAG schema.")
    parser.add_argument("--dsn", required=True, help="PostgreSQL DSN, for example postgresql://user:pass@127.0.0.1:5432/echoweave")
    parser.add_argument("--table", default="echoweave_rag_chunks", help="RAG chunk table name")
    parser.add_argument("--dimensions", type=int, default=BGEM3_DIMENSIONS, help="Embedding dimensions")
    args = parser.parse_args()
    init_pgvector_schema(args.dsn, table=args.table, dimensions=args.dimensions)
    print(f"EchoWeave RAG schema ready: table={args.table}, dimensions={args.dimensions}")


if __name__ == "__main__":
    main()
