from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from echoweave_runtime.extensions.manager import ExtensionManager, build_extension_manager
from echoweave_runtime.runtime.agent_session import AgentSessionRuntime
from echoweave_runtime.runtime.observer import JsonLineRuntimeObserver, NullRuntimeObserver, RuntimeEventDispatcher
from echoweave_runtime.sandbox import DockerSandboxProfile
from echoweave_runtime.tools.agent import AgentTool
from echoweave_runtime.tools.bash import BashTool
from echoweave_runtime.tools.edit import EditTool
from echoweave_runtime.tools.find import FindTool
from echoweave_runtime.tools.grep import GrepTool
from echoweave_runtime.tools.ls import LsTool
from echoweave_runtime.tools.mcp_call import McpCallTool
from echoweave_runtime.tools.patch import PatchTool
from echoweave_runtime.tools.policy import ShellCommandPolicy, default_shell_command_policy
from echoweave_runtime.tools.read import ReadTool
from echoweave_runtime.tools.skill_call import SkillCallTool
from echoweave_runtime.tools.todo import TodoTool
from echoweave_runtime.tools.tool_search import ToolSearchTool
from echoweave_runtime.tools.write import WriteTool
from echoweave_runtime.tools.workers import WorkersTool
from echoweave_runtime.tools_base import ToolConflictPolicy, ToolRegistry
from echoweave_runtime.models.base import ModelClient
from echoweave_runtime.models.factory import ProviderCapabilities
from echoweave_runtime.session.store import SessionStore
from echoweave_runtime.types import ToolExecutionMode


def build_registry(
    cwd: Path,
    approval_callback: Callable[[str, str], bool] | None = None,
    extensions: ExtensionManager | None = None,
    conflict_policy: ToolConflictPolicy = "builtin_wins",
) -> ToolRegistry:
    """构建工具注册表：统一注入本地工具、Skill 调用与 MCP 调用能力。"""
    registry = ToolRegistry(conflict_policy=conflict_policy)
    extension_manager = extensions or build_extension_manager(cwd)

    registry.register(ReadTool(cwd), source="builtin")
    registry.register(WriteTool(cwd), source="builtin")
    registry.register(EditTool(cwd), source="builtin")
    shell_policy = ShellCommandPolicy(auto_approve=False) if approval_callback else default_shell_command_policy
    registry.register(
        BashTool(
            cwd,
            policy=shell_policy,
            approval_callback=approval_callback,
            container_sandbox=_docker_sandbox_from_env(),
        ),
        source="builtin",
    )
    registry.register(LsTool(cwd), source="builtin")
    registry.register(FindTool(cwd), source="builtin")
    registry.register(GrepTool(cwd), source="builtin")
    registry.register(SkillCallTool(cwd, extension_manager), source="builtin")
    registry.register(McpCallTool(cwd, extension_manager), source="builtin")
    registry.register(AgentTool(cwd), source="builtin")
    registry.register(WorkersTool(cwd), source="builtin")
    registry.register(PatchTool(cwd), source="builtin")
    registry.register(TodoTool(cwd), source="builtin")

    for spec in extension_manager.list_tools():
        registry.register(
            spec.tool,
            source=spec.source,
            conflict_policy=spec.conflict_policy,
        )
    registry.register(ToolSearchTool(registry), source="builtin")
    return registry


def _docker_sandbox_from_env() -> DockerSandboxProfile:
    mode = (os.environ.get("ECHOWEAVE_SANDBOX_MODE") or "").strip().lower()
    enabled = mode in {"docker", "container"}
    defaults = DockerSandboxProfile()
    return DockerSandboxProfile(
        enabled=enabled,
        image=os.environ.get("ECHOWEAVE_SANDBOX_IMAGE") or defaults.image,
        network=os.environ.get("ECHOWEAVE_SANDBOX_NETWORK") or defaults.network,
        memory=os.environ.get("ECHOWEAVE_SANDBOX_MEMORY") or defaults.memory,
        cpus=os.environ.get("ECHOWEAVE_SANDBOX_CPUS") or defaults.cpus,
        read_only_rootfs=(os.environ.get("ECHOWEAVE_SANDBOX_READ_ONLY_ROOTFS") or "true").lower() not in {"0", "false", "no"},
    )


def build_runtime(
    model_client: ModelClient,
    tool_registry: ToolRegistry,
    session_store: SessionStore,
    event_sink=None,
    extensions: ExtensionManager | None = None,
    compact_keep_tail: int = 8,
    tool_execution_mode: ToolExecutionMode = "sequential",
    provider_capabilities: ProviderCapabilities | None = None,
    retrieval_enabled: bool = True,
) -> AgentSessionRuntime:
    """组装运行时：把模型、工具、会话存储和事件分发器接成可执行主链。"""
    observer = JsonLineRuntimeObserver(event_sink) if event_sink is not None else NullRuntimeObserver()
    dispatcher = RuntimeEventDispatcher(observer)
    return AgentSessionRuntime(
        model_client,
        tool_registry,
        session_store,
        dispatcher=dispatcher,
        extensions=extensions,
        compact_keep_tail=compact_keep_tail,
        tool_execution_mode=tool_execution_mode,
        provider_capabilities=provider_capabilities,
        retrieval_enabled=retrieval_enabled,
    )
