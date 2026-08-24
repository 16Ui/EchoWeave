from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from echoweave_harness.audit import AuditEvent


@dataclass(frozen=True)
class HarnessMetrics:
    answer_quality: float | None
    tool_call_success_rate: float | None
    approval_hit_rate: float | None
    approval_resolution_rate: float | None
    rag_hit_rate: float | None
    sandbox_escape_block_rate: float | None
    policy_block_rate: float | None
    model_call_success_rate: float | None
    category_status_counts: dict[str, dict[str, int]]
    total_events: int

    def to_dict(self) -> dict[str, object]:
        return {
            "answer_quality": self.answer_quality,
            "tool_call_success_rate": self.tool_call_success_rate,
            "approval_hit_rate": self.approval_hit_rate,
            "approval_resolution_rate": self.approval_resolution_rate,
            "rag_hit_rate": self.rag_hit_rate,
            "sandbox_escape_block_rate": self.sandbox_escape_block_rate,
            "policy_block_rate": self.policy_block_rate,
            "model_call_success_rate": self.model_call_success_rate,
            "category_status_counts": self.category_status_counts,
            "total_events": self.total_events,
        }


def compute_harness_metrics(events: Iterable[AuditEvent]) -> HarnessMetrics:
    items = list(events)
    quality_scores = [
        float(event.metadata["score"])
        for event in items
        if event.category == "answer" and event.action == "quality" and _is_number(event.metadata.get("score"))
    ]
    tool_events = [event for event in items if _is_tool_execution_event(event)]
    approval_events = [event for event in items if event.category == "approval" and event.action == "request"]
    approval_resolution_events = [
        event
        for event in items
        if event.category == "approval" and event.action in {"approve", "deny", "revoke", "retry"}
    ]
    command_events = [event for event in items if event.category == "command" and event.action == "policy"]
    rag_events = [event for event in items if event.category == "rag" and event.action == "retrieve"]
    sandbox_events = [event for event in items if _is_sandbox_escape_event(event)]
    model_events = [event for event in items if event.category == "model" and event.action == "call" and event.status != "start"]

    return HarnessMetrics(
        answer_quality=_avg(quality_scores),
        tool_call_success_rate=_rate(tool_events, lambda event: event.status == "ok"),
        approval_hit_rate=_ratio(len(approval_events), len(command_events)),
        approval_resolution_rate=_ratio(len(approval_resolution_events), len(approval_events)),
        rag_hit_rate=_rate(rag_events, lambda event: int(event.metadata.get("result_count") or 0) > 0),
        sandbox_escape_block_rate=_rate(sandbox_events, lambda event: event.status in {"blocked", "error", "denied"}),
        policy_block_rate=_rate(command_events, lambda event: event.status in {"blocked", "denied"}),
        model_call_success_rate=_rate(model_events, lambda event: event.status == "ok"),
        category_status_counts=_category_status_counts(items),
        total_events=len(items),
    )


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _rate(events: list[AuditEvent], predicate) -> float | None:
    if not events:
        return None
    return sum(1 for event in events if predicate(event)) / len(events)


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_sandbox_escape_event(event: AuditEvent) -> bool:
    reason_code = str(event.metadata.get("reason_code", ""))
    return event.category in {"file", "command"} and (
        reason_code.endswith("path_escape")
        or reason_code in {"deny.path_traversal", "deny.windows_absolute_path", "harness.path.not_allowed"}
    )


def _is_tool_execution_event(event: AuditEvent) -> bool:
    if event.category == "tool" and event.action == "execute":
        return True
    if event.category == "command" and event.action == "execute":
        return True
    return event.category == "file" and event.action in {"edit", "find", "grep", "list", "read", "write"}


def _category_status_counts(events: list[AuditEvent]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for event in events:
        by_status = counts.setdefault(event.category, {})
        by_status[event.status] = by_status.get(event.status, 0) + 1
    return counts
