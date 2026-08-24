from __future__ import annotations

from pathlib import Path
import json
from typing import Any

from echoweave_runtime.extensions.base import MemoryChunk


class InMemoryContextProvider:
    """默认 Memory Provider：持久化到工作区文件并支持简单词法检索。"""

    def __init__(
        self,
        cwd: Path,
        storage_path: Path | None = None,
        *,
        exact_match_weight: float = 1.0,
        token_overlap_weight: float = 1.0,
        recency_weight: float = 0.15,
    ) -> None:
        self.cwd = cwd.resolve()
        self.storage_path = (storage_path or (self.cwd / ".echoweave" / "memory.jsonl")).resolve()
        self.exact_match_weight = max(0.0, float(exact_match_weight))
        self.token_overlap_weight = max(0.0, float(token_overlap_weight))
        self.recency_weight = max(0.0, float(recency_weight))

    def remember(self, text: str, metadata: dict[str, Any] | None = None) -> None:
        normalized = text.strip()
        if not normalized:
            return
        payload = {
            "text": normalized,
            "metadata": metadata or {},
        }
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        with self.storage_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def retrieve(self, query: str, history: list[dict[str, object]] | None = None, top_k: int = 3) -> list[MemoryChunk]:
        if not query.strip() or top_k <= 0 or not self.storage_path.exists():
            return []
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []

        raw_hits: list[dict[str, Any]] = []
        lines = self.storage_path.read_text(encoding="utf-8").splitlines()
        total = len(lines)
        normalized_query = self._normalize_text(query)
        for index, line in enumerate(lines, start=1):
            record = self._parse_record(line)
            if record is None:
                continue
            text = record.get("text", "")
            metadata = record.get("metadata", {})
            source = str(metadata.get("source") or f"memory://entry-{index}")

            text_tokens = self._tokenize(text)
            overlap = query_tokens.intersection(text_tokens)
            if not overlap:
                continue

            overlap_score = len(overlap) / max(len(query_tokens), 1)
            normalized_text = self._normalize_text(text)
            exact_bonus = 1.0 if normalized_query and normalized_query == normalized_text else 0.0
            recency_score = index / max(total, 1)
            score = (
                self.token_overlap_weight * overlap_score
                + self.exact_match_weight * exact_bonus
                + self.recency_weight * recency_score
            )
            raw_hits.append({"score": float(score), "source": source, "text": text, "index": index})

        deduped: dict[tuple[str, str], dict[str, Any]] = {}
        for hit in raw_hits:
            key = (hit["source"], self._normalize_text(hit["text"]))
            existing = deduped.get(key)
            if not existing or hit["score"] > existing.get("score", float("-inf")):
                deduped[key] = hit

        sorted_hits = sorted(
            deduped.values(),
            key=lambda item: (item["score"], item["index"]),
            reverse=True,
        )
        return [
            MemoryChunk(source=item["source"], text=item["text"], score=float(item["score"]))
            for item in sorted_hits[:top_k]
        ]

    def _parse_record(self, line: str) -> dict[str, Any] | None:
        if not line.strip():
            return None
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            return None
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        return {"text": text.strip(), "metadata": metadata}

    def _normalize_text(self, text: str) -> str:
        normalized = "".join(char.lower() if char.isalnum() else " " for char in text)
        return " ".join(normalized.split())

    def _tokenize(self, text: str) -> set[str]:
        normalized = self._normalize_text(text)
        return {token for token in normalized.split() if token}
