# EchoWeave

EchoWeave 是一个自研、模型无关、可扩展的个人 AI Agent Runtime。它把消息渠道、
模型 Provider、Agent Loop、工具、知识检索、权限与可观测性组织在统一运行时中；
Coding Agent 是其中技术深度最高的内置能力，但不是项目的全部定位。

项目吸收早期个人 Coding Agent 的运行时积累，并参考成熟机器人框架的多平台适配与
插件化思路。目标不是复制现有机器人，也不是堆叠聊天界面，而是建立一个能够解释、
验证并持续演进的个人 Agent 底座。

默认文档语言为中文；英文说明作为备选放在文档末尾或独立英文摘要中。

## 项目定位

EchoWeave 的核心目标：

- 把每个社交会话路由到独立的 Agent 会话和沙盒环境。
- 通过统一事件协议解耦 Web、CLI、QQ、飞书和微信等消息入口。
- 通过显式生命周期和能力声明承载插件、Skill 与工具扩展。
- 支持多模型接入，包含 OpenAI、DeepSeek、Anthropic、Ollama 和 OpenAI-compatible 服务。
- 支持可选 RAG，默认方案为 pgvector + BGE-M3 + 混合检索。
- 支持全局 skill 和会话 skill 的勾选式启用。
- 支持审批、审计、策略 DSL、指标报表和 harness 反馈闭环。
- 将 Coding Workspace 作为内置 Agent 能力，而不是把整个项目限制成 Coding Agent。
- 支持作为个人助手、技术作品和长期运行的本地服务继续演进。

## 项目结构

```text
EchoWeave
  packages/echoweave_runtime        底层执行基础设施、工具、模型客户端、RAG registry、session store
  packages/echoweave_ai             多平台 AI provider 注册表和适配层
  packages/echoweave_agent_core     Agent 编排层、Turn Outcome 状态机、checkpoint/replay
  packages/echoweave_coding_agent   本地 AI Coding Agent 应用层、coding CLI/TUI
  packages/echoweave_harness        策略、审计、指标、反馈闭环
  packages/echoweave_social         社交平台 backend、适配器、OneBot/NapCat、配置、社交侧 CLI
  packages/echoweave_web            Web 服务、Webhook server、管理面板、命令中心、SSE、顶层 CLI
  src/echoweave                     项目主 facade 命名空间
  docs                           部署和运维文档
  scripts                        启动、健康检查、RAG 初始化、harness 报表脚本
```

消息链路：

```text
社交平台 Webhook
  -> 平台适配器，例如 generic / AstrBot / OneBot v11 / Feishu / WeChat
  -> InboundMessage
  -> EchoWeaveBackend
  -> 内嵌 EchoWeave Agent Runtime
  -> OutboundMessage
  -> 平台响应或主动发送
```

## 当前能力

