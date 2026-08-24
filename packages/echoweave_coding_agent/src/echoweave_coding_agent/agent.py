from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from echoweave_agent_core import (
    AgentCore,
    AgentCoreConfig,
    RecoverTurnRequest,
    TurnOutcome,
    TurnRequest,
    TurnResult,
)
from echoweave_runtime.app import build_registry
from echoweave_runtime.extensions.manager import ExtensionManager, build_extension_manager
from echoweave_runtime.models.base import ModelClient
from echoweave_runtime.models.factory import ProviderCapabilities
from echoweave_runtime.session.store import SessionStore
from echoweave_runtime.types import ToolExecutionMode


@dataclass(frozen=True)
class CodingAgentConfig:
    """本地 AI Coding Agent 应用层配置。"""

    workspace: Path
    model_client: ModelClient
    session_store: SessionStore | None = None
    extensions: ExtensionManager | None = None
    approval_callback: Callable[..., bool] | None = None
    compact_keep_tail: int = 8
    tool_execution_mode: ToolExecutionMode = "sequential"
    provider_capabilities: ProviderCapabilities | None = None
    retrieval_enabled: bool = True
    metadata: dict[str, Any] | None = None


class CodingAgent:
    """面向本地代码工作的应用层 Agent。

    这一层负责把 workspace、工具注册、session store 和 AgentCore 组装起来；
    具体工具执行、模型客户端、RAG provider 仍由 runtime 提供。
    """

    def __init__(self, core: AgentCore) -> None:
        self.core = core

    @classmethod
    def from_config(cls, config: CodingAgentConfig) -> "CodingAgent":
        workspace = config.workspace.expanduser().resolve()
        extensions = config.extensions or build_extension_manager(workspace)
        session_store = config.session_store or SessionStore(workspace / "echoweave-data" / "sessions")
        registry = build_registry(
            workspace,
            approval_callback=config.approval_callback,
            extensions=extensions,
        )
        core = AgentCore.from_config(
            AgentCoreConfig(
                model_client=config.model_client,
                tool_registry=registry,
                session_store=session_store,
                extensions=extensions,
                compact_keep_tail=config.compact_keep_tail,
                tool_execution_mode=config.tool_execution_mode,
                provider_capabilities=config.provider_capabilities,
                retrieval_enabled=config.retrieval_enabled,
                metadata={"workspace": str(workspace), **(config.metadata or {})},
            )
        )
        return cls(core)

    def run(
        self,
        prompt: str,
        *,
        session_path: str | Path | None = None,
        resume: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> TurnResult:
        return self.execute(
            prompt,
            session_path=session_path,
            resume=resume,
            metadata=metadata,
        ).require_result()

    def execute(
        self,
        prompt: str,
        *,
        session_path: str | Path | None = None,
        resume: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> TurnOutcome:
        return self.core.execute_turn(
            TurnRequest(
                prompt=prompt,
                session_path=Path(session_path) if session_path is not None else None,
                resume=resume,
                metadata=metadata or {},
            )
        )

    def list_sessions(self) -> list[dict[str, Any]]:
        return self.core.list_sessions()

    def create_checkpoint(self, session_path: str | Path, label: str | None = None) -> dict[str, Any]:
        return self.core.create_checkpoint(session_path, label=label)

    def recover(
        self,
        session_path: str | Path,
        checkpoint_id: str,
        *,
        allow_incomplete: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> TurnOutcome:
        return self.core.recover_turn(
            RecoverTurnRequest(
                session_path=Path(session_path),
                checkpoint_id=checkpoint_id,
                allow_incomplete=allow_incomplete,
                metadata=metadata or {},
            )
        )

    def replay_from_checkpoint(self, session_path: str | Path, checkpoint_id: str) -> dict[str, Any]:
        return self.core.replay_from_checkpoint(session_path, checkpoint_id)
