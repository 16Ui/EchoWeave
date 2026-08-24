from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from echoweave_agent_core.hooks import CoreTurnContext
from echoweave_agent_core.sessions import (
    SessionRuntimeFacade,
    list_session_items,
    resolve_session_path,
)
from echoweave_agent_core.types import AgentCoreConfig, TurnRequest, TurnResult
from echoweave_runtime.app import build_runtime
from echoweave_runtime.governance import record_runtime_audit
from echoweave_runtime.runtime.agent_session import AgentSessionRuntime
from echoweave_runtime.session.store import SessionStore


class AgentCore:
    """EchoWeave Agent 编排层。

    这一层向 Web/Social/Coding Agent 暴露稳定 API；底层工具执行、模型调用、
    RAG provider 和 session store 仍由 `echoweave_runtime` 承担。
    """

    def __init__(
        self,
        runtime: AgentSessionRuntime,
        session_store: SessionStore,
        *,
        hooks: tuple[Any, ...] = (),
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.runtime = runtime
        self.session_store = session_store
        self.hooks = hooks
        self.metadata = metadata or {}

    @classmethod
    def from_config(cls, config: AgentCoreConfig) -> "AgentCore":
        runtime = build_runtime(
            config.model_client,
            config.tool_registry,
            config.session_store,
            event_sink=config.event_sink,
            extensions=config.extensions,
            compact_keep_tail=config.compact_keep_tail,
            tool_execution_mode=config.tool_execution_mode,
            provider_capabilities=config.provider_capabilities,
            retrieval_enabled=config.retrieval_enabled,
        )
        return cls(runtime, config.session_store, hooks=config.hooks, metadata=config.metadata)

    def run_turn(self, request: TurnRequest) -> TurnResult:
        session_path = self._resolve_turn_session(request)
        snapshot = self.session_store.load_snapshot(session_path)
        session_id = snapshot.header.id
        metadata = {**self.metadata, **request.metadata}
        context = CoreTurnContext(session_path=session_path, session_id=session_id, metadata=metadata)
        request = self._run_before_turn_hooks(context, request)
        history = request.history if request.history is not None else snapshot.history
        summary = request.summary if request.summary is not None else snapshot.summary
        started = time.perf_counter()
        record_runtime_audit(
            "agent_core",
            "turn",
            status="start",
            session_id=session_id,
            workspace=metadata.get("workspace"),
            metadata={
                "prompt_chars": len(request.prompt),
                "resume": request.resume,
                **_audit_metadata(metadata),
            },
        )
        try:
            text, next_history, next_summary = self.runtime.run_turn(
                session_path,
                history,
                request.prompt,
                summary,
            )
        except Exception as exc:
            self._run_error_hooks(context, request, exc)
            record_runtime_audit(
                "agent_core",
                "turn",
                status="error",
                session_id=session_id,
                workspace=metadata.get("workspace"),
                latency_ms=(time.perf_counter() - started) * 1000,
                metadata={"reason": str(exc), **_audit_metadata(metadata)},
            )
            raise
        record_runtime_audit(
            "agent_core",
            "turn",
            status="ok",
            session_id=session_id,
            workspace=metadata.get("workspace"),
            latency_ms=(time.perf_counter() - started) * 1000,
            metadata={"reply_chars": len(text or ""), **_audit_metadata(metadata)},
        )
        result = TurnResult(
            text=text,
            session_path=session_path,
            session_id=session_id,
            history=next_history,
            summary=next_summary,
            metadata=metadata,
        )
        return self._run_after_turn_hooks(context, request, result)

    def resume(self) -> Path:
        return resolve_session_path(self.session_store, resume=True)

    def new_session(self) -> Path:
        return self.session_store.create()

    def switch_session(self, session: str | Path) -> Path:
        return self.session_store.resolve_session_path(session)

    def inspect(self, session_path: str | Path) -> tuple[Any, dict[str, Any]]:
        facade = SessionRuntimeFacade(self.session_store, Path(session_path))
        return facade.inspect()

    def list_sessions(self) -> list[dict[str, Any]]:
        return list_session_items(self.session_store)

    def task_graph(self, session_path: str | Path, by: str = "turn") -> dict[str, Any]:
        facade = SessionRuntimeFacade(self.session_store, Path(session_path))
        return facade.task_graph(by=by)

    def create_checkpoint(
        self,
        session_path: str | Path,
        *,
        label: str | None = None,
        at_event_index: int | None = None,
        runtime_context: dict[str, str | None] | None = None,
    ) -> dict[str, Any]:
        facade = SessionRuntimeFacade(self.session_store, Path(session_path))
        return facade.create_checkpoint(
            label=label,
            at_event_index=at_event_index,
            runtime_context=runtime_context,
        )

    def list_checkpoints(self, session_path: str | Path) -> list[dict[str, Any]]:
        facade = SessionRuntimeFacade(self.session_store, Path(session_path))
        return facade.list_checkpoints()

    def replay_from_checkpoint(
        self,
        session_path: str | Path,
        checkpoint_id: str,
        *,
        until_event_index: int | None = None,
        fork: bool = False,
        runtime_context: dict[str, str | None] | None = None,
    ) -> dict[str, Any]:
        facade = SessionRuntimeFacade(self.session_store, Path(session_path))
        return facade.replay_from_checkpoint(
            checkpoint_id,
            until_event_index=until_event_index,
            fork=fork,
            runtime_context=runtime_context,
        )

    def branch(self, session_path: str | Path, label: str) -> Path:
        facade = SessionRuntimeFacade(self.session_store, Path(session_path))
        return facade.branch(label)

    def _resolve_turn_session(self, request: TurnRequest) -> Path:
        if request.session_path is not None:
            return self.session_store.resolve_session_path(request.session_path)
        return resolve_session_path(self.session_store, resume=request.resume)

    def _run_before_turn_hooks(self, context: CoreTurnContext, request: TurnRequest) -> TurnRequest:
        current = request
        for hook in self.hooks:
            before_turn = getattr(hook, "before_turn", None)
            if not callable(before_turn):
                continue
            replacement = before_turn(context, current)
            if replacement is not None:
                current = replacement
        return current

    def _run_after_turn_hooks(
        self,
        context: CoreTurnContext,
        request: TurnRequest,
        result: TurnResult,
    ) -> TurnResult:
        current = result
        for hook in self.hooks:
            after_turn = getattr(hook, "after_turn", None)
            if not callable(after_turn):
                continue
            replacement = after_turn(context, request, current)
            if replacement is not None:
                current = replacement
        return current

    def _run_error_hooks(self, context: CoreTurnContext, request: TurnRequest, error: Exception) -> None:
        for hook in self.hooks:
            on_turn_error = getattr(hook, "on_turn_error", None)
            if callable(on_turn_error):
                on_turn_error(context, request, error)


def _audit_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "conversation_id",
        "actor_id",
        "provider",
        "model",
        "rag_enabled",
        "tool_execution_mode",
    }
    return {key: value for key, value in metadata.items() if key in allowed}