- Generic Webhook：用于本地 smoke test 或自定义平台接入。
- AstrBot-shaped adapter：兼容 AstrBot 风格事件字段。
- OneBot v11 adapter：适配 QQ/NapCat/Lagrange 等 OneBot v11 HTTP 事件。
- OneBot HTTP client：可主动调用 `send_private_msg` / `send_group_msg`。
- Feishu/Lark adapter：支持飞书事件回调和 `im.message.receive_v1`。
- WeChat Official Account adapter：支持微信公众号明文 XML 回调和 `echostr` 校验。
- Web 管理面板：配置模型、RAG、skill、管理员、沙盒、审批、harness policy。
- SSE：提供运行事件流，便于仪表盘或调试客户端订阅。
- 统一事件协议：消息、附件和 Runtime 事件共享版本化契约，事件 ID 与 SSE 续传游标相互独立。
- Runtime Host：统一管理运行组件的启动、逆序关闭和启动失败回滚；Web Gateway 已完成接入。
- AstrBot 兼容入口：安全分析插件 Manifest、配置、Skills 和 API 使用，未审查代码不会在扫描阶段执行。
- AstrBot 基础命令桥：通过显式授权的独立 Worker 运行兼容插件，并提供生命周期、请求关联和超时终止。
- 插件执行策略：敏感能力需声明并授权，Worker 使用隔离模式、环境白名单和有界 JSON 协议。
- 可恢复 Turn 协议：结构化 Outcome、显式状态机、失败分类、起始 checkpoint 与跨层 trace 关联。
- Tool Invocation 账本：稳定调用键、参数冲突检测、结果复用和基于副作用等级的中断重放保护。
- 受控 Turn 恢复：从 checkpoint 重建可见历史，以同一逻辑 Turn 的新 attempt 继续，并在危险副作用不确定时暂停。
- 人工恢复决策：支持补录工具结果、一次性授权重试或放弃 Turn，并保留操作者与原因审计。
- Provider 可靠性层：单请求指数退避、Turn 级共享重试预算、`Retry-After`、流式安全边界和进程内熔断状态机。
- 并行批次恢复：稳定批次身份、成员指纹校验、部分完成摘要，以及 safe retry / durable reuse / indeterminate suspend 分流。
- 执行所有权：跨进程可过期 Lease、后台 heartbeat、单调 fencing token 与 stale owner 拦截，支持重启后的安全接管。
- 并发基础设施：只读工具线程池、Session JSONL 的进程内/跨进程双层锁、按 Store 协调器单例和进程级心跳调度单例。
- 孤儿恢复调度：只读扫描过期 Lease，以固定线程池和 attempt 上限自动接管，并在持有 Lease 后重新校验状态。
- Docker Compose：包含 EchoWeave + PostgreSQL + pgvector 的部署形态。

可恢复执行的当前边界和后续幂等设计见
[docs/RECOVERABLE_AGENT_RUNTIME.md](docs/RECOVERABLE_AGENT_RUNTIME.md)，Provider 故障处理契约见
[docs/PROVIDER_RELIABILITY.md](docs/PROVIDER_RELIABILITY.md)，并行批次恢复见
[docs/PARALLEL_BATCH_RECOVERY.md](docs/PARALLEL_BATCH_RECOVERY.md)，并发与接管设计见
[docs/CONCURRENCY_AND_TAKEOVER.md](docs/CONCURRENCY_AND_TAKEOVER.md)，孤儿扫描与调度见
[docs/ORPHAN_RECOVERY.md](docs/ORPHAN_RECOVERY.md)。

## 快速启动

从 `D:\games\EchoWeave` 运行：

```powershell
$env:PYTHONPATH="D:\games\EchoWeave\src;D:\games\EchoWeave\packages\echoweave_runtime\src;D:\games\EchoWeave\packages\echoweave_ai\src;D:\games\EchoWeave\packages\echoweave_agent_core\src;D:\games\EchoWeave\packages\echoweave_coding_agent\src;D:\games\EchoWeave\packages\echoweave_harness\src;D:\games\EchoWeave\packages\echoweave_social\src;D:\games\EchoWeave\packages\echoweave_web\src"
python -m echoweave.cli once --cwd D:\games\EchoWeave --text "hello from EchoWeave" --json
```

本地 coding-agent 的原 runtime CLI 已迁移到 `echoweave_coding_agent.cli`，console script
为 `echoweave-coding`。

## NapCat / OneBot v11 部署

1. 复制配置文件：

```powershell
Copy-Item D:\games\EchoWeave\config.example.json D:\games\EchoWeave\config.local.json
```

2. 修改 `config.local.json`：

```json
{
  "adapter": "onebot-v11",
  "host": "127.0.0.1",
  "port": 8787,
  "webhook_token": "换成一个足够长的随机 token",
  "web_allow_url_token": false,
  "web_session_ttl_seconds": 28800,
  "default_model_profile": "deepseek-chat"
}
```

3. 启动 EchoWeave：

```powershell
D:\games\EchoWeave\scripts\start-echoweave.ps1
```

4. 在 NapCat 的 HTTP Client 中填写：

```text
http://127.0.0.1:8787/
```

如果你同时启用了 NapCat HTTP Server，可以把 `onebot_api_url` 配成例如
`http://127.0.0.1:3000`，这样 EchoWeave 会主动调用 OneBot API 发送消息。只用
HTTP Client 也可以工作，EchoWeave 会通过 HTTP 响应返回 OneBot quick operation。
如果平台支持自定义请求头，推荐使用 `Authorization: Bearer <webhook_token>`。URL
query token 默认关闭，只保留给开发兼容场景，可通过 `web_allow_url_token: true` 临时打开。

