from __future__ import annotations

from typing import Any


def truncate_history(history: list[dict[str, Any]], max_messages: int = 12) -> list[dict[str, Any]]:
    if len(history) <= max_messages:
        return history
    return history[-max_messages:]


def trim_for_compaction(history: list[dict[str, Any]], keep_tail: int = 8) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if len(history) <= keep_tail:
        return [], history
    return history[:-keep_tail], history[-keep_tail:]
