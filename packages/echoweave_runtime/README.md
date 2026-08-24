# echoweave_runtime

`echoweave_runtime` 是 EchoWeave 内嵌的 Python Coding Agent 底层运行时包，负责执行基础设施。

它应该主要承载：

- 工具注册与执行。
- 文件/命令工具。
- 模型客户端适配。
- RAG provider。
- RAG provider registry，可通过 `register_retrieval_provider()` 注册新后端。
- session store。会话恢复、分支、checkpoint、replay 等编排 facade 已迁移到
  `echoweave_agent_core.sessions`。
- extension provider。
- 低层 event/schema。
- sandbox/path 基础能力。
- runtime governance 协议：`record_runtime_audit`、`evaluate_runtime_tool`、
  `evaluate_runtime_path`、`evaluate_runtime_command`。runtime 只调用这些协议，
  不直接依赖 `echoweave_harness`。

Agent 编排入口应优先放在 `echoweave_agent_core`，例如 `AgentCore`、`TurnRequest`、
`TurnResult`、`SessionRuntimeFacade`、checkpoint/replay 编排等。
应用级 CLI/TUI 入口已下沉到 `echoweave_coding_agent.cli`；`echoweave_runtime.cli` 只保留
旧导入兼容。
模型工厂支持 `anthropic`、`openai`、`openai-compatible`、`deepseek`、`openrouter`、
`siliconflow`、`ollama`。

上层 EchoWeave 包应尽量通过更明确的边界层访问运行时：

- `echoweave_ai`：模型/provider 能力。
- `echoweave_agent_core`：Agent loop 能力。
- `echoweave_coding_agent`：编码工具、会话、项目上下文能力。
- `echoweave_social`：社交平台接入能力。
- `echoweave_harness`：策略、审计、指标、反馈能力。

只有在做底层运行时集成或维护时，才建议直接 import `echoweave_runtime`。Web、Social、
Coding Agent 等上层入口应优先依赖 `echoweave_agent_core`。

## Harness 接入

`echoweave_harness` 通过 `echoweave_harness.runtime_bridge.install_runtime_bridge()` 把
audit recorder 和 policy evaluator 安装到 runtime governance。这样工具执行、路径访问、
命令策略、RAG 检索和 Agent turn 都能继续产出结构化审计，同时保持依赖方向为：

```text
echoweave_harness -> echoweave_runtime.governance
echoweave_runtime -/> echoweave_harness
```

## English Brief

`echoweave_runtime` embeds the Python coding-agent runtime inside EchoWeave. Prefer
the higher-level EchoWeave facade packages unless you are working on low-level
runtime integration.
