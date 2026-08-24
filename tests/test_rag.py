from __future__ import annotations

import http.client
import io
import json
import shutil
import threading
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from echoweave_agent_core import AgentCore, AgentCoreConfig, AgentCoreHookBase, CoreTurnContext, SessionRuntimeFacade, TurnRequest, TurnResult
from echoweave_coding_agent import CodingAgent, CodingAgentConfig
from echoweave_harness.audit import configure_audit, read_audit_events
from echoweave_harness.feedback import suggest_harness_improvements, write_feedback_backlog
from echoweave_harness.metrics import compute_harness_metrics
from echoweave_harness.policy import configure_harness_policy
from echoweave_runtime.app import build_registry
from echoweave_runtime.extensions.base import RetrievalChunk
from echoweave_runtime.extensions.hybrid_rag_provider import HybridRagProviderConfig, HybridRagRetrievalProvider
from echoweave_runtime.rag.pipeline import Bm25Reranker, LocalMultiQueryRewriter
from echoweave_runtime.rag.model import RagSearchOptions
from echoweave_runtime.rag.pgvector_hybrid import PgVectorHybridConfig, PgVectorHybridRagModel, collect_chunks
from echoweave_runtime.models.demo import AgentResponse, SequenceModelClient, tool_response
from echoweave_runtime.session.store import SessionStore
from echoweave_social.adapters.astrbot_event import AstrBotEventAdapter
from echoweave_social.adapters.feishu import FeishuAdapter
from echoweave_social.adapters.generic_webhook import GenericWebhookAdapter
from echoweave_social.adapters.onebot_v11 import OneBotV11Adapter
from echoweave_social.adapters.wechat_official import WeChatOfficialAdapter
from echoweave_social.agent_runtime import SocialAgentConfig, EchoWeaveSocialAgent
from echoweave_social.agent_schema import SocialMessage
from echoweave_social.backend import EchoWeaveBackend, EchoWeaveBackendConfig
from echoweave_web.cli import app
from echoweave_social.config import EchoWeaveConfig
from echoweave_web.server import HubWebhookServer
from echoweave_web.server import _read_chunked_body
from echoweave_social.onebot_client import OneBotHttpClient
from echoweave_social.schema import EchoWeaveEvent, EchoWeaveReply


@contextmanager
def _local_tmp():
    path = Path.cwd() / ".test-data" / uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_rag_collects_markdown_headings_and_fixed_window_chunks() -> None:
    with _local_tmp() as tmp_path:
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "guide.md").write_text(
            "# Install\nUse pgvector with BGE-M3.\n\n## Search\nHybrid search mixes vector and BM25.",
            encoding="utf-8",
        )
        (docs / "notes.txt").write_text("OCR extracted text " * 200, encoding="utf-8")

        chunks = collect_chunks(docs, markdown_max_chars=80, fixed_max_chars=120, overlap=20)

        assert any(chunk.source == "guide.md" and chunk.title_path == ("Install",) for chunk in chunks)
        assert any(chunk.source == "guide.md" and chunk.title_path == ("Install", "Search") for chunk in chunks)
        assert any(chunk.source == "notes.txt" and chunk.metadata["chunker"] == "fixed-window" for chunk in chunks)
        assert len([chunk for chunk in chunks if chunk.source == "notes.txt"]) > 1
def test_pgvector_hybrid_search_combines_vector_and_bm25_scores() -> None:
    class FakeEmbedder:
        def embed(self, texts):
            return [[1.0, 0.0] for _ in texts]

    model = PgVectorHybridRagModel(
        PgVectorHybridConfig(dsn="postgresql://unused", dimensions=2),
        embedder=FakeEmbedder(),
    )
    model.ensure_schema = lambda: None  # type: ignore[method-assign]
    model._candidate_rows = lambda query, workspace_id, query_vector: [  # type: ignore[method-assign]
        {"id": "vector", "source": "semantic.md", "chunk_index": 0, "content": "semantic only", "vector_score": 1.0},
        {"id": "lexical", "source": "exact.md", "chunk_index": 0, "content": "alpha alpha alpha", "vector_score": 0.1},
    ]

    results = model.search(
        "alpha",
        workspace_id="workspace-1",
        options=RagSearchOptions(top_k=2, vector_weight=0.1, bm25_weight=0.9),
    )

    assert results[0].source == "exact.md#chunk-0"
    assert results[0].score > results[1].score
def test_rag_query_rewriter_and_bm25_reranker_slots() -> None:
    rewriter = LocalMultiQueryRewriter(max_queries=3)
    rewritten = rewriter.rewrite("如何 使用 pgvector 混合搜索？")
    reranker = Bm25Reranker(original_score_weight=0.1, bm25_weight=0.9)
    ranked = reranker.rerank(
        "pgvector 混合搜索",
        [
            RetrievalChunk(source="semantic.md#chunk-0", text="semantic match only", score=1.0),
            RetrievalChunk(source="exact.md#chunk-0", text="pgvector 混合搜索 pgvector", score=0.1),
        ],
        top_k=2,
    )

    assert rewritten[0] == "如何 使用 pgvector 混合搜索？"
    assert any("pgvector" in query and "如何" not in query for query in rewritten[1:])
    assert ranked[0].source == "exact.md#chunk-0"
def test_hybrid_rag_provider_uses_multi_query_and_rerank_slots() -> None:
    class StaticRewriter:
        name = "static"

        def rewrite(self, query: str) -> list[str]:
            return [query, "exact alpha"]

    class FakeModel:
        def __init__(self) -> None:
            self.queries: list[str] = []

        def search(self, query: str, *, workspace_id: str, options: RagSearchOptions):
            self.queries.append(query)
            if query == "exact alpha":
                return [RetrievalChunk(source="exact.md#chunk-0", text="alpha alpha", score=0.2)]
            return [RetrievalChunk(source="semantic.md#chunk-0", text="semantic only", score=1.0)]

    with _local_tmp() as tmp_path:
        fake_model = FakeModel()
        provider = HybridRagRetrievalProvider(
            tmp_path,
            HybridRagProviderConfig(
                dsn="postgresql://unused",
                query_rewrite_enabled=True,
                rerank_enabled=True,
                rerank_candidate_multiplier=3,
                rerank_original_score_weight=0.1,
                rerank_bm25_weight=0.9,
            ),
            query_rewriter=StaticRewriter(),
            rag_model=fake_model,
        )

        results = provider.retrieve("alpha", top_k=1)

        assert fake_model.queries == ["alpha", "exact alpha"]
        assert results[0].source == "exact.md#chunk-0"


def test_retrieval_provider_registry_can_register_custom_backend() -> None:
    from echoweave_runtime.extensions.manager import (
        RetrievalProviderRegistration,
        build_extension_manager,
        list_retrieval_backends,
        register_retrieval_provider,
    )

    class StaticProvider:
        def retrieve(self, query: str, top_k: int = 3):
            return [RetrievalChunk(source="custom", text=query, score=1.0)]

    register_retrieval_provider(
        RetrievalProviderRegistration(
            names=("custom-test",),
            factory=lambda cwd, options: StaticProvider(),
            description="test provider",
        )
    )

    with _local_tmp() as tmp_path:
        manager = build_extension_manager(tmp_path, rag_backend="custom-test")

        chunks = manager.retrieval_provider.retrieve("hello", top_k=1)

        assert chunks[0].source == "custom"
        assert any("custom-test" in item["names"] for item in list_retrieval_backends())
