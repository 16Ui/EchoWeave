from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class DocumentChunk:
    source: str
    chunk_index: int
    text: str
    title_path: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, str | int | float | bool | None] = field(default_factory=dict)


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def chunk_markdown_by_heading(
    text: str,
    *,
    source: str,
    max_chars: int = 1800,
    overlap: int = 180,
) -> list[DocumentChunk]:
    sections: list[tuple[tuple[str, ...], list[str]]] = []
    current_titles: list[str] = []
    current_lines: list[str] = []

    def flush() -> None:
        if current_lines:
            sections.append((tuple(current_titles), list(current_lines)))
            current_lines.clear()

    for line in text.splitlines():
        match = HEADING_RE.match(line)
        if match:
            flush()
            level = len(match.group(1))
            title = match.group(2).strip()
            current_titles[:] = current_titles[: level - 1]
            current_titles.append(title)
        current_lines.append(line)
    flush()

    chunks: list[DocumentChunk] = []
    for titles, lines in sections:
        section_text = "\n".join(lines).strip()
        for piece in fixed_window_chunks(section_text, max_chars=max_chars, overlap=overlap):
            chunks.append(
                DocumentChunk(
                    source=source,
                    chunk_index=len(chunks),
                    text=piece,
                    title_path=titles,
                    metadata={"chunker": "markdown-heading"},
                )
            )
    return chunks


def chunk_fixed_window(
    text: str,
    *,
    source: str,
    max_chars: int = 1200,
    overlap: int = 180,
    metadata: dict[str, str | int | float | bool | None] | None = None,
) -> list[DocumentChunk]:
    return [
        DocumentChunk(
            source=source,
            chunk_index=index,
            text=piece,
            metadata={"chunker": "fixed-window", **(metadata or {})},
        )
        for index, piece in enumerate(fixed_window_chunks(text, max_chars=max_chars, overlap=overlap))
    ]


def fixed_window_chunks(text: str, *, max_chars: int, overlap: int) -> list[str]:
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if overlap < 0:
        raise ValueError("overlap must be non-negative")
    if overlap >= max_chars:
        overlap = max(0, max_chars // 4)
    cleaned = text.strip()
    if not cleaned:
        return []
    chunks: list[str] = []
    start = 0
    step = max_chars - overlap
    while start < len(cleaned):
        end = min(len(cleaned), start + max_chars)
        chunk = cleaned[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(cleaned):
            break
        start += step
    return chunks


def iter_supported_files(root: Path) -> Iterable[Path]:
    ignored = {".git", ".venv", "venv", "node_modules", "__pycache__"}
    suffixes = {".md", ".markdown", ".txt", ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        rel = path.relative_to(root)
        if any(part.startswith(".") or part in ignored for part in rel.parts):
            continue
        yield path
