from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from echoweave_runtime.extensions.manager import ExtensionManager
from echoweave_runtime.models.base import ModelClient
from echoweave_runtime.models.factory import ProviderCapabilities
from echoweave_runtime.session.store import SessionStore
from echoweave_runtime.tools_base import ToolRegistry
from echoweave_runtime.types import ToolExecutionMode


@dataclass(frozen=True)
class AgentCoreConfig:
    """Agent 编排层配置：把底层 runtime 依赖组合成稳定核心入口。"""

    model_client: ModelClient
    tool_registry: ToolRegistry
    session_store: SessionStore
    event_sink: Any | None = None
    extensions: ExtensionManager | None = None
    compact_keep_tail: int = 8
    tool_execution_mode: ToolExecutionMode = "sequential"
    provider_capabilities: ProviderCapabilities | None = None
    retrieval_enabled: bool = True
    hooks: tuple[Any, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TurnRequest:
    """一次 Agent turn 的稳定请求结构。"""

    prompt: str
    session_path: Path | None = None
    resume: bool = True
    history: list[dict[str, Any]] | None = None
    summary: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TurnResult:
    """一次 Agent turn 的稳定返回结构。"""

    text: str
    session_path: Path
    session_id: str
    history: list[dict[str, Any]]
    summary: str | None
    metadata: dict[str, Any] = field(default_factory=dict)
