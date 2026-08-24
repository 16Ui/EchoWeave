from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from echoweave_runtime.session.schema import SessionHeader, SessionSnapshot, StoredEvent
from echoweave_runtime.session.tree import SessionTree, SessionTreeNode


class SessionStore:
    """会话事件存储：使用 JSONL 记录事件，并可从事件重建运行快照。"""

    def __init__(self, sessions_dir: Path) -> None:
        self.sessions_dir = sessions_dir
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_session_id(self, session_path: Path) -> str | None:
        try:
            with session_path.open("r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    event = StoredEvent.from_json(line)
                    if event.type == "session":
                        header_id = event.payload.get("id") if isinstance(event.payload, dict) else None
                        if isinstance(header_id, str) and header_id.strip():
                            return header_id.strip()
                    break
        except OSError:
            return None
        except ValueError:
            return None
        return None

    def create(self, parent_id: str | None = None, branch_label: str | None = None) -> Path:
        """创建新会话文件并写入首条 session header 事件。"""
        session_id = str(uuid.uuid4())
        path = self.sessions_dir / f"{session_id}.jsonl"
        header = StoredEvent(
            type="session",
            payload={"id": session_id, "parent_id": parent_id, "branch_label": branch_label},
            session_id=session_id,
        )
        path.write_text(header.to_json() + "\n", encoding="utf-8")
        return path

    def latest(self) -> Path | None:
        files = self.list_paths()
        return files[0] if files else None

    def list_paths(self) -> list[Path]:
        return sorted(self.sessions_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)

    def append(self, session_path: Path, event_type: str, payload: dict[str, Any]) -> None:
        """以 append-only 方式追加事件，保证会话历史可回放。"""
        session_id = self._resolve_session_id(session_path)
        with session_path.open("a", encoding="utf-8") as f:
            f.write(StoredEvent(type=event_type, payload=payload, session_id=session_id).to_json() + "\n")

    def resolve_session_path(self, session: str | Path) -> Path:
        text = str(session).strip()
        if not text:
            raise ValueError("session reference is required")

        raw_path = Path(text).expanduser()
        candidates: list[Path] = [
            self.sessions_dir / f"{text}.jsonl",
            self.sessions_dir / text,
        ]
        if raw_path.is_absolute():
            candidates.append(raw_path)
        else:
            candidates.append((self.sessions_dir / raw_path).resolve())
            candidates.append((Path.cwd() / raw_path).resolve())

        for candidate in candidates:
            if candidate.exists() and candidate.is_file():
                return candidate.resolve()
        raise ValueError(f"session not found: {text}")

    def import_session(self, source_session: str | Path) -> Path:
        source_text = str(source_session).strip()
        if not source_text:
            raise ValueError("source session path is required")

        source_path = Path(source_text).expanduser()
        if not source_path.is_absolute():
            source_path = (Path.cwd() / source_path).resolve()
        source_path = source_path.resolve()
        if not source_path.exists() or not source_path.is_file():
            raise ValueError(f"source session not found: {source_text}")

        if source_path.parent == self.sessions_dir.resolve():
            raise ValueError("source session is already in current store; use switch")

        source_header = self.read_header(source_path)
        source_events = self.read_events(source_path)

        imported_path = self.create(
            parent_id=source_header.parent_id,
            branch_label=source_header.branch_label,
        )
        self.append(
            imported_path,
            "session.imported",
            {
                "source_session_id": source_header.id,
                "source_session_path": str(source_path),
            },
        )
        for event in source_events[1:]:
            self.append(imported_path, event.type, event.payload)
        return imported_path

    def read_events(self, session_path: Path) -> list[StoredEvent]:
        events: list[StoredEvent] = []
        for line in session_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(StoredEvent.from_json(line))
        return events

    def read_header(self, session_path: Path) -> SessionHeader:
        events = self.read_events(session_path)
        if not events:
            raise ValueError("session is empty")
        header_event = events[0]
        if header_event.type != "session":
            raise ValueError("session header missing")
        return SessionHeader(
            id=header_event.payload["id"],
            parent_id=header_event.payload.get("parent_id"),
            branch_label=header_event.payload.get("branch_label"),
        )

    def load_snapshot(self, session_path: Path) -> SessionSnapshot:
        """
        从事件流重建可恢复快照。

        说明：snapshot 是“状态数据”，用于 resume/继续会话；
        并非新分支动作，分叉由 fork() 显式触发。
        """
        events = self.read_events(session_path)
        if not events:
            raise ValueError("session is empty")
        return self._build_snapshot(session_path, events)

    def load_snapshot_at(self, session_path: Path, event_index: int) -> SessionSnapshot:
        """Rebuild the visible session state at an inclusive event index."""
        events = self.read_events(session_path)
        index = int(event_index)
        if index < 0 or index >= len(events):
            raise ValueError("event_index out of range")
        return self._build_snapshot(session_path, events[: index + 1])

    def _build_snapshot(
        self,
        session_path: Path,
        events: list[StoredEvent],
    ) -> SessionSnapshot:
        header = self.read_header(session_path)
        history: list[dict[str, Any]] = []
        summary: str | None = None
        compaction: dict[str, Any] | None = None
        state: dict[str, Any] | None = None
        for event in events[1:]:
            if event.type == "message":
                history.append(event.payload)
            elif event.type == "history_reset":
                reset_history = event.payload.get("history")
                if isinstance(reset_history, list):
                    history = reset_history
                reset_summary = event.payload.get("summary")
                summary = reset_summary if isinstance(reset_summary, str) else None
                compaction = None
            elif event.type == "tool_result":
                history.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": event.payload["id"],
                                "content": event.payload.get("content", ""),
                            }
                        ],
                    }
                )
            elif event.type == "tool_error":
                history.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": event.payload["id"],
                                "content": event.payload.get("error", ""),
                                "is_error": True,
                            }
                        ],
                    }
                )
            elif event.type == "summary":
                summary = event.payload.get("content")
            elif event.type == "compaction":
                # compaction 会携带 kept_history，加载时优先用它作为当前可见历史。
                compaction = event.payload
                kept_history = event.payload.get("kept_history")
                if isinstance(kept_history, list):
                    history = kept_history
                summary = event.payload.get("summary") or event.payload.get("content") or summary
            elif event.type == "state":
                snapshot = event.payload.get("snapshot")
                if isinstance(snapshot, dict):
                    state = snapshot
            elif event.type == "state_update":
                patch = event.payload.get("patch")
                if isinstance(patch, dict):
                    if state is None:
                        state = {}
                    state.update(patch)
            elif event.type == "state_reset":
                state = {}
        return SessionSnapshot(header=header, history=history, summary=summary, compaction=compaction, state=state)

    def load_history(self, session_path: Path) -> tuple[list[dict[str, Any]], str | None]:
        snapshot = self.load_snapshot(session_path)
        return snapshot.history, snapshot.summary

    def build_tree(self) -> SessionTree:
        """扫描会话文件并根据 parent_id 组装谱系树。"""
        tree = SessionTree()
        nodes: dict[str, SessionTreeNode] = {}
        for session_path in sorted(self.sessions_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime):
            header = self.read_header(session_path)
            nodes[header.id] = SessionTreeNode(
                session_id=header.id,
                parent_id=header.parent_id,
                branch_label=header.branch_label,
                path=session_path,
            )
        for node in nodes.values():
            tree.attach(node)
        return tree

    def fork(self, session_path: Path, branch_label: str) -> Path:
        """从当前会话分叉：创建子会话并复制历史事件。"""
        snapshot = self.load_snapshot(session_path)
        branch_path = self.create(parent_id=snapshot.header.id, branch_label=branch_label)
        self.append(
            branch_path,
            "branch",
            {
                "from_session_id": snapshot.header.id,
                "branch_label": branch_label,
                "source_session": session_path.name,
            },
        )
        for event in self.read_events(session_path)[1:]:
            # 跳过原 session header，只复制业务事件到分支。
            self.append(branch_path, event.type, event.payload)
        return branch_path

    def build_task_graph(self, events: list[StoredEvent], by: str = "turn") -> dict[str, Any]:
        """基于事件流构建任务图摘要（节点、边、统计信息）。"""
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        node_map: dict[str, dict[str, Any]] = {}
        tool_node_ids: dict[str, str] = {}
        turn_node_ids: set[str] = set()
        last_turn_node: str | None = None
        last_node_by_turn: dict[str, str] = {}
        inferred_turn_index = 0
        active_turn_id = "turn-0"

        def append_node(node: dict[str, Any]) -> None:
            nodes.append(node)
            node_map[node["id"]] = node

        def add_edge(from_id: str | None, to_id: str, kind: str) -> None:
            if not from_id or from_id == to_id:
                return
            edges.append({"from": from_id, "to": to_id, "kind": kind})

        def ensure_turn_node(turn_id: str) -> None:
            nonlocal last_turn_node
            turn_node_id = f"turn:{turn_id}"
            if turn_node_id in turn_node_ids:
                return
            append_node(
                {
                    "id": turn_node_id,
                    "type": "turn",
                    "status": "ok",
                    "turn_id": turn_id,
                    "tool_name": None,
                    "start_ts": None,
                    "end_ts": None,
                }
            )
            if last_turn_node is not None:
                add_edge(last_turn_node, turn_node_id, "next_turn")
            turn_node_ids.add(turn_node_id)
            last_turn_node = turn_node_id

        def normalize_positive_int(value: Any) -> int | None:
            if isinstance(value, int):
                return value if value > 0 else None
            if isinstance(value, str):
                text = value.strip()
                if text.isdigit():
                    parsed = int(text)
                    return parsed if parsed > 0 else None
            return None

        def resolve_batch(data: dict[str, Any]) -> dict[str, Any]:
            batch_payload = data.get("batch") if isinstance(data.get("batch"), dict) else {}
            raw_batch_id = data.get("batch_id") or batch_payload.get("id")
            batch_id = str(raw_batch_id).strip() if raw_batch_id is not None else ""
            return {
                "batch_id": batch_id or None,
                "batch_size": normalize_positive_int(data.get("batch_size") or batch_payload.get("size")),
                "batch_index": normalize_positive_int(data.get("batch_index") or batch_payload.get("index")),
            }

        def resolve_execution_mode(data: dict[str, Any]) -> str | None:
            raw_mode = data.get("mode")
            if not isinstance(raw_mode, str) or not raw_mode.strip():
                raw_mode = data.get("tool_execution_mode")
            if isinstance(raw_mode, str):
                mode = raw_mode.strip().lower()
                if mode in {"sequential", "parallel", "streaming"}:
                    return mode
            return None

        for index, event in enumerate(events):
            payload = event.payload if isinstance(event.payload, dict) else {}
            explicit_turn = payload.get("turn_id") if by == "turn" else None
            if isinstance(explicit_turn, str) and explicit_turn.strip():
                active_turn_id = explicit_turn.strip()
            elif by == "turn" and event.type == "turn_start":
                turn_payload = payload.get("turn") if isinstance(payload.get("turn"), dict) else {}
                input_text = turn_payload.get("input")
                inferred_turn_index += 1
                if isinstance(input_text, str) and input_text.strip():
                    active_turn_id = f"turn-{inferred_turn_index}:{input_text[:24]}"
                else:
                    active_turn_id = f"turn-{inferred_turn_index}"
            elif by == "turn" and event.type == "message" and payload.get("role") == "user" and isinstance(payload.get("content"), str):
                inferred_turn_index += 1
                active_turn_id = f"turn-{inferred_turn_index}"
            elif by != "turn":
                active_turn_id = "global"

            ensure_turn_node(active_turn_id)
            turn_node_id = f"turn:{active_turn_id}"

            if event.type == "turn.state_changed":
                turn_node = node_map[turn_node_id]
                state_value = str(payload.get("state") or "")
                if state_value in {"failed", "timed_out", "cancelled"}:
                    turn_node["status"] = "error"
                elif state_value == "suspended":
                    turn_node["status"] = "blocked"
                elif state_value == "completed":
                    turn_node["status"] = "ok"
                else:
                    turn_node["status"] = "running"
                turn_node["attempt"] = normalize_positive_int(payload.get("attempt")) or 1
                if state_value == "created":
                    turn_node["start_ts"] = event.timestamp
                if state_value in {"completed", "failed", "timed_out", "cancelled", "suspended"}:
                    turn_node["end_ts"] = event.timestamp
            elif event.type == "tool_call":
                tool_id = str(payload.get("id") or f"tool-{index}")
                node_id = f"tool:{tool_id}"
                tool_node_ids[tool_id] = node_id
                append_node(
                    {
                        "id": node_id,
                        "type": "tool",
                        "status": "running",
                        "turn_id": active_turn_id,
                        "tool_name": payload.get("name"),
                        **resolve_batch(payload),
                        "execution_mode": resolve_execution_mode(payload),
                        "start_ts": None,
                        "end_ts": None,
                    }
                )
                add_edge(last_node_by_turn.get(active_turn_id, turn_node_id), node_id, "sequence")
                add_edge(turn_node_id, node_id, "contains")
                last_node_by_turn[active_turn_id] = node_id
            elif event.type in {"tool_execution_start", "tool_execution_update", "tool_execution_end"}:
                tool_id = str(payload.get("id") or f"tool-{index}")
                node_id = tool_node_ids.get(tool_id)
                if node_id is None:
                    node_id = f"tool:{tool_id}"
                    tool_node_ids[tool_id] = node_id
                    append_node(
                        {
                            "id": node_id,
                            "type": "tool",
                            "status": "running",
                            "turn_id": active_turn_id,
                            "tool_name": payload.get("name"),
                            **resolve_batch(payload),
                            "execution_mode": resolve_execution_mode(payload),
                            "start_ts": None,
                            "end_ts": None,
                        }
                    )
                    add_edge(last_node_by_turn.get(active_turn_id, turn_node_id), node_id, "sequence")
                    add_edge(turn_node_id, node_id, "contains")
                tool_node = node_map[node_id]
                if isinstance(payload.get("name"), str) and payload.get("name"):
                    tool_node["tool_name"] = payload.get("name")
                resolved_batch = resolve_batch(payload)
                if resolved_batch["batch_id"] is not None:
                    tool_node["batch_id"] = resolved_batch["batch_id"]
                if resolved_batch["batch_size"] is not None:
                    tool_node["batch_size"] = resolved_batch["batch_size"]
                if resolved_batch["batch_index"] is not None:
                    tool_node["batch_index"] = resolved_batch["batch_index"]
                resolved_mode = resolve_execution_mode(payload)
                if resolved_mode is not None:
                    tool_node["execution_mode"] = resolved_mode
                if event.type == "tool_execution_end":
                    status = str(payload.get("status", "ok"))
                    tool_node["status"] = "error" if status == "error" else "ok"
                    tool_node["end_ts"] = tool_node.get("end_ts")
                elif event.type == "tool_execution_start":
                    tool_node["status"] = "running"
                last_node_by_turn[active_turn_id] = node_id
            elif event.type in {"tool_result", "tool_error"}:
                tool_id = str(payload.get("id") or f"tool-{index}")
                node_id = tool_node_ids.get(tool_id)
                if node_id is None:
                    node_id = f"tool:{tool_id}"
                    append_node(
                        {
                            "id": node_id,
                            "type": "tool",
                            "status": "ok",
                            "turn_id": active_turn_id,
                            "tool_name": payload.get("name"),
                            **resolve_batch(payload),
                            "execution_mode": resolve_execution_mode(payload),
                            "start_ts": None,
                            "end_ts": None,
                        }
                    )
                    add_edge(last_node_by_turn.get(active_turn_id, turn_node_id), node_id, "sequence")
                    add_edge(turn_node_id, node_id, "contains")
                tool_node = node_map[node_id]
                resolved_batch = resolve_batch(payload)
                if resolved_batch["batch_id"] is not None:
                    tool_node["batch_id"] = resolved_batch["batch_id"]
                if resolved_batch["batch_size"] is not None:
                    tool_node["batch_size"] = resolved_batch["batch_size"]
                if resolved_batch["batch_index"] is not None:
                    tool_node["batch_index"] = resolved_batch["batch_index"]
                resolved_mode = resolve_execution_mode(payload)
                if resolved_mode is not None:
                    tool_node["execution_mode"] = resolved_mode
                tool_node["status"] = "error" if event.type == "tool_error" else "ok"
                tool_node["end_ts"] = tool_node.get("end_ts")
                last_node_by_turn[active_turn_id] = node_id
            elif event.type in {"retrieval", "retrieval_start", "retrieval_end", "retrieval_error", "memory", "memory_start", "memory_end", "memory_error", "memory_write", "memory_write_error", "compaction", "policy.decision", "extension_hook", "extension_error", "summary", "state", "state_update", "state_reset", "state_error"}:
                if event.type == "policy.decision":
                    decision = str(payload.get("decision", "allow"))
                    status = "blocked" if decision == "deny" else ("escalate" if decision == "escalate" else "ok")
                    node_type = "policy"
                elif event.type == "retrieval_error":
                    status = "error"
                    node_type = "retrieval"
                elif event.type == "memory_error":
                    status = "error"
                    node_type = "memory"
                elif event.type == "memory_write_error":
                    status = "error"
                    node_type = "memory"
                elif event.type == "extension_error":
                    status = "error"
                    node_type = "extension"
                elif event.type == "extension_hook":
                    status = "ok"
                    node_type = "extension"
                elif event.type in {"retrieval", "retrieval_start", "retrieval_end"}:
                    status = "ok"
                    node_type = "retrieval"
                elif event.type in {"memory", "memory_start", "memory_end"}:
                    status = "ok"
                    node_type = "memory"
                elif event.type == "memory_write":
                    status = "ok"
                    node_type = "memory"
                elif event.type == "summary":
                    status = "ok"
                    node_type = "summary"
                elif event.type == "state_error":
                    status = "error"
                    node_type = "state"
                elif event.type in {"state", "state_update", "state_reset"}:
                    status = "ok"
                    node_type = "state"
                else:
                    status = "ok"
                    node_type = event.type
                node_id = f"{event.type}:{index}"
                append_node(
                    {
                        "id": node_id,
                        "type": node_type,
                        "status": status,
                        "turn_id": active_turn_id,
                        "tool_name": None,
                        "start_ts": None,
                        "end_ts": None,
                    }
                )
                add_edge(last_node_by_turn.get(active_turn_id, turn_node_id), node_id, "sequence")
                add_edge(turn_node_id, node_id, "contains")
                last_node_by_turn[active_turn_id] = node_id
            elif event.type.startswith("checkpoint.") or event.type.startswith("eval."):
                node_id = f"{event.type}:{index}"
                append_node(
                    {
                        "id": node_id,
                        "type": event.type,
                        "status": "ok",
                        "turn_id": active_turn_id,
                        "tool_name": None,
                        "start_ts": None,
                        "end_ts": None,
                    }
                )
                add_edge(last_node_by_turn.get(active_turn_id, turn_node_id), node_id, "sequence")
                add_edge(turn_node_id, node_id, "contains")
                last_node_by_turn[active_turn_id] = node_id

        stats = {
            "turn_count": sum(1 for node in nodes if node["type"] == "turn"),
            "tool_count": sum(1 for node in nodes if node["type"] == "tool"),
            "error_count": sum(1 for node in nodes if node["status"] == "error"),
            "blocked_count": sum(1 for node in nodes if node["status"] == "blocked"),
            "tool_invocation_count": sum(1 for event in events if event.type == "tool.invocation_started"),
            "tool_invocation_reuse_count": sum(1 for event in events if event.type == "tool.invocation_reused"),
            "tool_invocation_blocked_count": sum(1 for event in events if event.type == "tool.invocation_blocked"),
            "recovery_attempt_count": sum(1 for event in events if event.type == "turn.recovery_started"),
            "duration_ms": None,
        }
        return {"nodes": nodes, "edges": edges, "stats": stats}

    def iter_graph_edges(self, events: list[StoredEvent]) -> list[dict[str, Any]]:
        return self.build_task_graph(events)["edges"]

    @staticmethod
    def _runtime_context_payload(
        turn_id: str | None = None,
        trace_id: str | None = None,
        event_id: str | None = None,
        parent_event_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "turn_id": turn_id,
            "trace_id": trace_id,
            "event_id": event_id,
            "parent_event_id": parent_event_id,
        }

    def create_checkpoint(
        self,
        session_path: Path,
        label: str | None = None,
        at_event_index: int | None = None,
        turn_id: str | None = None,
        trace_id: str | None = None,
        event_id: str | None = None,
        parent_event_id: str | None = None,
    ) -> dict[str, Any]:
        events = self.read_events(session_path)
        if not events:
            raise ValueError("session is empty")
        if at_event_index is None:
            event_index = len(events) - 1
        else:
            event_index = int(at_event_index)
            if event_index < 0 or event_index >= len(events):
                raise ValueError("at_event_index out of range")

        checkpoint = {
            "id": str(uuid.uuid4()),
            "label": label,
            "event_index": event_index,
            "event_type": events[event_index].type,
            **self._runtime_context_payload(
                turn_id=turn_id,
                trace_id=trace_id,
                event_id=event_id,
                parent_event_id=parent_event_id,
            ),
        }
        self.append(session_path, "checkpoint.created", checkpoint)
        return checkpoint

    def list_checkpoints(self, session_path: Path) -> list[dict[str, Any]]:
        checkpoints: list[dict[str, Any]] = []
        for event in self.read_events(session_path):
            if event.type == "checkpoint.created":
                checkpoints.append(event.payload)
        return checkpoints

    def replay_from_checkpoint(
        self,
        session_path: Path,
        checkpoint_id: str,
        until_event_index: int | None = None,
        fork: bool = False,
        turn_id: str | None = None,
        trace_id: str | None = None,
        event_id: str | None = None,
        parent_event_id: str | None = None,
    ) -> dict[str, Any]:
        events = self.read_events(session_path)
        checkpoints = self.list_checkpoints(session_path)
        checkpoint = next((item for item in checkpoints if str(item.get("id")) == checkpoint_id), None)
        if checkpoint is None:
            raise ValueError(f"checkpoint not found: {checkpoint_id}")

        start_index = int(checkpoint.get("event_index", 0))
        max_index = len(events) - 1
        if until_event_index is None:
            end_index = max_index
        else:
            end_index = int(until_event_index)
            if end_index < start_index or end_index > max_index:
                raise ValueError("until_event_index out of range")

        replay_started_payload = {
            "checkpoint_id": checkpoint_id,
            "start_event_index": start_index,
            "until_event_index": end_index,
            "fork": fork,
            **self._runtime_context_payload(
                turn_id=turn_id,
                trace_id=trace_id,
                event_id=event_id,
                parent_event_id=parent_event_id,
            ),
        }
        self.append(session_path, "checkpoint.replay_started", replay_started_payload)

        replayed_events = [
            {
                "index": index,
                "type": event.type,
                "payload": event.payload,
            }
            for index, event in enumerate(events)
            if start_index <= index <= end_index
        ]

        fork_session_path: Path | None = None
        if fork:
            header = self.read_header(session_path)
            replay_branch_label = f"replay-{checkpoint_id[:8]}"
            fork_session_path = self.create(parent_id=header.id, branch_label=replay_branch_label)
            self.append(
                fork_session_path,
                "branch",
                {
                    "from_session_id": header.id,
                    "branch_label": replay_branch_label,
                    "source_session": session_path.name,
                    "replay_checkpoint_id": checkpoint_id,
                },
            )
            for index, event in enumerate(events[1:], start=1):
                if index > end_index:
                    break
                self.append(fork_session_path, event.type, event.payload)

        result = {
            "checkpoint_id": checkpoint_id,
            "start_event_index": start_index,
            "until_event_index": end_index,
            "replayed_count": len(replayed_events),
            "events": replayed_events,
            "fork": fork,
            "fork_session": str(fork_session_path) if fork_session_path else None,
            "divergence_detected": False,
        }

        replay_finished_payload = {
            "checkpoint_id": checkpoint_id,
            "replayed_count": len(replayed_events),
            "fork": fork,
            "fork_session": result["fork_session"],
            "divergence_detected": False,
            **self._runtime_context_payload(
                turn_id=turn_id,
                trace_id=trace_id,
                event_id=event_id,
                parent_event_id=event_id,
            ),
        }
        self.append(session_path, "checkpoint.replay_finished", replay_finished_payload)
        return result
