from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from echoweave_runtime.extensions.base import RetrievalChunk


@dataclass(frozen=True)
class RagSearchOptions:
    top_k: int = 3
    vector_weight: float = 0.65
    bm25_weight: float = 0.35


@dataclass(frozen=True)
class RagIndexOptions:
    workspace: Path
    workspace_id: str
    markdown_max_chars: int = 1800
    fixed_max_chars: int = 1200
    overlap: int = 180
    metadata: dict[str, str] = field(default_factory=dict)


class RagModel(Protocol):
    name: str

    def index_workspace(self, options: RagIndexOptions) -> int:
        ...

    def search(self, query: str, *, workspace_id: str, options: RagSearchOptions) -> list[RetrievalChunk]:
        ...
