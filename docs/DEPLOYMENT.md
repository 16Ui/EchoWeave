# EchoWeave 部署文档

本文档默认使用中文。英文说明作为文末备选摘要。

## 1. 配置文件

复制本地配置：

```powershell
Copy-Item D:\games\EchoWeave\config.example.json D:\games\EchoWeave\config.local.json
```

编辑：

```text
D:\games\EchoWeave\config.local.json
```

关键字段：

```json
"host": "127.0.0.1",
"port": 8787,
"webhook_token": "change-me",
"web_allow_url_token": false,
"web_session_ttl_seconds": 28800,
"adapter": "onebot-v11",
"default_model_profile": "deepseek-chat",
"admins": ["your_qq_id"],
"admin_only_commands": ["approve", "approvals", "bind", "deny", "rag:index", "retry", "revoke", "skill:global"]
```

支持的平台 adapter：

- `onebot-v11` / `qq`
- `astrbot`
- `generic`
- `feishu` / `lark`
- `wechat-official` / `wechat` / `weixin`

支持的模型 provider：

- `demo`
- `deepseek`
- `openai`
- `anthropic`
- `ollama`
- `openrouter`
- `siliconflow`
- `openai-compatible`

本地 coding-agent CLI 的 provider 工厂同样支持这些 OpenAI-compatible provider。
管理端可通过 `AI providers JSON` 注册新的 OpenAI-compatible 平台，再通过
`model_profiles` 选择，不需要修改 Web/Social 入口：

```json
"ai_providers": {
  "local-lmstudio": {
    "type": "openai-compatible",
    "base_url": "http://127.0.0.1:1234/v1",
    "api_key_env": "LOCAL_LLM_API_KEY",
    "default_model": "qwen2.5-coder:7b"
  }
},
"model_profiles": {
  "local-lmstudio-qwen": {
    "provider": "local-lmstudio",
    "model": "qwen2.5-coder:7b",
    "label": "LM Studio 本地 Qwen"
  }
}
```

如果 provider 需要非 OpenAI-compatible 协议，可以在代码侧使用
`echoweave_ai.register_ai_provider()` 注册自定义 factory。
建议 profile 名使用 `deepseek-chat`、`openai-gpt-4.1-mini`、
`ollama-qwen-coder` 这种清晰命名。Web 用户端会以下拉框展示已配置 profile，
并提示需要的 API key 环境变量，例如 `DEEPSEEK_API_KEY` 或 `OPENAI_API_KEY`。

## 2. 启动服务

```powershell
D:\games\EchoWeave\scripts\start-echoweave.ps1
```

NapCat HTTP Client URL：

```text
http://127.0.0.1:8787/
```

Feishu/Lark callback URL：

```text
http://127.0.0.1:8787/
```

飞书事件订阅添加：

```text
im.message.receive_v1
```

微信公众号 callback URL：

```text
http://127.0.0.1:8787/
```

`wechat-official` adapter 支持明文 XML 文本回调和 `echostr` URL 校验。企业微信
加密回调后续需要额外解密层。
Webhook 推荐使用 `Authorization: Bearer <webhook_token>` 或 `X-EchoWeave-Token`。
URL query token 默认关闭，只保留给开发兼容场景，可通过 `web_allow_url_token: true`
临时打开。

## 3. 健康检查

```powershell
D:\games\EchoWeave\scripts\verify-deploy.ps1 -Config D:\games\EchoWeave\config.local.json
```

`verify-deploy.ps1` 会调用增强后的 `health-check.ps1`，检查配置文件、`/healthz`、
`/api/status`、用户端、管理端、sandbox root、模型 profile、RAG DSN，以及可选的
OneBot HTTP API。

期望返回包含：

```json
{
  "ok": true,
  "admin_panel": "http://127.0.0.1:8787/admin"
}
```

## 4. Web 用户端和管理端

用户端：

```text
http://127.0.0.1:8787/
```

用户端是独立于社交平台的 AI Coding 工作台，可以直接和本地 Agent 会话交互。它支持：

- 聊天式发送编码任务或 slash command。
- 查看状态、模型、skill、RAG、审批。
- 切换模型 profile。
- 开启/关闭 RAG，索引当前工作区。
- 启用/关闭会话 skill。
- 绑定真实工作区或解绑回会话沙盒。
- 在同一套权限和审批机制下执行本地代码编写、文件操作和命令请求。
- 查看实时 SSE 事件流，辅助观察消息、回复、错误和 heartbeat。

管理端：

打开：

```text
http://127.0.0.1:8787/admin
```

管理端可处理：

- 查看 pending approvals。
- approve / deny / revoke / retry 审批。
- 注册 AI providers。
- 配置模型 profiles。
- 配置 RAG、query rewrite、rerank。
- 配置全局 skill、管理员、sandbox root。
- 配置 harness audit 和 harness policy。

