# Orphan Turn 自动恢复

EchoWeave 将“发现可能需要恢复”和“获得恢复执行权”分成两个阶段。领域术语见根目录
[CONTEXT.md](../CONTEXT.md)，自动恢复边界见
[ADR-0002](adr/0002-conservative-automatic-orphan-recovery.md)。

## 处理链路

```text
Session JSONL + Execution Lease
              │
              ▼
      OrphanTurnScanner（只读）
              │ Recovery Candidate
              ▼
  OrphanRecoveryScheduler（有界调度）
              │ acquire Lease
              ▼
       持有 Lease 后重新校验状态
              │
              ▼
       AgentCore.recover_turn()
```

扫描器只选择同时满足以下条件的 Turn：

- 最新状态是 `created / running / waiting_for_tool`；
- 持久化 Execution Lease 已经过期；
- 存在与该 Turn 关联的起始 checkpoint；
- 尚未到达 `max_attempts_per_turn`；
- 没有完成或放弃记录。

`suspended` 可能包含无法判断是否已经发生的外部副作用，因此不会自动恢复。无 Lease 的旧会话也不会仅凭进程重启时间进行猜测。

## 生命周期接入

调度器实现 `RuntimeHost` 的同步生命周期协议，并且默认不会自动启动：

```python
from echoweave_agent_core import OrphanRecoveryConfig, OrphanRecoveryScheduler
from echoweave_runtime.lifecycle import RuntimeHost

scheduler = OrphanRecoveryScheduler(
    core,
    OrphanRecoveryConfig(
        scan_interval_seconds=5.0,
        max_concurrent_recoveries=1,
        max_recoveries_per_scan=4,
        max_attempts_per_turn=3,
    ),
)

host = RuntimeHost().register(scheduler)
with host:
    run_service()
```

Coding Agent 可以通过 `agent.build_recovery_scheduler(config)` 创建同一个组件。`stop()` 会先停止扫描，再取消尚未开始的任务，并等待已经开始的恢复结束，避免 Runtime Host 关闭后仍悄悄启动新 attempt。

## 并发控制

- 一个 daemon dispatcher thread 周期扫描 Session；
- 固定大小 `ThreadPoolExecutor` 限制同时恢复的 Turn 数；
- 默认共享一个 `AgentCore` 时只允许一个 recovery worker，因为 `AgentSessionRuntime` 持有 Turn 级可变上下文；配置大于 1 时必须提供为每个 Candidate 创建独立 Core/Runtime 的 `core_factory`；
- `RLock` 保护 in-flight 集合、计数器和最近结果；
- 同一调度器不会重复提交正在恢复的 Turn；
- 多个进程或调度器仍可发现同一 Candidate，但只有 Lease acquire 的胜者可以继续；
- 获得 Lease 后会重新读取 Turn 状态和 attempt，消除“扫描后、acquire 前”状态变化造成的 TOCTOU；
- `snapshot()` 提供 scans、scheduled、completed、failed、contended、scan issues 和 in-flight 数量。

`turn.recovery_started` 会记录 `mode=automatic` 与 `trigger=expired_execution_lease`。手工恢复仍记录 `mode=manual`，两条路径共用同一 checkpoint、invocation ledger 和 fencing 规则。

## 失败隔离与边界

单个损坏 Session 或 Lease 会形成 `OrphanScanIssue`，不会阻止其他 Session 被扫描。自动恢复失败会形成正常 Turn Outcome；本轮调度器不会把终态失败重新当作 Orphan。若恢复进程再次崩溃，新的进程只能在 Lease 再次过期且 attempt 尚未达到上限时继续。

当前不提供运行中模型请求的强制线程中断；Runtime Host 关闭会等待已经进入恢复执行的同步调用返回。外部工具仍需使用 invocation key、幂等键或自身 fencing 支持处理本地账本无法覆盖的副作用窗口。
