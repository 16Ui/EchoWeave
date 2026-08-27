"""EchoWeave Agent 编排层。

`echoweave_agent_core` 对 Web、Social、Coding Agent 暴露稳定 Agent API；
底层工具、模型、RAG、session store 仍由 `echoweave_runtime` 承担。
"""

from echoweave_agent_core.core import AgentCore
from echoweave_agent_core.hooks import AgentCoreHook, AgentCoreHookBase, CoreTurnContext
from echoweave_agent_core.outcomes import (
    InvalidTurnTransition,
    TurnExecutionError,
    TurnFailure,
    TurnFailureKind,
    TurnOutcome,
    TurnRecoveryConflictError,
    TurnState,
    TurnStateMachine,
)
from echoweave_agent_core.recovery import (
    OrphanRecoveryConfig,
    OrphanRecoveryScheduler,
    OrphanScanIssue,
    OrphanScanReport,
    OrphanTurnCandidate,
    RecoveryDispatchResult,
    RecoverySchedulerSnapshot,
)
from echoweave_agent_core.sessions import (
    SessionRuntimeFacade,
    empty_tool_execution_stats,
    list_session_items,
    resolve_session_path,
    summarize_tool_execution,
)
from echoweave_agent_core.types import (
    AgentCoreConfig,
    RecoverTurnRequest,
    ResolveToolInvocationRequest,
    TurnRequest,
    TurnResult,
)
from echoweave_runtime.app import build_runtime  # 兼容旧导入
from echoweave_runtime.runtime.agent_session import AgentSessionRuntime  # 兼容旧导入

__all__ = [
    "AgentCore",
    "AgentCoreConfig",
    "AgentCoreHook",
    "AgentCoreHookBase",
    "AgentSessionRuntime",
    "CoreTurnContext",
    "SessionRuntimeFacade",
    "RecoverTurnRequest",
    "ResolveToolInvocationRequest",
    "InvalidTurnTransition",
    "OrphanRecoveryConfig",
    "OrphanRecoveryScheduler",
    "OrphanScanIssue",
    "OrphanScanReport",
    "OrphanTurnCandidate",
    "RecoveryDispatchResult",
    "RecoverySchedulerSnapshot",
    "TurnExecutionError",
    "TurnFailure",
    "TurnFailureKind",
    "TurnOutcome",
    "TurnRecoveryConflictError",
    "TurnRequest",
    "TurnResult",
    "TurnState",
    "TurnStateMachine",
    "build_runtime",
    "empty_tool_execution_stats",
    "list_session_items",
    "resolve_session_path",
    "summarize_tool_execution",
]
