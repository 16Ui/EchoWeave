# echoweave_web

`echoweave_web` 是 EchoWeave 的独立 Web 包，负责 HTTP 服务、用户端 AI Coding 工作台、
管理端、命令中心和 SSE 事件流。

## 当前职责

- Webhook HTTP server。
- `/` 和 `/user` 用户端 AI Coding 工作台。
- `/admin` 管理端。
- `/api/status`、`/api/config`、`/api/approvals`。
- `/api/login`、`/api/logout`，浏览器端使用 Cookie session。
- `/api/command` 网页命令执行入口。
- `/events` SSE 事件流。
- 微信 URL 校验和 XML payload 解析。
- `echoweave_web.cli`：顶层 `echoweave` CLI，组合 social backend、平台 adapter
  和 Web server。

## 用户端

用户端用于作为独立于 QQ、飞书、微信等社交平台的 AI Coding 系统。它通过
`/api/command` 把用户输入转成 `EchoWeaveEvent`，继续走 Agent runtime、会话沙盒、
权限、审批、模型、RAG 和 skill 流程。

用户端支持：

- 聊天式编码任务输入。
- 快捷状态、模型、skill、RAG、审批命令。
- 实时 SSE 事件面板。
- 当前会话、模型和 RAG 状态提示。
- 工作区绑定和解绑回沙盒。
- RAG 开关和索引。
- 会话 skill 开关。

## 管理端

管理端负责整个项目的运维和配置：

- 审批列表和审批操作。
- AI providers JSON，用于网页端注册 OpenAI-compatible 平台。
- 模型 profiles。
- RAG、query rewrite、rerank。
- 全局 skill。
- 管理员和 sandbox root。
- harness audit 和 harness policy。

用户端和管理端默认不再使用 URL query token。浏览器先访问 `/login`，使用
`webhook_token` 登录后获得 HttpOnly Cookie session；Webhook 和脚本调用继续使用
`Authorization: Bearer <webhook_token>` 或 `X-EchoWeave-Token`。

`AI providers JSON` 示例：

```json
{
  "local-lmstudio": {
    "type": "openai-compatible",
    "base_url": "http://127.0.0.1:1234/v1",
    "api_key_env": "LOCAL_LLM_API_KEY",
    "default_model": "qwen2.5-coder:7b"
  }
}
```

保存后即可在 `Model profiles JSON` 中使用：

```json
{
  "local-lmstudio-qwen": {
    "provider": "local-lmstudio",
    "model": "qwen2.5-coder:7b",
    "label": "LM Studio 本地 Qwen",
    "description": "通过本机 LM Studio 的 OpenAI-compatible API 调用。"
  }
}
```

用户端模型选择使用下拉框，只展示 `model_profiles` 中已经配置好的模型。每个
profile 会显示 label、provider/model 和 API key 状态，避免把
`DEEPSEEK_API_KEY`、`OPENAI_API_KEY` 等凭据问题混成普通对话错误。

兼容入口 `echoweave_social.http_server`、`echoweave.http_server` 仍然保留
兼容导出，但新代码应优先从 `echoweave_web.server` 或 `echoweave_web.cli` 导入。`echoweave_social`
不再在主路径导入 Web server，旧的 `echoweave_social.http_server` 只保留懒加载兼容层。

## English Brief

`echoweave_web` owns the EchoWeave HTTP server, user AI-coding workspace, admin panel,
command API, and SSE stream. Legacy imports still work through compatibility
re-exports.
