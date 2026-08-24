from __future__ import annotations

from pathlib import Path
from typing import Any

from echoweave_runtime.session.store import SessionStore


def render_tree(node: Any, prefix: str = "") -> list[str]:
    label = f" [{node.branch_label}]" if node.branch_label else ""
    lines = [f"{prefix}{node.session_id}{label}"]
    for child in node.children:
        lines.extend(render_tree(child, prefix + "  "))
    return lines


def summarize_tool_execution(graph: dict[str, Any]) -> dict[str, int]:
    tool_nodes = [node for node in graph.get("nodes", []) if node.get("type") == "tool"]
    total = len(tool_nodes)
    ok = sum(1 for node in tool_nodes if node.get("status") == "ok")
    error = sum(1 for node in tool_nodes if node.get("status") == "error")
    running = sum(1 for node in tool_nodes if node.get("status") == "running")
    unknown = total - ok - error - running

    batched_total = 0
    batch_groups: set[str] = set()
    parallel_batch_groups: set[str] = set()
    max_batch_size = 0
    sequential_total = 0
    parallel_total = 0

    def resolve_mode(node: dict[str, Any]) -> str:
        raw_mode = node.get("execution_mode")
        if not isinstance(raw_mode, str) or not raw_mode.strip():
            raw_mode = node.get("mode")
        if not isinstance(raw_mode, str) or not raw_mode.strip():
            raw_mode = node.get("tool_execution_mode")
        mode = raw_mode.strip().lower() if isinstance(raw_mode, str) else ""
        return mode if mode in {"sequential", "parallel", "streaming"} else ""

    def normalize_positive_int(value: Any) -> int:
        if isinstance(value, int):
            return value if value > 0 else 0
        if isinstance(value, str):
            text = value.strip()
            if text.isdigit():
                parsed = int(text)
                return parsed if parsed > 0 else 0
        return 0

    for node in tool_nodes:
        mode = resolve_mode(node)
        if mode == "sequential":
            sequential_total += 1
        elif mode == "parallel":
            parallel_total += 1

        raw_batch_id = node.get("batch_id")
        if raw_batch_id is None and isinstance(node.get("batch"), dict):
            raw_batch_id = node["batch"].get("id")
        batch_id = str(raw_batch_id).strip() if raw_batch_id is not None else ""
        if not batch_id:
            continue
        batched_total += 1
        batch_groups.add(batch_id)

        raw_batch_size = node.get("batch_size")
        if raw_batch_size is None and isinstance(node.get("batch"), dict):
            raw_batch_size = node["batch"].get("size")
        batch_size = normalize_positive_int(raw_batch_size)
        if batch_size > max_batch_size:
            max_batch_size = batch_size
        if batch_size > 1:
            parallel_batch_groups.add(batch_id)

    return {
        "total": total,
        "ok": ok,
        "error": error,
        "running": running,
        "unknown": unknown,
        "batched_total": batched_total,
        "batch_groups": len(batch_groups),
        "parallel_batch_groups": len(parallel_batch_groups),
        "max_batch_size": max_batch_size,
        "sequential_total": sequential_total,
        "parallel_total": parallel_total,
    }


def empty_tool_execution_stats() -> dict[str, int]:
    return {
        "total": 0,
        "ok": 0,
        "error": 0,
        "running": 0,
        "unknown": 0,
        "batched_total": 0,
        "batch_groups": 0,
        "parallel_batch_groups": 0,
        "max_batch_size": 0,
        "sequential_total": 0,
        "parallel_total": 0,
    }


def build_session_item(session_store: SessionStore, session_path: Path) -> dict[str, Any]:
    header = session_store.read_header(session_path)
    snapshot = session_store.load_snapshot(session_path)
    graph = session_store.build_task_graph(session_store.read_events(session_path))
    return {
        "session_id": header.id,
        "path": str(session_path),
        "parent_id": header.parent_id,
        "branch_label": header.branch_label,
        "summary": snapshot.summary,
        "message_count": len(snapshot.history),
        "state": snapshot.state,
        "tool_execution": summarize_tool_execution(graph),
    }