## 模型配置

模型通过 `model_profiles` 管理。建议把 profile 名写成“平台 + 模型 + 用途”，
不要只写 `default`、`openai` 这类容易混淆的名字。内置 provider 可直接写入
profile：

```json
"model_profiles": {
  "demo-echo": {
    "provider": "demo",
    "model": null,
    "label": "Demo / 本地 Echo",
    "description": "不调用外部模型，用于确认链路是否打通。"
  },
  "deepseek-chat": {
    "provider": "deepseek",
    "model": "deepseek-chat",
    "label": "DeepSeek Chat",
    "description": "需要 DEEPSEEK_API_KEY。"
  },
  "openai-gpt-4.1-mini": {
    "provider": "openai",
    "model": "gpt-4.1-mini",
    "label": "OpenAI GPT-4.1 mini",
    "description": "需要 OPENAI_API_KEY。"
  },
  "anthropic-sonnet": {
    "provider": "anthropic",
    "model": "claude-3-5-sonnet-latest",
    "label": "Anthropic Claude Sonnet",
    "description": "需要 ANTHROPIC_API_KEY。"
  },
  "ollama-qwen-coder": {
    "provider": "ollama",
    "model": "qwen2.5-coder:7b",
    "label": "Ollama 本地 Qwen Coder"
  },
  "custom-openai-compatible": {
    "provider": "openai-compatible",
    "model": "your-model",
    "base_url": "https://example.com/v1",
    "api_key_env": "YOUR_API_KEY",
    "label": "自定义 OpenAI-compatible"
  }
}
```

设置对应环境变量，例如 `DEEPSEEK_API_KEY`、`OPENAI_API_KEY`、
`ANTHROPIC_API_KEY` 或自定义 `api_key_env`。管理端和用户端会在模型下拉框里
显示 profile 的 label、provider/model 以及 API key 是否已配置；如果缺少 key，
真实 LLM 会返回中文错误提示，不再直接暴露 OpenAI SDK 的低层报错。
本地 coding CLI 的模型工厂也支持 `anthropic`、`openai`、`openai-compatible`、
`deepseek`、`openrouter`、`siliconflow`、`ollama`。

管理端也可以通过 `AI providers JSON` 注册声明式 OpenAI-compatible 平台，再在
`Model profiles JSON` 中选择它：

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

这类网页端注册适合 DeepSeek 兼容网关、OpenRouter、SiliconFlow、本地 Ollama、
LM Studio、vLLM 或公司内部 OpenAI-compatible 服务。更复杂的自定义 provider 可以在
Python 代码中通过 `echoweave_ai.register_ai_provider()` 注册，注册后同样在
`model_profiles` 中直接选择。

聊天中可使用：

```text
/models
/model <profile>
```

## 沙盒与权限

默认情况下，每个私聊或群聊会话都有独立沙盒。普通会话不会直接扫描
`D:\develop\agent` 或其他真实项目目录。

常用命令：

```text
/status
/bind <workspace>
/unbind
```

`/bind` 只应在你明确希望某个会话操作真实仓库时使用；`/unbind` 会回到该会话的
独立沙盒。

访问控制配置：

```json
"admins": ["your_user_id"],
"allowed_users": [],
"allowed_groups": [],
"blocked_users": [],
"require_mention_in_group": false,
"bot_ids": [],
"admin_only_commands": ["approve", "approvals", "bind", "deny", "rag:index", "retry", "revoke", "skill:global"],
"approval_timeout_seconds": 3600
```

空白 allowlist 表示开发期友好默认：允许任何发送者使用。配置 `allowed_users` 或
`allowed_groups` 后，仅允许列表中的用户/群使用。`blocked_users` 总是优先生效。

## 审批流程

当命令需要审批时，EchoWeave 会保存 pending approval 并返回审批 id。管理员可以通过
聊天或 Web 面板处理：

```text
/approvals
/approve <id>
/deny <id>
/revoke <id>
/retry <id>
```

Web 用户端：

