# echoweave_harness

Harness 还提供确定性的可靠性故障注入运行器，覆盖 Provider retry/circuit、路径穿越阻断和
Execution Lease fencing。运行 `echoweave demo --cwd <workspace>` 会生成结构化 Eval 报告与可由
管理端 Trace 时间线直接读取的 Session 事件。完整设计见
[`docs/OBSERVABILITY_AND_FAULT_EVAL.md`](../../docs/OBSERVABILITY_AND_FAULT_EVAL.md)。

`echoweave_harness` 是 EchoWeave 的 Agent harness 包，用来集中管理跨业务模块的治理能力。

它负责：

- 结构化 audit event。
- Policy DSL 加载与评估。
- 质量、安全和可用性指标。
- 把失败、拦截、低命中率等信号转成可处理的 hardening 建议。
- runtime bridge：把 harness 的审计和策略能力安装到 `echoweave_runtime.governance`。

业务模块仍然拥有自己的业务逻辑；harness 包只提供统一的约束、记录、度量和反馈格式。
runtime 不直接依赖 harness，harness 以适配器形式订阅和约束 runtime 行为。

## Runtime Bridge

导入 `echoweave_harness` 时会自动执行 `install_runtime_bridge()`，把：

- `record_audit()` 接到 `record_runtime_audit()`。
- `HarnessPolicyEvaluator` 接到 `evaluate_runtime_tool/path/command()`。

因此工具、命令、文件访问、RAG 检索和 Agent turn 可以继续产生 harness audit log，
但底层 runtime 只依赖自己的 governance 协议。

## Audit Event

Audit event 使用 JSONL 记录，字段稳定：

- `category`
- `action`
- `status`
- `subject`
- `conversation_id`
- `session_id`
- `actor_id`
- `workspace`
- `latency_ms`
- `metadata`

当前已接入的审计类别：

- `approval`：request、approve、deny、revoke、retry。
- `command`：命令策略判断和命令执行结果。
- `file`：read、write、edit、list、grep、find。
- `model`：模型调用开始、成功、失败。
- `rag`：检索成功和失败。
- `access` / `message`：社交网关访问决策和消息流。

## Policy DSL

运行时从配置里的 `harness_policy` 读取策略：

```json
{
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

说明：

- pattern 使用正则表达式。
- 空 allowlist 表示沿用运行时默认行为。
- deny 规则优先级高于 allow 规则。
- `command_approval_patterns` 会把命令强制送入 EchoWeave 审批流。
- `session_model_allowlist` 可限制会话可选模型。
- `session_skill_allowlist` 可限制会话可用 skill。
- `session_rag_enabled` 为 `true` 或 `false` 时会覆盖会话自己的 RAG 开关。

## 指标与反馈

`compute_harness_metrics()` 会计算：

- `answer_quality`：回答质量。
- `tool_call_success_rate`：工具调用成功率。
- `approval_hit_rate`：审批命中率。
- `approval_resolution_rate`：审批处理率。
- `rag_hit_rate`：RAG 命中率。
- `sandbox_escape_block_rate`：沙盒逃逸拦截率。
- `policy_block_rate`：策略拦截率。
- `model_call_success_rate`：模型调用成功率。
- `category_status_counts`：按 audit category/status 聚合的事件数。

`suggest_harness_improvements()` 会把低指标、重复错误和拦截事件转成结构化建议。
每条建议包含 `metric`、`evidence` 和 `action`，方便后续自动 hardening 流程把建议转成
测试、策略 DSL 或文档补丁。

生成报表：

```powershell
python -m echoweave_harness.report --audit-log D:\games\EchoWeave\logs\audit.jsonl
```

追加反馈 backlog：

```powershell
python -m echoweave_harness.report --audit-log D:\games\EchoWeave\logs\audit.jsonl --feedback-log D:\games\EchoWeave\logs\harness-feedback.jsonl
```

推荐直接使用项目脚本：

```powershell
D:\games\EchoWeave\scripts\harness-report.ps1
```

## 自修复闭环

当前闭环是保守型设计：harness 不会擅自改代码或放宽策略，而是把建议写入
`logs/harness-feedback.jsonl`。后续可以在管理面板中展示这些建议，再由开发者或自动化流程
把它们转成规则、文档或测试。

## English Brief

`echoweave_harness` centralizes policy, structured audit logs, metrics, and
feedback for EchoWeave. The main documentation is Chinese; this section is only a
short fallback for English readers.
