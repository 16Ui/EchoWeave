# echoweave_agent_core

`echoweave_agent_core` 是 EchoWeave 的 Agent 编排层。它对 Web、Social、Coding Agent
暴露稳定 API，把上层产品入口和底层运行时基础设施隔开。

## 职责

- `AgentCore`：统一 Agent 编排入口。
- `AgentCoreConfig`：组合模型、工具注册表、session store、扩展、RAG 开关等底层依赖。
- `TurnRequest` / `TurnResult`：稳定的一轮对话请求和返回结构。
- `run_turn`：负责 session 解析、history/summary 装载、调用底层 runtime、返回结构化结果。
- `resume` / `new_session` / `switch_session`：会话入口。
- `SessionRuntimeFacade`：会话编排 facade，承载 session resume、branch、import、
  checkpoint、replay、tree/task graph 等操作。
- `create_checkpoint` / `list_checkpoints` / `replay_from_checkpoint`：checkpoint 和 replay 编排入口。
- `list_sessions` / `inspect` / `task_graph`：会话观察和调试入口。
- runtime governance audit 挂点：记录 `agent_core/turn` 的 start/ok/error；
  当 harness bridge 启用时，这些事件会进入 harness audit log。
- hook 生命周期：`before_turn`、`after_turn`、`on_turn_error`，用于接入 prompt 策略、
  harness 策略、质量检查和自修复反馈。

## 与 runtime 的边界

`echoweave_agent_core` 不直接实现文件读写、命令执行、模型 SDK、RAG 数据库或工具细节。
这些属于 `echoweave_runtime`。

依赖方向应保持为：

```text
echoweave_web / echoweave_social / echoweave_coding_agent
  -> echoweave_agent_core
      -> echoweave_runtime
```

新上层代码应优先调用：

```python
from echoweave_agent_core import AgentCore, AgentCoreConfig, TurnRequest
```

旧的 `build_runtime` 和 `AgentSessionRuntime` 兼容导出仍保留，但只建议底层维护或迁移期使用。
旧的 `echoweave_runtime.runtime.session_runtime` 路径也保留为兼容 re-export；新代码应优先从
`echoweave_agent_core` 或 `echoweave_agent_core.sessions` 导入会话编排能力。

## English Brief

`echoweave_agent_core` is the orchestration layer. It exposes stable `AgentCore`,
`TurnRequest`, and `TurnResult` APIs while delegating low-level tools, models,
RAG providers, and session storage to `echoweave_runtime`.