```text
http://127.0.0.1:8787/
```

Web 管理端：

```text
http://127.0.0.1:8787/admin
```

`echoweave_web` 现在分为用户端和管理端：

- 用户端是独立于社交平台的 AI Coding 工作台，类似早期 `ui-mono` 的 Web 入口。
- 管理端负责项目级配置、审批处理、模型/RAG/harness/skill/管理员配置。
- 浏览器访问用户端/管理端会先进入登录页，登录成功后使用 HttpOnly Cookie 中的 JWT。
- 第一次进入登录页时使用“注册/初始化”创建首个账号；首个账号默认为 admin，用户数据持久化到 `echoweave-users.json`。
- `webhook_token` 只用于 webhook 的服务端校验，也保留“Webhook 访问密码登录”的开发兼容入口，不再展示在页面 URL 中。

用户端内置常用命令按钮。用户不需要记住大部分 slash command，可以直接在网页中操作：

- 查看 `/status`、`/models`、`/skills`、`/rag`、`/approvals`。
- 切换模型 profile。
- 开启/关闭 RAG，索引当前工作区。
- 启用/关闭会话 skill 或全局 skill。
- 绑定真实工作区或解绑回独立沙盒。
- 发送自定义命令或普通消息到指定会话。
- 查看实时 SSE 事件流，包括消息、回复、错误和 heartbeat。

用户端发送的请求会走同一套 Agent runtime、沙盒、权限、审批、RAG、skill 和模型逻辑，
不会绕过后端直接执行命令。

要让某个会话实际访问当前主机上的真实文件夹：

1. 进入管理端，把 `web-admin` 加入“管理员”，然后保存配置。
2. 回到用户端，保持“用户 ID”为 `web-admin`。
3. 在“工作区 -> 本地路径”填写真实目录，例如 `D:\games\EchoWeave`。
4. 点击“绑定路径”。之后该会话的文件访问和命令工作目录会指向这个真实目录。
5. 点击“回到沙盒”或发送 `/unbind` 可恢复到每个会话独立沙盒。

真实工作区绑定仍会经过权限、审批、harness policy 和路径策略检查；不想让网页端绑定真实目录时，不要把 `web-admin` 加入管理员。

## Skill

Skill 采用列表勾选式启用模型，分为全局 skill 和会话 skill。

```text
/skills
/skill on <name>
/skill off <name>
/skill global on <name>
/skill global off <name>
```

`/skills` 会显示 `[x]` / `[ ]` 状态，并用 `G` 表示全局启用、`S` 表示会话级启用。

## RAG

RAG 默认关闭，可按会话开启：

```text
/rag
/rag on
/rag off
/rag index
```

默认 RAG 后端为 `pgvector_hybrid_bgem3`：

- 向量模型：BGE-M3，默认 `BAAI/bge-m3`。
- 数据库：PostgreSQL + `pgvector`。
- 检索：余弦距离向量检索 + BM25 混合搜索。
- Query rewrite：可选插槽，默认 `local_multi_query`。
- Rerank：可选插槽，默认 BM25 reranker。
- Provider registry：可通过 `register_retrieval_provider()` 注册新的 RAG provider。
- Markdown：优先按标题层级切 chunk。
- PDF：文字提取后固定窗口 + overlap。
- 图片：OCR 后固定窗口 + overlap。

初始化数据库：

```powershell
D:\games\EchoWeave\scripts\init-rag-db.ps1
```

可选 query rewrite / rerank 配置：

```json
"rag_query_rewrite_enabled": true,
"rag_query_rewrite_strategy": "local_multi_query",
"rag_query_rewrite_max_queries": 3,
"rag_rerank_enabled": true,
"rag_rerank_strategy": "bm25",
"rag_rerank_candidate_multiplier": 4,
"rag_rerank_original_score_weight": 0.65,
"rag_rerank_bm25_weight": 0.35
```

## Harness

`echoweave_harness` 是 EchoWeave 的约束和反馈层，负责：

- 结构化 audit log。
- 命令、工具、路径、模型、skill、RAG 的策略 DSL。
- 回答质量、工具调用正确率、审批命中率、审批处理率、RAG 命中率、沙盒逃逸拦截率、
  策略拦截率、模型调用成功率等指标。
