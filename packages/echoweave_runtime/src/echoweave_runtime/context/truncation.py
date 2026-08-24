from __future__ import annotations

from typing import Any


def truncate_history(history: list[dict[str, Any]], max_messages: int = 12) -> list[dict[str, Any]]:
    snipped = snip_tool_outputs(history)
    if len(snipped) <= max_messages:
        return snipped
    return snipped[-max_messages:]


def snip_tool_outputs(history: list[dict[str, Any]], max_chars: int = 1500) -> list[dict[str, Any]]:
    """Trim oversized historical tool results while preserving head/tail signal."""

    return [_snip_message(message, max_chars=max_chars) for message in history]


def trim_for_compaction(history: list[dict[str, Any]], keep_tail: int = 8) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if len(history) <= keep_tail:
        return [], history
    return history[:-keep_tail], history[-keep_tail:]


def _snip_message(message: dict[str, Any], *, max_chars: int) -> dict[str, Any]:
    copied = dict(message)
    content = copied.get("content")
    if copied.get("role") == "tool" and isinstance(content, str):
        copied["content"] = _snip_text(content, max_chars=max_chars)
        return copied
    if isinstance(content, list):
        copied["content"] = [_snip_content_item(item, max_chars=max_chars) for item in content]
    return copied


def _snip_content_item(item: Any, *, max_chars: int) -> Any:
    if not isinstance(item, dict):
        return item
    copied = dict(item)
    if copied.get("type") == "tool_result" and isinstance(copied.get("content"), str):
        copied["content"] = _snip_text(str(copied["content"]), max_chars=max_chars)
    return copied


def _snip_text(text: str, *, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    keep = max(100, max_chars // 2)
    return (
        text[:keep].rstrip()
        + f"\n... history tool output snipped, original_chars={len(text)} ...\n"
        + text[-keep:].lstrip()
    )
