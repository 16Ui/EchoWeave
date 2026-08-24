from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from echoweave_runtime.session.schema import sanitize_value
from echoweave_runtime.session.store import SessionStore


class ToolEffect(str, Enum):
    READ_ONLY = "read_only"
    IDEMPOTENT_WRITE = "idempotent_write"
    NON_IDEMPOTENT = "non_idempotent"
    UNKNOWN = "unknown"

    @property
    def safe_after_interruption(self) -> bool:
        return self in {ToolEffect.READ_ONLY, ToolEffect.IDEMPOTENT_WRITE}


class ToolInvocationBlockedError(RuntimeError):
    """Raised when replay safety cannot be established automatically."""


@dataclass(frozen=True)
class InvocationDecision:
    action: str
    invocation_key: str
    identity: str
    fingerprint: str
    effect: ToolEffect
    attempt: int
    outcome: dict[str, Any] | None = None
    reason: str | None = None


def resolve_tool_effect(tool_name: str, tool: Any, tool_input: Any | None = None) -> ToolEffect:
    classify = getattr(tool, "classify_effect", None)
    if callable(classify) and tool_input is not None:
        try:
            classified = classify(tool_input)
            return classified if isinstance(classified, ToolEffect) else ToolEffect(str(classified))
        except (TypeError, ValueError):
            return ToolEffect.UNKNOWN
    declared = getattr(tool, "effect", None)
    try:
        if isinstance(declared, ToolEffect):
            return declared
        return ToolEffect(str(declared)) if declared is not None else ToolEffect.UNKNOWN
    except ValueError:
        return ToolEffect.UNKNOWN


def build_invocation_identity(session_id: str, turn_id: str, tool_call_id: str) -> str:
    return f"{session_id}:{turn_id}:{tool_call_id}"