- 把失败和低质量指标转成可审查的 hardening backlog。

配置示例：

```json
"harness_audit_enabled": true,
"harness_audit_path": "D:/games/EchoWeave/logs/audit.jsonl",
"harness_policy": {
  "allowed_tools": [],
  "denied_tools": [],
  "allowed_paths": [],
  "denied_paths": [],
  "command_allow_patterns": [],
  "command_approval_patterns": [],
  "command_deny_patterns": [],
  "session_model_allowlist": [],
  "session_skill_allowlist": [],
  "session_rag_enabled": null
}
```

生成 harness 报表：

```powershell
D:\games\EchoWeave\scripts\harness-report.ps1
```

默认会读取 `logs/audit.jsonl`，输出指标，并把建议追加到
`logs/harness-feedback.jsonl`。
backlog 记录包含 `metric`、`evidence` 和 `action` 字段，便于后续自动生成测试、
策略或文档补丁。

### Coding Agent Harness 增强

EchoWeave runtime 已内置一组参考成熟 coding agent 的执行约束：

- `edit` 采用唯一搜索替换：`old`/`old_string` 必须在文件中恰好出现一次，成功后返回
  unified diff，避免行号漂移和整文件误写。
- `write` 面向整文件写入：覆盖已有文件时返回 diff，可通过 `overwrite=false` 拒绝覆盖。
- `read` 支持 `start_line`/`end_line` 和 `max_chars`，大文件会保留头尾并标记截断，
  避免一次工具结果冲爆上下文。
- `bash` 会拦截交互式命令，记录命令分类、超时和输出截断信息，并支持 `cd` 工作目录跟踪；
  后续命令会在更新后的工作目录中执行，但仍不能逃出 workspace。
- `agent` 支持 `explore`、`plan`、`verify`、`summarize` 只读角色，用于隔离式扫描工作区并返回
  紧凑报告；传入 `use_model=true` 时会用独立消息上下文调用一次模型生成子 Agent 摘要。
- `agent` 的 `worker` 角色会把指定 scope 复制到临时工作区，在副本里执行唯一文本替换并返回
  unified diff，默认不修改真实 workspace，适合先试修、再由主 Agent/用户确认应用。
- `workers` 提供多 Worker 编排入口：可先 `plan` 多个子任务的读写集合并报告冲突，再 `run`
  只读/写入型隔离 Worker；写同一文件的 Worker 会标记 `requires_serial=true`，避免并行合并时互相覆盖。
- `patch` 提供 worker patch 闭环：`stage` 保存补丁、`show/list` 预览、`apply` 必须传入
  `confirm=true` 才会写入真实 workspace，应用时保存回滚备份，`rollback` 可恢复应用前内容。
- `tool_search` 可按名称或说明搜索当前已注册工具，便于在 Skill/MCP 扩展较多时做能力发现。
- `todo` 提供显式任务清单，支持 `list`、`set`、`clear`，并限制同一时间只有一个
  `in_progress` 项，帮助长任务在 checkpoint/replay 前后保持进度可读。
- RAG 和 memory 注入会标记为不可信参考资料；疑似 prompt injection 的片段会被标注，
  不能覆盖 system prompt 或工具安全策略。
- 历史消息进入模型前会对大段 `tool_result` 做 head/tail 裁剪；再结合已有 summary/compaction
  机制，减少长会话里重复工具输出挤占上下文的问题。
- `tool_execution_mode=streaming` 时，runtime 会在模型流中收到完整的安全只读 `tool_call_end`
  后立即执行该工具，并在 assistant tool_use 消息落盘后回填结果；写文件、命令等副作用工具会延后到
  常规调度阶段，保证安全顺序。
- provider 调用前会动态组装 system prompt 上下文，包括 workspace、工具列表、执行模式、
  RAG 状态和关键安全约束。
- runtime 会读取 `ECHOWEAVE.md`、`AGENTS.md`、`.echoweave/instructions.md`、`CLAUDE.md` 作为
  有长度上限的项目级指令，让仓库约定、测试命令和风格要求能随会话继承；项目指令不能覆盖系统安全规则。
