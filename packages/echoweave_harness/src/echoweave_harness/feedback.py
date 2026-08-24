from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from echoweave_harness.audit import AuditEvent
from echoweave_harness.evaluation import EvalScorecard
from echoweave_harness.metrics import compute_harness_metrics


@dataclass(frozen=True)
class FeedbackSuggestion:
    kind: str
    title: str
    body: str
    priority: int = 2
    metric: str | None = None
    evidence: dict[str, object] | None = None
    action: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "title": self.title,
            "body": self.body,
            "priority": self.priority,
            "metric": self.metric,
            "evidence": self.evidence or {},
            "action": self.action,
        }


def suggest_harness_improvements(events: Iterable[AuditEvent]) -> list[FeedbackSuggestion]:
    items = list(events)
    metrics = compute_harness_metrics(items)
    suggestions: list[FeedbackSuggestion] = []
    if metrics.tool_call_success_rate is not None and metrics.tool_call_success_rate < 0.9:
        suggestions.append(
            FeedbackSuggestion(
                kind="test",
                title="补充工具失败回归测试",
                body="工具调用成功率低于 90%；应把重复失败的工具调用沉淀为测试，并改进工具错误提示。",
                priority=1,
                metric="tool_call_success_rate",
                evidence={"value": metrics.tool_call_success_rate, "threshold": 0.9},
                action="add_test_fixture",
            )
        )
    if metrics.model_call_success_rate is not None and metrics.model_call_success_rate < 0.95:
        suggestions.append(
            FeedbackSuggestion(
                kind="model",
                title="补充模型调用降级与配置校验",
                body="模型调用成功率低于 95%；应检查 provider/profile 配置，并增加模型调用失败的降级或提示测试。",
                priority=1,
                metric="model_call_success_rate",
                evidence={"value": metrics.model_call_success_rate, "threshold": 0.95},
                action="add_model_fallback_or_validation",
            )
        )
    if metrics.rag_hit_rate is not None and metrics.rag_hit_rate < 0.7:
        suggestions.append(
            FeedbackSuggestion(
                kind="rag",
                title="改进 RAG 检索 harness",
                body="RAG 命中率偏低；应增加 golden query fixtures，调参 query rewrite/rerank，或补充索引文档。",
                priority=1,
                metric="rag_hit_rate",
                evidence={"value": metrics.rag_hit_rate, "threshold": 0.7},
                action="add_rag_golden_queries",
            )
        )
    if metrics.sandbox_escape_block_rate is not None and metrics.sandbox_escape_block_rate > 0:
        suggestions.append(
            FeedbackSuggestion(
                kind="policy",
                title="把沙盒逃逸样例固化为策略测试",
                body="检测到已拦截的沙盒逃逸尝试；应把样例加入 policy DSL 回归测试，防止边界退化。",
                priority=1,
                metric="sandbox_escape_block_rate",
                evidence={"value": metrics.sandbox_escape_block_rate},
                action="add_policy_fixture",
            )
        )
    if metrics.approval_resolution_rate is not None and metrics.approval_resolution_rate < 0.8:
        suggestions.append(
            FeedbackSuggestion(
                kind="approval",
                title="缩短审批滞留路径",
                body="审批处理率低于 80%；应检查审批超时、撤销、重试流程，并在管理端突出未处理项。",
                priority=2,
                metric="approval_resolution_rate",
                evidence={"value": metrics.approval_resolution_rate, "threshold": 0.8},
                action="improve_approval_flow",
            )
        )
    repeated_errors = _repeated_errors(items)
    for reason, count in repeated_errors[:3]:
        suggestions.append(
            FeedbackSuggestion(
                kind="rule",
                title=f"固化重复失败：{reason}",
                body=f"该失败出现 {count} 次。应补充规则、文档或测试，让 Agent 更早自修复。",
                priority=2,
                evidence={"reason": reason, "count": count},
                action="add_rule_doc_or_test",
            )
        )
    return suggestions


def write_feedback_backlog(
    path: str | Path,
    suggestions: Iterable[FeedbackSuggestion],
    *,
    source_audit_log: str | None = None,
) -> int:
    backlog_path = Path(path)
    backlog_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with backlog_path.open("a", encoding="utf-8") as file:
        for suggestion in suggestions:
            record = {
                "id": uuid4().hex,
                "ts": time.time(),
                "status": "open",
                "source_audit_log": source_audit_log,
                "suggestion": suggestion.to_dict(),
            }
            file.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    return count


