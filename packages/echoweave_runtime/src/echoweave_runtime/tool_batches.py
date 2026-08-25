from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from echoweave_runtime.session.schema import sanitize_value
from echoweave_runtime.session.store import SessionStore
from echoweave_runtime.tool_invocations import ToolEffect, ToolInvocationBlockedError


ToolBatchEventSink = Callable[[str, dict[str, Any]], None]


class ToolBatchConflictError(ToolInvocationBlockedError):
    """Raised when a recovered batch position contains different members."""

    def __init__(self, batch_key: str, reason: str) -> None:
        super().__init__(f"conflicting tool batch {batch_key}: {reason}")
        self.batch_key = batch_key
        self.reason = reason


@dataclass(frozen=True)
class ToolBatchDecision:
    action: str
    batch_key: str
    identity: str
    fingerprint: str
    turn_id: str
    sequence: int
    attempt: int
    mode: str
    members: tuple[dict[str, Any], ...]
    recovery_summary: dict[str, Any]
    reason: str | None = None


def build_tool_batch_identity(session_id: str, turn_id: str, sequence: int) -> str:
    return f"{session_id}:{turn_id}:batch:{sequence}"


def build_tool_batch_key(identity: str) -> str:
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def build_tool_batch_fingerprint(members: list[dict[str, Any]]) -> str:
    canonical = json.dumps(
        sanitize_value(members),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ToolBatchLedger:
    """Durable lifecycle for an ordered group of model-produced tool calls."""

    _START_EVENTS = {
        "tool.batch_started",
        "tool.batch_resumed",
        "tool.batch_replayed",
    }

    def __init__(self, session_store: SessionStore) -> None:
        self.session_store = session_store
        self._lock = threading.RLock()

    def prepare(
        self,
        session_path: Path,
        *,
        turn_id: str,
        trace_id: str | None,
        sequence: int,
        mode: str,
        members: list[dict[str, Any]],
        on_event: ToolBatchEventSink | None = None,
    ) -> ToolBatchDecision:
        normalized_members = self._normalize_members(members)
        session_id = self.session_store.read_header(session_path).id
        identity = build_tool_batch_identity(session_id, turn_id, sequence)
        batch_key = build_tool_batch_key(identity)
        fingerprint = build_tool_batch_fingerprint(normalized_members)
        with self._lock:
            records = self._records(session_path, identity)
            prior = [record for record in records if record["event"] in self._START_EVENTS]
            conflict = next(
                (record for record in prior if record.get("fingerprint") != fingerprint),
                None,
            )
            if conflict is not None:
                reason = "the same logical batch position contains different tool calls"
                payload = self._base_payload(
                    batch_key=batch_key,
                    identity=identity,
                    fingerprint=fingerprint,
                    turn_id=turn_id,
                    trace_id=trace_id,
                    sequence=sequence,
                    attempt=max((int(record.get("attempt") or 1) for record in prior), default=1),
                    mode=mode,
                    members=normalized_members,
                )
                payload.update(
                    {
                        "reason": reason,
                        "expected_fingerprint": conflict.get("fingerprint"),
                        "actual_fingerprint": fingerprint,
                    }
                )
                self._append(session_path, "tool.batch_conflict", payload, on_event)
                return ToolBatchDecision(
                    "conflict",
                    batch_key,
                    identity,
                    fingerprint,
                    turn_id,
                    sequence,
                    int(payload["attempt"]),
                    mode,
                    tuple(normalized_members),
                    self.summarize(session_path, batch_key, normalized_members),
                    reason,
                )

            attempt = max((int(record.get("attempt") or 1) for record in prior), default=0) + 1
            completed = any(record["event"] == "tool.batch_completed" for record in records)
            if completed:
                action = "replay"
                event_type = "tool.batch_replayed"
            elif prior:
                action = "resume"
                event_type = "tool.batch_resumed"
            else:
                action = "start"
                event_type = "tool.batch_started"
            recovery_summary = self.summarize(session_path, batch_key, normalized_members)
            payload = self._base_payload(
                batch_key=batch_key,
                identity=identity,
                fingerprint=fingerprint,
                turn_id=turn_id,
                trace_id=trace_id,
                sequence=sequence,
                attempt=attempt,
                mode=mode,
                members=normalized_members,
            )
            payload["recovery_summary"] = recovery_summary
            self._append(session_path, event_type, payload, on_event)
            return ToolBatchDecision(
                action,
                batch_key,
                identity,
                fingerprint,
                turn_id,
                sequence,
                attempt,
                mode,
                tuple(normalized_members),
                recovery_summary,
            )

    def complete(
        self,
        session_path: Path,
        decision: ToolBatchDecision,
        *,
        trace_id: str | None,
        results: list[dict[str, Any]],
        on_event: ToolBatchEventSink | None = None,
    ) -> dict[str, Any]:
        normalized_results = sanitize_value(results)
        if not isinstance(normalized_results, list):
            normalized_results = []
        error_count = sum(
            1 for result in normalized_results if isinstance(result, dict) and result.get("status") == "error"
        )
        payload = self._decision_payload(decision, trace_id=trace_id)
        payload.update(
            {
                "status": "completed_with_errors" if error_count else "completed",
                "result_summary": {
                    "member_count": len(decision.members),
                    "ok_count": len(normalized_results) - error_count,
                    "error_count": error_count,
                    "results": normalized_results,
                },
                "recovery_summary": self.summarize(
                    session_path,
                    decision.batch_key,
                    list(decision.members),
                    trace_id=trace_id,
                ),
            }
        )
        self._append(session_path, "tool.batch_completed", payload, on_event)
        return payload

    def suspend(
        self,
        session_path: Path,
        decision: ToolBatchDecision,
        *,
        trace_id: str | None,
        reason: str,
        blocked_tool_call_id: str | None = None,
        on_event: ToolBatchEventSink | None = None,
    ) -> dict[str, Any]:
        payload = self._decision_payload(decision, trace_id=trace_id)
        payload.update(
            {
                "status": "suspended",
                "reason": reason,
                "blocked_tool_call_id": blocked_tool_call_id,
                "recovery_summary": self.summarize(
                    session_path,
                    decision.batch_key,
                    list(decision.members),
                    trace_id=trace_id,
                ),
            }
        )
        self._append(session_path, "tool.batch_suspended", payload, on_event)
        return payload

    def summarize(
        self,
        session_path: Path,
        batch_key: str,
        members: list[dict[str, Any]],
        *,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_members = self._normalize_members(members)
        records_by_index: dict[int, list[dict[str, Any]]] = {}
        for event in self.session_store.read_events(session_path):
            if not event.type.startswith("tool.invocation_"):
                continue
            if event.payload.get("batch_id") != batch_key:
                continue
            try:
                index = int(event.payload.get("batch_index") or 0)
            except (TypeError, ValueError):
                continue
            if index > 0:
                records_by_index.setdefault(index, []).append({"event": event.type, **event.payload})

        member_states: list[dict[str, Any]] = []
        for member in normalized_members:
            index = int(member["index"])
            records = records_by_index.get(index, [])
            started = [record for record in records if record["event"] == "tool.invocation_started"]
            completed = next(
                (record for record in reversed(records) if record["event"] == "tool.invocation_completed"),
                None,
            )
            resolution = next(
                (record for record in reversed(records) if record["event"] == "tool.invocation_resolved"),
                None,
            )
            blocked = next(
                (record for record in reversed(records) if record["event"] == "tool.invocation_blocked"),
                None,
            )
            state = "pending"
            outcome = None
            if completed is not None:
                outcome = completed.get("outcome")
                state = "completed_error" if isinstance(outcome, dict) and outcome.get("status") == "error" else "completed"
            elif resolution is not None and resolution.get("resolution") == "confirm_completed":
                outcome = resolution.get("outcome")
                state = "completed_error" if isinstance(outcome, dict) and outcome.get("status") == "error" else "completed"
            elif started:
                effect = str(started[-1].get("effect") or ToolEffect.UNKNOWN.value)
                authorized_attempt = int(resolution.get("authorized_attempt") or 0) if resolution else 0
                max_attempt = max(int(record.get("attempt") or 1) for record in started)
                if authorized_attempt > max_attempt or effect in {
                    ToolEffect.READ_ONLY.value,
                    ToolEffect.IDEMPOTENT_WRITE.value,
                }:
                    state = "retryable"
                else:
                    state = "indeterminate"
            elif blocked is not None:
                state = "blocked"
            member_states.append(
                {
                    "index": index,
                    "tool_call_id": member["id"],
                    "tool_name": member["name"],
                    "state": state,
                    "invocation_key": next(
                        (record.get("invocation_key") for record in reversed(records) if record.get("invocation_key")),
                        None,
                    ),
                    "outcome": sanitize_value(outcome) if outcome is not None else None,
                }
            )

        state_counts: dict[str, int] = {}
        for member in member_states:
            state = str(member["state"])
            state_counts[state] = state_counts.get(state, 0) + 1
        activity = {"executed": 0, "reused": 0, "blocked": 0}
        if trace_id:
            for records in records_by_index.values():
                for record in records:
                    if record.get("trace_id") != trace_id:
                        continue
                    if record["event"] == "tool.invocation_completed":
                        activity["executed"] += 1
                    elif record["event"] == "tool.invocation_reused":
                        activity["reused"] += 1
                    elif record["event"] == "tool.invocation_blocked":
                        activity["blocked"] += 1
        return {
            "counts": state_counts,
            "members": member_states,
            "attempt_activity": activity,
        }

    @staticmethod
    def _normalize_members(members: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for index, member in enumerate(members, start=1):
            normalized.append(
                {
                    "index": int(member.get("index") or index),
                    "id": str(member.get("id") or ""),
                    "name": str(member.get("name") or ""),
                    "input": sanitize_value(member.get("input") or {}),
                }
            )
        return normalized

    def _records(self, session_path: Path, identity: str) -> list[dict[str, Any]]:
        return [
            {"event": event.type, **event.payload}
            for event in self.session_store.read_events(session_path)
            if event.type.startswith("tool.batch_") and event.payload.get("identity") == identity
        ]

    @staticmethod
    def _base_payload(
        *,
        batch_key: str,
        identity: str,
        fingerprint: str,
        turn_id: str,
        trace_id: str | None,
        sequence: int,
        attempt: int,
        mode: str,
        members: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "batch_id": batch_key,
            "identity": identity,
            "fingerprint": fingerprint,
            "turn_id": turn_id,
            "trace_id": trace_id,
            "sequence": sequence,
            "attempt": attempt,
            "mode": mode,
            "batch_size": len(members),
            "members": sanitize_value(members),
        }

    def _decision_payload(
        self,
        decision: ToolBatchDecision,
        *,
        trace_id: str | None,
    ) -> dict[str, Any]:
        return self._base_payload(
            batch_key=decision.batch_key,
            identity=decision.identity,
            fingerprint=decision.fingerprint,
            turn_id=decision.turn_id,
            trace_id=trace_id,
            sequence=decision.sequence,
            attempt=decision.attempt,
            mode=decision.mode,
            members=list(decision.members),
        )

    def _append(
        self,
        session_path: Path,
        event_type: str,
        payload: dict[str, Any],
        on_event: ToolBatchEventSink | None,
    ) -> None:
        durable_payload = sanitize_value(payload)
        self.session_store.append(session_path, event_type, durable_payload)
        if on_event is not None:
            on_event(event_type, durable_payload)
