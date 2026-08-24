from __future__ import annotations

from typing import Any


def _clip(text: str, limit: int = 240) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def _stringify_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                parts.append(str(item))
                continue
            item_type = item.get("type")
            if item_type == "text":
                parts.append(str(item.get("text", "")))
            elif item_type == "tool_use":
                parts.append(f"tool_use:{item.get('name')}({item.get('id')}) input={item.get('input')}")
            elif item_type == "tool_result":
                prefix = "tool_error" if item.get("is_error") else "tool_result"
                parts.append(f"{prefix}:{item.get('tool_use_id')} {item.get('content')}")
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part)
    if isinstance(content, dict):
        return str(content)
    return "" if content is None else str(content)


def _extract_state_lines(history: list[dict[str, Any]], max_lines: int = 18) -> list[str]:
    lines: list[str] = []
    for item in history:
        role = str(item.get("role", "unknown"))
        content = item.get("content", "")
        text = _stringify_content(content)
        if not text.strip():
            continue
        if "tool_use:" in text or "tool_result:" in text or "tool_error:" in text:
            label = "工具状态"
        elif role == "user":
            label = "用户目标"
        elif role == "assistant":
            label = "模型结论"
        else:
            label = role
        lines.append(f"- {label}: {_clip(text)}")
    if len(lines) <= max_lines:
        return lines
    head = lines[: max_lines // 2]
    tail = lines[-(max_lines - len(head)) :]
    return [*head, f"- ... 已压缩 {len(lines) - len(head) - len(tail)} 条低价值历史 ...", *tail]


def build_summary(history: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in history[-8:]:
        role = item.get("role", "unknown")
        content = _stringify_content(item.get("content", ""))
        parts.append(f"[{role}] {_clip(str(content), 200)}")
    return "\n".join(parts)


def build_compaction_summary(removed: list[dict[str, Any]], kept: list[dict[str, Any]]) -> str:
    removed_count = len(removed)
    kept_count = len(kept)
    state_lines = _extract_state_lines(removed)
    kept_preview = build_summary(kept[-3:])
    sections = [
        "Compaction checkpoint summary",
        f"- compacted_messages: {removed_count}",
        f"- kept_tail_messages: {kept_count}",
        "- preserved_state:",
        *(state_lines or ["- no prior state"]),
        "- recent_tail_preview:",
        kept_preview or "[empty]",
    ]
    return "\n".join(sections)
