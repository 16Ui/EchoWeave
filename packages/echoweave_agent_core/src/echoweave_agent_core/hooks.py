from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from echoweave_agent_core.types import TurnRequest, TurnResult


@dataclass(frozen=True)
class CoreTurnContext:
    """AgentCore turn 生命周期上下文。"""

    session_path: Path
    session_id: str
    metadata: dict[str, Any] = field(default_factory=dict)


class AgentCoreHook(Protocol):
    """AgentCore 编排扩展点。

    Hook 用于 harness、prompt 策略、质量检查、策略拦截等横切能力。
    返回 `None` 表示不修改请求/结果。
    """

    def before_turn(self, context: CoreTurnContext, request: TurnRequest) -> TurnRequest | None:
        return None

    def after_turn(self, context: CoreTurnContext, request: TurnRequest, result: TurnResult) -> TurnResult | None:
        return None

    def on_turn_error(self, context: CoreTurnContext, request: TurnRequest, error: Exception) -> None:
        return None


class AgentCoreHookBase:
    """便于实现局部 hook 的基类。"""

    def before_turn(self, context: CoreTurnContext, request: TurnRequest) -> TurnRequest | None:
        return None

    def after_turn(self, context: CoreTurnContext, request: TurnRequest, result: TurnResult) -> TurnResult | None:
        return None

    def on_turn_error(self, context: CoreTurnContext, request: TurnRequest, error: Exception) -> None:
        return None
