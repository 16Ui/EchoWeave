# EchoWeave Packages

EchoWeave 采用小型 Python monorepo 结构。原先从 `ui-mono` 演进来的运行时代码被
收纳到 `echoweave_runtime`，面向产品和业务的能力通过更清晰的 EchoWeave 包边界暴露。

```text
echoweave_runtime
  底层执行基础设施，包含工具、模型客户端、RAG provider、session store、extension provider。
  只暴露 runtime governance 协议，不直接依赖 harness。

echoweave_ai
  多平台 AI provider 注册表和适配层，可通过 register_ai_provider() 随加随用。

echoweave_agent_core
  Agent 编排层，提供 AgentCore、TurnRequest、TurnResult、checkpoint/replay 等稳定 API。

echoweave_coding_agent
  本地 AI Coding Agent 应用层，组合 workspace、工具、session store、extension 和 AgentCore。

echoweave_harness
  策略、结构化审计、指标和反馈闭环，并通过 runtime bridge 接入 runtime governance。

echoweave_social
  社交平台接入实现，包含 adapter、webhook server、OneBot/NapCat、配置和 CLI。

echoweave_web
  Web 服务和管理面板，包含命令中心、配置 API、审批 API、SSE。
  顶层 `echoweave` CLI 入口由这里组合 social backend 和 Web server。
```

`src/echoweave` 是项目的主 facade namespace。
新代码应优先从真正拥有该行为的包导入，例如平台接入相关代码从 `echoweave_social`
导入，Agent 编排从 `echoweave_agent_core` 导入，底层 runtime 维护才直接导入
`echoweave_runtime`，harness 相关能力从 `echoweave_harness` 导入，Web 管理和 HTTP 服务相关
能力从 `echoweave_web` 导入。

## English Brief

EchoWeave is organized as a small Python monorepo. `echoweave_runtime` owns the
embedded runtime, while `echoweave_social`, `echoweave_harness`, and the facade
packages provide clearer product-facing boundaries.