- `echoweave-coding eval` 除了 pass/fail，也支持在 case 中声明 `expected_tools`、
  `forbidden_tools`、`expected_rag_sources`、`expected_policy_blocks` 等字段，输出回答质量、
  工具调用正确率、RAG 命中和策略拦截等行为级 scorecard。
- `echoweave-coding eval --feedback-log <path>` 会把未达标的 scorecard 条目转成 hardening backlog，
  作为后续补测试、补策略、补 RAG golden query 或项目说明的输入。
- `echoweave-coding hardening-plan --audit-log logs/audit.jsonl --feedback-log logs/hardening.jsonl
  --eval-out .echoweave/generated-hardening-eval.json` 会从审计日志生成 backlog，并把建议转换成可复跑的
  eval fixture 草案，形成“日志 -> 指标 -> 建议 -> 回归用例”的自动加固链路。
- harness policy DSL 不只约束工具、路径和命令，也能统一判断会话可用模型、Skill allowlist 和
  RAG 开关，便于管理端把“哪些会话能用哪些能力”落成可审计策略。
- shell policy 的每次决策都会带上 `category` 和 `risk_level`，例如 test/read 为低风险，
  install/vcs_write 进入高风险审批，破坏性命令为 critical 拦截，便于 Web 管理端和审计报表展示。
- 设置 `ECHOWEAVE_SANDBOX_MODE=docker` 后，`bash` 工具会把允许执行的命令包装到受限
  `docker run --rm --network none --read-only` 容器中运行，并支持 `ECHOWEAVE_SANDBOX_IMAGE`、
  `ECHOWEAVE_SANDBOX_MEMORY`、`ECHOWEAVE_SANDBOX_CPUS` 等环境变量调整镜像和资源限制。
- 管理端 API 增加 `/api/audit` 和 `/api/hardening`，用于查看结构化审计指标、生成 hardening backlog
  和 eval fixture；CLI 也提供 `patch-review`、`audit-inspect`、`sandbox-plan`、
  `complex-repo-verify` 方便在没有 Web 页面时完成同样流程。
- `echoweave-coding corecoder-status` 可直接查看上述 CoreCoder 风格能力的落地状态。

## SSE

SSE 事件流：

```text
http://127.0.0.1:8787/events
```

事件包含 `message.inbound`、`message.reply`、`message.error` 和 `heartbeat`。

## Docker Compose

```powershell
Copy-Item D:\games\EchoWeave\config.docker.example.json D:\games\EchoWeave\config.docker.json
docker compose up --build
```

初始化容器内 RAG schema：

```powershell
docker compose exec echoweave python -m echoweave_runtime.rag.init_db --dsn postgresql://echoweave:echoweave-password@postgres:5432/echoweave
```

服务默认监听：

```text
http://127.0.0.1:8787
```

## 验证

```powershell
python -m pytest
```

上线健康检查：

```powershell
D:\games\EchoWeave\scripts\verify-deploy.ps1 -Config D:\games\EchoWeave\config.local.json
```

测试已按领域拆分到 `tests/test_runtime_tools.py`、`tests/test_agent_core.py`、
`tests/test_coding_agent.py`、`tests/test_harness.py`、`tests/test_social_backend.py`、
`tests/test_adapters.py`、`tests/test_web.py`、`tests/test_rag.py`、`tests/test_cli_config.py`。
覆盖平台适配器、审批、管理 API、多模型配置、会话沙盒隔离、RAG chunking、混合排序和
harness 审计/指标。

## 简历描述参考

EchoWeave 可以描述为：

> 一个自研社交平台 AI Coding Agent 网关，参考 AstrBot 的多平台适配思路和
> pi-mono 的 Agent 分层思想，使用 Python 实现会话沙盒、多模型接入、RAG、
> skill 管理、审批权限、harness 审计与反馈闭环。

## English Brief

EchoWeave is a local social-platform AI coding-agent gateway. It combines a Python
coding-agent runtime, AstrBot-style platform adapters, per-conversation
sandboxes, model profiles, optional RAG, skills, approvals, and a harness layer
for policy, audit logs, metrics, and feedback. The main documentation is in
Chinese; this English section is only a short fallback.
