# 并发与执行接管

EchoWeave 的并发设计围绕一个不变量展开：同一时刻只有一个 Execution Owner 可以推进一个 Logical Turn。线程池、进程内锁和 Singleton 都只是实现手段，不能代替跨进程所有权协议。

领域术语见根目录 [CONTEXT.md](../CONTEXT.md)，关键决策见 [ADR-0001](adr/0001-expiring-execution-leases.md)。

## 并发结构

```text
Process
  ├─ KeyedRLockPool singleton
  │    └─ 每个 session / lease key 一把 RLock
  ├─ LeaseHeartbeatScheduler singleton
  │    └─ 一个 daemon thread 服务全部 active leases
  ├─ ExecutionLeaseCoordinator（每个 SessionStore root 一个实例）
  │    └─ owner_id + active lease table
  └─ Agent Turn
       ├─ Provider request
       └─ read-only ToolBatch -> ThreadPoolExecutor

Cross-process
  ├─ session JSONL sidecar file lock
  └─ lease record sidecar file lock + atomic replace
```

## 多线程

### 只读工具并发

模型一次返回多个工具调用时，Runtime 先判断所有 runnable member 的副作用等级。只有全部为 `read_only` 才提交到 `ThreadPoolExecutor`；存在写操作时退化为顺序执行。

Future 的外部执行结果会先全部收束，再按模型给出的成员顺序生成 tool result。这避免“哪个线程先完成，模型历史就按哪个顺序写入”的非确定性。

### 单后台心跳线程

所有 active Lease 共享一个 `_LeaseHeartbeatScheduler` daemon thread。它使用 `Condition + RLock` 管理下一次唤醒时间，避免每个 Turn 创建独立线程造成线程膨胀。调度器只续租，不执行 Agent 业务。

## 锁

### 为什么同时需要两层锁

- `RLock`：解决同一进程内多个线程、多个 SessionStore/Coordinator 对象之间的竞争；
- 文件锁：解决两个 Python 进程同时写同一个 Session 或争抢同一个 Lease；
- 原子 `os.replace`：保证读者只能看到旧 Lease 或完整的新 Lease，不看到半个 JSON。

Session JSONL 的 append 和 read 都经过“按文件 RLock + sidecar file lock”，因此并行工具线程不会把两条 JSON 写到同一行。Lease 的 read-check-write 则在同一个组合锁临界区中完成，避免 TOCTOU：两个进程不能同时读取“已过期”后都认为自己接管成功。

使用 `RLock` 而不是普通 `Lock`，是因为同一线程的高层操作可能再次进入按 key 的存储读取路径；可重入锁避免自锁。Singleton 创建本身使用普通 `Lock`，因为这段临界区不需要重入。

## Lease、Heartbeat 与 Fencing

Acquire 在锁内执行：

1. 读取当前 Lease；
2. active 且未过期则拒绝；
3. expired/released/missing 才能授予；
4. 每次授予将 fencing token 加一；
5. 原子写入后，记录 acquire 或 takeover 事件。

Heartbeat 更新 `heartbeat_at` 和 `expires_at`，但不增加 token。正常结束将 Lease 标为 `released`，记录不会删除，因此下一任 owner 仍能获得更大的 token。

Agent 在以下边界调用 `assert_owned()`：

- 每轮 Agent Loop 开始；
- 发起 Provider 请求前；
- Tool Invocation prepare 前；
- 工具副作用真正执行前。

如果旧进程暂停过久、Lease 被新进程以更高 token 接管，旧 owner 恢复后会得到 `ExecutionLeaseLostError`。该异常不能被包装成普通 tool error，否则 stale owner 仍可能继续让模型推理；Runtime 会立即终止 attempt，并返回可重试的 `TurnFailureKind.CONCURRENCY`。

## Singleton 的正确边界

项目使用三种进程内复用：

- `KeyedRLockPool`：相同文件路径总是拿到同一把进程内锁；
- `_LeaseHeartbeatScheduler`：整个进程只有一个心跳线程；
- `ExecutionLeaseCoordinator.for_store()`：相同 SessionStore root 复用 owner 与 active table。

这些 Singleton 只减少重复状态和线程，不能证明“系统全局只有一个执行者”。另一个进程有自己的 Singleton，所以跨进程正确性仍必须依赖文件锁、Lease 记录和 fencing token。

## 为什么 GIL 不够

GIL 只保证一个解释器进程在某一瞬间执行 Python bytecode，不保证以下复合操作原子：

```text
read lease -> check expiry -> increment token -> write lease
```

线程可能在任意两个步骤之间切换；文件 I/O 还会释放 GIL，更无法协调另一个进程。因此共享状态必须显式加锁，不能用“Python 有 GIL”解释并发安全。

## 面试回答模板

可以这样描述这部分：

> 我没有把 Singleton 当作分布式锁。项目中同一个 Turn 通过带 TTL 的 Execution Lease 确定 owner，后台单线程统一 heartbeat；接管时 fencing token 单调递增。进程内使用 keyed RLock 防止线程竞争，进程间用 sidecar file lock 把 read-check-write 变成临界区，Lease JSON 通过原子替换提交。Agent 在 Provider 和 Tool 副作用边界检查 token，所以旧进程恢复后不会继续执行。并行工具只允许只读调用进入线程池，结果按原顺序归并，保证历史确定性。

如果面试官追问限制，应主动说明：

- 这不是严格 exactly-once；外部系统不校验 fencing/idempotency key 时，已经发出的旧副作用无法撤回；
- heartbeat 线程证明进程仍维护 Lease，不证明业务线程一定有进展；当前选择保守地阻止 hung executor 被自动接管；
- 当前接管由 `recover_turn()` 触发，还没有服务启动后的 orphan scanner；
- 单机文件锁适合个人 Runtime，未来多节点部署应把 Lease backend 换成支持条件写的数据库或协调服务。