def build_invocation_fingerprint(tool_name: str, tool_input: Any) -> str:
    canonical = json.dumps(
        sanitize_value(tool_input),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(f"{tool_name}\0{canonical}".encode("utf-8")).hexdigest()


def build_invocation_key(identity: str, fingerprint: str) -> str:
    return hashlib.sha256(f"{identity}\0{fingerprint}".encode("utf-8")).hexdigest()


class ToolInvocationLedger:
    """Append-only tool execution ledger backed by the session event stream."""

    def __init__(self, session_store: SessionStore) -> None:
        self.session_store = session_store
        self._lock = threading.RLock()
        self._in_flight: set[str] = set()

    def prepare(
        self,
        session_path: Path,
        *,
        turn_id: str,
        trace_id: str | None,
        tool_call_id: str,
        tool_name: str,
        tool_input: Any,
        effect: ToolEffect,
    ) -> InvocationDecision:
        session_id = self.session_store.read_header(session_path).id
        identity = build_invocation_identity(session_id, turn_id, tool_call_id)
        fingerprint = build_invocation_fingerprint(tool_name, tool_input)
        invocation_key = build_invocation_key(identity, fingerprint)
        with self._lock:
            records = self._records(session_path, identity)
            conflicting = next(
                (
                    record
                    for record in records
                    if record.get("event") in {"tool.invocation_started", "tool.invocation_completed"}
                    and record.get("fingerprint") != fingerprint
                ),
                None,
            )
            if conflicting is not None:
                reason = "tool call identity was reused with different name or input"
                self._append_decision(
                    session_path,
                    "tool.invocation_blocked",
                    invocation_key=invocation_key,
                    identity=identity,
                    fingerprint=fingerprint,
                    turn_id=turn_id,
                    trace_id=trace_id,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    effect=effect,
                    reason=reason,
                )
                return InvocationDecision(
                    "conflict", invocation_key, identity, fingerprint, effect, 0, reason=reason
                )

            matching = [record for record in records if record.get("fingerprint") == fingerprint]
            completed = next(
                (record for record in reversed(matching) if record.get("event") == "tool.invocation_completed"),
                None,
            )
            if completed is not None:
                outcome = completed.get("outcome")
                normalized_outcome = outcome if isinstance(outcome, dict) else {"status": "error", "error": "missing ledger outcome"}
                attempt = int(completed.get("attempt") or 1)
                self._append_decision(
                    session_path,
                    "tool.invocation_reused",
                    invocation_key=invocation_key,
                    identity=identity,
                    fingerprint=fingerprint,
                    turn_id=turn_id,
                    trace_id=trace_id,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    effect=effect,
                    attempt=attempt,
                    outcome=normalized_outcome,
                )
                return InvocationDecision(
                    "reuse",
                    invocation_key,
                    identity,
                    fingerprint,
                    effect,
                    attempt,
                    outcome=normalized_outcome,
                )

            if invocation_key in self._in_flight:
                reason = "the same invocation is already executing in this runtime"
                self._append_decision(
                    session_path,
                    "tool.invocation_blocked",
                    invocation_key=invocation_key,
                    identity=identity,
                    fingerprint=fingerprint,
                    turn_id=turn_id,
                    trace_id=trace_id,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    effect=effect,
                    reason=reason,
                )
                return InvocationDecision(
                    "in_flight", invocation_key, identity, fingerprint, effect, 0, reason=reason
                )

            attempts = [int(record.get("attempt") or 1) for record in matching if record.get("event") == "tool.invocation_started"]
            if attempts and not effect.safe_after_interruption:
                reason = "previous execution has no durable result and the tool is not safe to replay"
                self._append_decision(
                    session_path,
                    "tool.invocation_blocked",
                    invocation_key=invocation_key,
                    identity=identity,
                    fingerprint=fingerprint,
                    turn_id=turn_id,
                    trace_id=trace_id,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    effect=effect,
                    attempt=max(attempts),
                    reason=reason,
                )
                return InvocationDecision(
                    "indeterminate",
                    invocation_key,
                    identity,
                    fingerprint,
                    effect,
                    max(attempts),
                    reason=reason,
                )

            attempt = max(attempts, default=0) + 1
            self._append_decision(
                session_path,
                "tool.invocation_started",
                invocation_key=invocation_key,
                identity=identity,
                fingerprint=fingerprint,
                turn_id=turn_id,
                trace_id=trace_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                effect=effect,
                attempt=attempt,
                replay_after_interruption=bool(attempts),
            )
            self._in_flight.add(invocation_key)
            return InvocationDecision("execute", invocation_key, identity, fingerprint, effect, attempt)

    def complete(
        self,
        session_path: Path,
        decision: InvocationDecision,
        *,
        turn_id: str,
        trace_id: str | None,
        tool_call_id: str,
        tool_name: str,
        outcome: dict[str, Any],
    ) -> None:
        if decision.action != "execute":
            return
        with self._lock:
            self._append_decision(
                session_path,
                "tool.invocation_completed",
                invocation_key=decision.invocation_key,
                identity=decision.identity,
                fingerprint=decision.fingerprint,
                turn_id=turn_id,
                trace_id=trace_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                effect=decision.effect,
                attempt=decision.attempt,
                outcome=sanitize_value(outcome),
            )
            self._in_flight.discard(decision.invocation_key)

    def _records(self, session_path: Path, identity: str) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for event in self.session_store.read_events(session_path):
            if not event.type.startswith("tool.invocation_"):
                continue
            if event.payload.get("identity") != identity:
                continue
            records.append({"event": event.type, **event.payload})
        return records

    def _append_decision(
        self,
        session_path: Path,
        event_type: str,
        *,
        invocation_key: str,
        identity: str,
        fingerprint: str,
        turn_id: str,
        trace_id: str | None,
        tool_call_id: str,
        tool_name: str,
        effect: ToolEffect,
        attempt: int | None = None,
        outcome: dict[str, Any] | None = None,
        reason: str | None = None,
        replay_after_interruption: bool = False,
    ) -> None:
        payload: dict[str, Any] = {
            "invocation_key": invocation_key,
            "identity": identity,
            "fingerprint": fingerprint,
            "turn_id": turn_id,
            "trace_id": trace_id,
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "effect": effect.value,
        }
        if attempt is not None:
            payload["attempt"] = attempt
        if outcome is not None:
            payload["outcome"] = outcome
        if reason is not None:
            payload["reason"] = reason
        if replay_after_interruption:
            payload["replay_after_interruption"] = True
        self.session_store.append(session_path, event_type, payload)