def resolve_session_path(session_store: SessionStore, resume: bool) -> Path:
    session_path = session_store.latest() if resume else None
    if session_path is None:
        session_path = session_store.create()
    return session_path


def list_session_items(session_store: SessionStore) -> list[dict[str, Any]]:
    return [build_session_item(session_store, session_path) for session_path in session_store.list_paths()]


class SessionRuntimeFacade:
    """Agent-level session orchestration facade.

    The underlying session data store belongs to `echoweave_runtime`; operations
    that shape an agent session, such as resume, branch, checkpoint and replay,
    live here with the rest of the orchestration layer.
    """

    def __init__(self, session_store: SessionStore, session_path: Path) -> None:
        self._session_store = session_store
        self._session_path = session_path

    @classmethod
    def from_resume(cls, session_store: SessionStore, resume: bool) -> "SessionRuntimeFacade":
        return cls(session_store, resolve_session_path(session_store, resume))

    @property
    def session_path(self) -> Path:
        return self._session_path

    def session_id(self) -> str:
        return self._session_store.read_header(self._session_path).id

    def load_history(self) -> tuple[list[dict[str, Any]], str | None]:
        snapshot = self._session_store.load_snapshot(self._session_path)
        return snapshot.history, snapshot.summary

    def run_prompt(
        self,
        runtime: Any,
        prompt: str,
        history: list[dict[str, Any]] | None = None,
        summary: str | None = None,
    ) -> tuple[str, list[dict[str, Any]], str | None]:
        if history is None:
            history, summary = self.load_history()
        return runtime.run_turn(self._session_path, history, prompt, summary)

    def inspect(self) -> tuple[Any, dict[str, Any]]:
        snapshot = self._session_store.load_snapshot(self._session_path)
        graph = self._session_store.build_task_graph(self._session_store.read_events(self._session_path))
        return snapshot, graph

    def list_sessions(self) -> list[dict[str, Any]]:
        return list_session_items(self._session_store)

    def build_tree(self) -> list[str]:
        tree = self._session_store.build_tree()
        return [line for root in tree.roots for line in render_tree(root)]

    def task_graph(self, by: str = "turn") -> dict[str, Any]:
        return self._session_store.build_task_graph(self._session_store.read_events(self._session_path), by=by)

    def create_checkpoint(
        self,
        label: str | None = None,
        at_event_index: int | None = None,
        runtime_context: dict[str, str | None] | None = None,
    ) -> dict[str, Any]:
        context = runtime_context or {}
        return self._session_store.create_checkpoint(
            self._session_path,
            label=label,
            at_event_index=at_event_index,
            turn_id=context.get("turn_id"),
            trace_id=context.get("trace_id"),
            event_id=context.get("event_id"),
            parent_event_id=context.get("parent_event_id"),
        )

    def list_checkpoints(self) -> list[dict[str, Any]]:
        return self._session_store.list_checkpoints(self._session_path)

    def replay_from_checkpoint(
        self,
        checkpoint_id: str,
        until_event_index: int | None = None,
        fork: bool = False,
        runtime_context: dict[str, str | None] | None = None,
    ) -> dict[str, Any]:
        context = runtime_context or {}
        return self._session_store.replay_from_checkpoint(
            self._session_path,
            checkpoint_id=checkpoint_id,
            until_event_index=until_event_index,
            fork=fork,
            turn_id=context.get("turn_id"),
            trace_id=context.get("trace_id"),
            event_id=context.get("event_id"),
            parent_event_id=context.get("parent_event_id"),
        )

    def branch(self, label: str) -> Path:
        self._session_path = self._session_store.fork(self._session_path, label)
        return self._session_path

    def new_session(self) -> Path:
        self._session_path = self._session_store.create()
        return self._session_path

    def switch_session(self, session: str | Path) -> Path:
        self._session_path = self._session_store.resolve_session_path(session)
        return self._session_path

    def import_session(self, source_session: str | Path) -> Path:
        self._session_path = self._session_store.import_session(source_session)
        return self._session_path
