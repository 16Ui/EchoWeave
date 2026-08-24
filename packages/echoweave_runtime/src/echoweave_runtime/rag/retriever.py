from __future__ import annotations

import re
from pathlib import Path
from typing import Any


TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_\-\u4e00-\u9fff]+")


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_PATTERN.findall(text)]


def chunk_text(text: str, chunk_size: int = 800, overlap: int = 120) -> list[str]:
    """把长文本切成可检索块，overlap 用于减少跨块语义断裂。"""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if overlap < 0:
        raise ValueError("overlap must be non-negative")
    if overlap >= chunk_size:
        overlap = max(0, chunk_size // 4)
    if not text:
        return []

    chunks: list[str] = []
    start = 0
    step = chunk_size - overlap
    while start < len(text):
        end = min(len(text), start + chunk_size)
        part = text[start:end].strip()
        if part:
            chunks.append(part)
        if end >= len(text):
            break
        start += step
    return chunks


def collect_workspace_documents(
    cwd: Path,
    include_globs: tuple[str, ...] = ("**/*.md", "**/*.txt", "**/*.py"),
    max_files: int = 200,
    chunk_size: int = 800,
    overlap: int = 120,
) -> list[dict[str, Any]]:
    """采集工作区文档并切块，过滤隐藏目录与常见无关目录。"""
    base = cwd.resolve()
    candidates: list[Path] = []
    seen: set[Path] = set()

    for pattern in include_globs:
        for path in base.glob(pattern):
            if len(candidates) >= max_files:
                break
            if not path.is_file():
                continue
            if path in seen:
                continue
            rel = path.relative_to(base)
            if any(part.startswith(".") for part in rel.parts):
                continue
            if any(part in {".venv", "venv", "node_modules", "__pycache__", ".git"} for part in rel.parts):
                continue
            seen.add(path)
            candidates.append(path)

    documents: list[dict[str, Any]] = []
    for path in candidates[:max_files]:
        text = path.read_text(encoding="utf-8", errors="ignore")
        pieces = chunk_text(text, chunk_size=chunk_size, overlap=overlap)
        for index, piece in enumerate(pieces):
            documents.append(
                {
                    "source": path.relative_to(base).as_posix(),
                    "chunk_index": index,
                    "text": piece,
                }
            )
    return documents


def lexical_score(query_tokens: list[str], text: str) -> float:
    """按 query token 在文本中的词频做简单打分。"""
    if not query_tokens:
        return 0.0
    doc_tokens = tokenize(text)
    if not doc_tokens:
        return 0.0
    doc_freq: dict[str, int] = {}
    for token in doc_tokens:
        doc_freq[token] = doc_freq.get(token, 0) + 1
    score = 0.0
    for token in query_tokens:
        score += float(doc_freq.get(token, 0))
    return score


def retrieve_top_chunks(query: str, documents: list[dict[str, Any]], top_k: int = 3) -> list[dict[str, Any]]:
    """从候选块中选出 top_k：先按得分，再按 source/chunk_index 稳定排序。"""
    if top_k <= 0:
        return []
    query_tokens = tokenize(query)
    scored: list[dict[str, Any]] = []
    for chunk in documents:
        score = lexical_score(query_tokens, str(chunk.get("text", "")))
        if score <= 0:
            continue
        scored.append({**chunk, "score": score})
    scored.sort(key=lambda item: (float(item["score"]), str(item["source"]), int(item["chunk_index"])), reverse=True)
    return scored[:top_k]