def suggestions_to_eval_cases(
    suggestions: Iterable[FeedbackSuggestion],
    *,
    prefix: str = "hardening",
) -> list[dict[str, object]]:
    """Convert hardening suggestions into executable eval-case drafts.

    The generated cases are intentionally conservative. They are not meant to
    claim a fix automatically; they give the next run a concrete fixture that
    exposes the failing metric and the evidence that triggered it.
    """

    cases: list[dict[str, object]] = []
    for index, suggestion in enumerate(suggestions, start=1):
        case: dict[str, object] = {
            "id": f"{prefix}-{index:03d}",
            "title": suggestion.title,
            "source_kind": suggestion.kind,
            "priority": suggestion.priority,
            "metric": suggestion.metric,
            "prompt": _prompt_for_suggestion(suggestion),
            "expected_contains": [],
            "expected_tools": [],
            "forbidden_tools": [],
            "expected_rag_sources": [],
            "expected_policy_blocks": 0,
            "expect_sandbox_escape_blocked": False,
            "evidence": suggestion.evidence or {},
            "suggested_action": suggestion.action,
        }
        if suggestion.kind == "tool":
            case["expected_tools"] = _string_list_from_evidence(suggestion.evidence, "expected")
        elif suggestion.kind == "rag":
            case["expected_rag_sources"] = _string_list_from_evidence(suggestion.evidence, "expected")
        elif suggestion.kind == "policy":
            case["expected_policy_blocks"] = 1
            case["expect_sandbox_escape_blocked"] = "sandbox" in (suggestion.metric or suggestion.title).lower()
        elif suggestion.kind == "approval":
            case["expected_policy_blocks"] = 1
        else:
            case["expected_contains"] = _string_list_from_evidence(suggestion.evidence, "expected")
        cases.append(case)
    return cases


def write_eval_fixtures(
    path: str | Path,
    suggestions: Iterable[FeedbackSuggestion],
    *,
    prefix: str = "hardening",
) -> int:
    fixture_path = Path(path)
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    cases = suggestions_to_eval_cases(suggestions, prefix=prefix)
    fixture_path.write_text(json.dumps({"cases": cases}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(cases)


def suggest_eval_hardening(case_id: str, scorecard: EvalScorecard) -> list[FeedbackSuggestion]:
    suggestions: list[FeedbackSuggestion] = []
    for criterion in scorecard.criteria:
        if criterion.passed:
            continue
        kind = _criterion_kind(criterion.name)
        suggestions.append(
            FeedbackSuggestion(
                kind=kind,
                title=f"固化 eval 失败：{criterion.name}",
                body=f"用例 {case_id} 的 {criterion.name} 未达标；应把证据沉淀为回归 fixture、策略规则或项目说明。",
                priority=1,
                metric=criterion.name,
                evidence={"case_id": case_id, **criterion.evidence},
                action=_criterion_action(criterion.name),
            )
        )
    return suggestions


def _repeated_errors(events: list[AuditEvent]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for event in events:
        if event.status not in {"error", "blocked", "denied", "failed"}:
            continue
        reason = str(event.metadata.get("reason") or event.metadata.get("reason_code") or event.action)
        counts[reason] = counts.get(reason, 0) + 1
    return sorted(counts.items(), key=lambda item: item[1], reverse=True)


def _criterion_kind(name: str) -> str:
    if "rag" in name:
        return "rag"
    if "tool" in name:
        return "tool"
    if "policy" in name or "approval" in name or "sandbox" in name:
        return "policy"
    return "test"


def _criterion_action(name: str) -> str:
    if name == "answer_quality":
        return "add_answer_golden_case"
    if name == "tool_call_correctness":
        return "add_tool_sequence_fixture"
    if name == "rag_hit_rate":
        return "add_rag_golden_query_or_index_doc"
    if name == "sandbox_escape_block_rate":
        return "add_sandbox_escape_regression"
    return "add_policy_or_eval_fixture"


def _prompt_for_suggestion(suggestion: FeedbackSuggestion) -> str:
    metric = f" metric={suggestion.metric}" if suggestion.metric else ""
    return (
        f"Run a regression for hardening item '{suggestion.title}'.{metric}\n"
        f"Expected behavior: {suggestion.body}\n"
        "Use the existing EchoWeave tools and policies; do not bypass the harness."
    )


def _string_list_from_evidence(evidence: dict[str, object] | None, key: str) -> list[str]:
    if not evidence:
        return []
    value = evidence.get(key)
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if isinstance(value, str) and value:
        return [value]
    return []
