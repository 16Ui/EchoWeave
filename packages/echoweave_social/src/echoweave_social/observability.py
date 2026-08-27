from __future__ import annotations

from pathlib import Path
from typing import Any

from echoweave_runtime.observability import build_trace_timeline
from echoweave_runtime.session.store import SessionStore
from echoweave_social.agent_runtime import EchoWeaveSocialAgent, SocialRecoveryContext


class SocialTraceExplorer:
    """Read-only aggregation of trace timelines for registered social sessions."""

    def __init__(self, agent: EchoWeaveSocialAgent) -> None:
        self.agent = agent

    def snapshot(
        self,
        *,
        limit: int = 50,
        event_limit_per_trace: int = 120,
    ) -> dict[str, Any]:
        limit = max(1, min(int(limit), 200))
        event_limit_per_trace = max(1, min(int(event_limit_per_trace), 500))
        grouped = self._group_contexts(self.agent.session_contexts())
        traces: list[dict[str, Any]] = []
        issues: list[dict[str, Any]] = []
        scanned_sessions = 0
        scoped_events = 0

        for session_key, contexts in grouped.items():
            session_path = contexts[0].session_path
            try:
                store = SessionStore(session_path.parent)
                header = store.read_header(session_path)
                projection = build_trace_timeline(
                    store.read_events(session_path),
                    session_id=header.id,
                    event_limit_per_trace=event_limit_per_trace,
                )
            except Exception as exc:
                issues.append(
                    {
                        "session_path": str(session_path),
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    }
                )
                continue
            scanned_sessions += 1
            scoped_events += int(projection["scoped_event_count"])
            conversation_keys = [context.conversation_key for context in contexts]
            for trace in projection["traces"]:
                traces.append(
                    {
                        **trace,
                        "conversation_key": conversation_keys[0],
                        "conversation_keys": conversation_keys,
                        "workspace": str(contexts[0].workspace),
                        "session_path": session_key,
                    }
                )

        traces.sort(
            key=lambda item: (item.get("started_at") or "", item["trace_id"]),
            reverse=True,
        )
        traces = traces[:limit]
        status_counts: dict[str, int] = {}
        category_counts: dict[str, int] = {}
        for trace in traces:
            status = str(trace.get("status") or "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1
            for category, count in trace.get("categories", {}).items():
                category_counts[str(category)] = (
                    category_counts.get(str(category), 0) + int(count)
                )
        return {
            "ok": True,
            "stats": {
                "registered_sessions": len(grouped),
                "scanned_sessions": scanned_sessions,
                "trace_count": len(traces),
                "scoped_event_count": scoped_events,
                "signal_count": sum(
                    int(trace.get("signal_count") or 0) for trace in traces
                ),
                "status_counts": status_counts,
                "category_counts": category_counts,
            },
            "traces": traces,
            "issues": issues,
        }

    @staticmethod
    def _group_contexts(
        contexts: tuple[SocialRecoveryContext, ...],
    ) -> dict[str, list[SocialRecoveryContext]]:
        grouped: dict[str, list[SocialRecoveryContext]] = {}
        for context in contexts:
            key = str(Path(context.session_path).expanduser().resolve())
            grouped.setdefault(key, []).append(context)
        return grouped


__all__ = ["SocialTraceExplorer"]