当 EchoWeave 使用 `--config` 启动时，管理面板修改会写回 JSON 配置文件。
浏览器端使用登录页和 HttpOnly Cookie session，默认过期时间由
`web_session_ttl_seconds` 控制。`webhook_token` 不再出现在用户端、管理端和 SSE URL 中。

## 5. RAG pgvector 初始化

安装可选依赖：

```powershell
python -m pip install -e "D:\games\EchoWeave[rag-pgvector]"
```

确保 PostgreSQL 用户可以创建扩展，然后设置：

```json
"rag_pgvector_dsn": "postgresql://echoweave:password@127.0.0.1:5432/echoweave"
```

初始化 schema：

```powershell
D:\games\EchoWeave\scripts\init-rag-db.ps1
```

聊天中启用并索引：

```text
/rag on
/rag index
```

可选 RAG 插槽：

```json
"rag_query_rewrite_enabled": true,
"rag_query_rewrite_strategy": "local_multi_query",
"rag_query_rewrite_max_queries": 3,
"rag_rerank_enabled": true,
"rag_rerank_strategy": "bm25",
"rag_rerank_candidate_multiplier": 4
```

内置 query rewriter 和 reranker 是轻量本地实现。后续可以替换为 LLM query
expansion、BGE reranker、cross-encoder 或远程 rerank 服务。
RAG provider 已通过 registry 管理，新增后端可以调用
`echoweave_runtime.extensions.manager.register_retrieval_provider()` 注册。

## 6. 审批流程

当命令需要审批时，EchoWeave 会返回 approval id：

```text
/approvals
/approve <id>
/deny <id>
/revoke <id>
/retry <id>
```

pending approval 会在 `approval_timeout_seconds` 后过期。

## 7. Harness 策略与审计

EchoWeave 会为审批、命令、文件访问、模型调用、RAG 检索、访问控制和消息流写入结构化
audit log。

推荐生产配置：

```json
"harness_audit_enabled": true,
"harness_audit_path": "D:/games/EchoWeave/logs/audit.jsonl",
"harness_policy": {
  "denied_tools": [],
  "allowed_paths": [],
  "denied_paths": ["**/.git/**", "**/.env", "**/secrets/**"],
  "command_deny_patterns": ["(?i)\\bformat\\b", "(?i)\\bshutdown\\b"],
  "command_approval_patterns": ["(?i)\\bgit\\s+push\\b", "(?i)\\bpip\\s+install\\b"],
  "session_model_allowlist": ["demo-echo", "deepseek-chat", "openai-gpt-4.1-mini"],
  "session_skill_allowlist": [],
  "session_rag_enabled": null
}
```

生成 harness 报表：

```powershell
D:\games\EchoWeave\scripts\harness-report.ps1
```

报表会输出：

- 回答质量
- 工具调用正确率
- 审批命中率
- 审批处理率
- RAG 命中率
- 沙盒逃逸拦截率
- 策略拦截率
- 模型调用成功率
- 按类别/状态聚合的 audit event 统计
- hardening 建议

默认建议会追加到：

```text
D:\games\EchoWeave\logs\harness-feedback.jsonl
```

每条建议会携带 `metric`、`evidence` 和 `action`，后续可由自动化流程转换为测试、
策略 DSL 或文档补丁。

## 8. Docker Compose

复制 Docker 配置：

```powershell
Copy-Item D:\games\EchoWeave\config.docker.example.json D:\games\EchoWeave\config.docker.json
```

启动：

```powershell
docker compose up --build
```

初始化容器内 RAG schema：

```powershell
docker compose exec echoweave python -m echoweave_runtime.rag.init_db --dsn postgresql://echoweave:echoweave-password@postgres:5432/echoweave
```

打开：

```text
http://127.0.0.1:8787/admin
```

## 9. 上线前检查

上线或暴露到局域网/公网前，至少检查：

- `webhook_token` 已替换为强随机 token。
- `web_allow_url_token` 保持 `false`，除非只是在本机开发调试。
- 普通会话保持 sandbox 模式，只有明确需要时才 `/bind` 真实仓库。
- `/status` 显示普通聊天的 `workspace_mode: sandbox`。
- `admins`、`allowed_users`、`allowed_groups` 已按需要收紧。
- 已运行 `python -m pytest`。
- 已运行 `scripts\verify-deploy.ps1`。
- 已运行 `scripts\harness-report.ps1` 并检查低指标和重复失败建议。
- 已轮换任何出现在聊天、日志或配置中的 API key。
- 管理面板前方有 HTTPS、访问白名单或反向代理鉴权。

## English Brief

This deployment guide is written in Chinese by default. In short: copy
`config.example.json` to `config.local.json`, set a strong token, choose an
adapter such as `onebot-v11`, start `scripts/start-echoweave.ps1`, check
`/healthz`, open the admin panel, initialize pgvector if RAG is needed, and run
the harness report before production use.
