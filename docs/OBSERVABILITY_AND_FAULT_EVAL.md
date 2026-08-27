# Trace 可视化与故障注入 Eval

EchoWeave 的可观察性直接投影 Session JSONL 中的持久化事件，不复制第二套 Trace 存储。相同
`turn_id` 的每次执行或恢复 attempt 使用独立 `trace_id`，因此 Provider 重试、Tool 调用、Policy、
Execution Lease、Recovery 和 Eval 事件可以在同一时间线上关联，同时保留逻辑 Turn 的连续性。

## Trace 投影

`echoweave_runtime.observability.build_trace_timeline()` 对事件流执行只读投影：

- 以 `trace_id` 分组，并使用 `turn_id` 关联少量没有重复携带 trace 的同 Turn 事件；
- 汇总 attempt、终态、开始/结束时间、耗时、事件数、分类数和可靠性信号数；
- 将事件归类为 Turn、Provider、Tool、Retrieval、Checkpoint、Policy、Eval 与 Runtime；
- 识别 retry、circuit、blocked tool、Lease takeover/lost 和 recovery 等信号；
- 每条 Trace 的返回事件数有上限，长字符串、深层对象和大数组会截断；
- API key、密码、Authorization、凭证和访问 Token 会在返回管理端前脱敏，fencing token 保留为并发证据。

Social 层只聚合状态文件中已经登记的 Runtime Session，同一个 Session 被多个会话引用时只读取一次。
单个损坏 Session 会进入 `issues`，不会阻止其他 Trace 展示。

管理 API 均要求管理员鉴权：

- `GET /api/traces?limit=50&event_limit=120`：返回已登记会话的 Trace 列表和时间线；
- `GET /api/evals/fault/latest`：返回最近一次可靠性 Eval 报告；
- `POST /api/demos/reliability`：运行隔离的可靠性演示并把生成 Session 登记到 Trace 页面。

管理端不会每 5 秒重扫完整 Session 文件；Trace 在首次打开时加载，也可以通过“刷新 Trace”手工刷新。

## 故障注入场景

一键演示直接调用正式运行时组件，不访问外部模型、不执行 Shell，也不伪造通过结果：

| 场景 | 注入方式 | 通过条件 |
| --- | --- | --- |
| Provider transient retry | 第一次模型请求抛出 `TimeoutError` | 受限预算只重试一次，第二次成功，并出现 `provider.retry_scheduled` |
| Provider circuit breaker | Provider 持续超时，阈值设为一次失败 | Circuit 进入 open，下一请求被 `ProviderCircuitOpenError` 拒绝 |
| Sandbox path escape | 检查 `cat ../outside-secrets.txt` | `ShellCommandPolicy` 在执行前返回 `deny.path_traversal` |
| Lease takeover fencing | 让旧 Lease 的 TTL 到期后由新 Owner acquire | fencing token 单调递增，旧 Owner 的 `assert_owned()` 失败并记录 `turn.lease_lost` |

每个场景生成独立 `turn_id/trace_id`、开始/结束状态和 expected/observed 证据。最终报告包含场景通过率、
平均得分、Session 路径与每个场景耗时；报告和完整事件流可以互相交叉验证。

## 一条命令生成演示证据

```powershell
echoweave demo --cwd D:\games\EchoWeave
```

如果需要让正在运行的管理端发现该 Trace，可以同时登记 Social State：

```powershell
echoweave demo `
  --cwd D:\games\EchoWeave `
  --state-path D:\games\EchoWeave\echoweave-state.json `
  --json
```

默认产物位于：

```text
<workspace>/echoweave-data/demos/<run-id>/
├── fault-eval-report.json
└── workspace/
    └── echoweave-data/sessions/<session-id>.jsonl
```

`echoweave-data/demos/latest.json` 保存最近一次报告的副本，管理端重启后仍能展示最近结果。每次运行使用
新的隔离目录和 Session，不会覆盖前一次证据。

## 当前边界

- 当前是确定性的本地可靠性 Eval，不衡量真实模型答案质量；模型质量仍由 `echoweave-coding eval` 的行为 scorecard 覆盖。
- Trace 查询面向单机文件存储；多副本部署需要共享 Trace 后端或集中式遥测系统。
- 管理端展示的是有界事件投影；完整审计证据仍以 Session JSONL 和 fault eval report 为准。
