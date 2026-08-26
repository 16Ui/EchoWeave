# 并行工具批次恢复

单个 Tool Invocation 的幂等账本只能回答“这个工具调用是否完成”。当模型一次产生多个工具调用时，还需要回答：恢复后出现的是不是原来的同一批次、哪些成员已完成、哪些成员可以安全继续，以及批次何时可以重新交给模型。

EchoWeave 因此在 Invocation ledger 之上增加 Batch ledger。它不替代单调用账本，也不引入任务队列。

## 稳定身份与成员指纹

批次身份由以下逻辑位置生成：

```text
session_id + logical turn_id + batch_sequence
```

`batch_sequence` 是同一个 Turn 内模型产生工具批次的顺序，从 1 开始。批次 key 只由这个稳定位置生成；有序成员列表的 `tool_call_id + name + input` 则单独生成 fingerprint。

这种拆分有两个作用：

- 恢复 attempt 再次到达同一批次位置时得到同一个 batch key；
- Provider 如果在恢复时改变成员、参数或顺序，会触发 `tool.batch_conflict`，不会把新批次冒充旧批次继续执行。

## 生命周期

```text
tool.batch_started
        |
        +-- 全部形成结果 ----------------------> tool.batch_completed
        |
        +-- 成员状态不确定 --------------------> tool.batch_suspended
                                                    |
                                                    +-> tool.batch_resumed

tool.batch_completed -- Turn 后续失败并恢复 ------> tool.batch_replayed
```

`resumed` 表示上一次批次没有完整收束；`replayed` 表示批次已经完整收束，但 Turn 在后续模型请求中失败，因此需要重新构造 tool result 消息。后者只读取 durable result，不重新执行工具。

## 部分完成决策

每个 Batch event 都包含按成员索引聚合的 `recovery_summary`：

| 成员状态 | 恢复动作 |
| --- | --- |
| `completed` / `completed_error` | 从 Invocation ledger 复用 durable outcome |
| `retryable` | 只读或幂等写工具允许创建下一次 invocation attempt |
| `indeterminate` | 暂停 Turn，等待人工确认完成、授权一次重试或放弃 |
| `pending` | 上次尚未进入工具调用，按正常流程执行 |
| `blocked` | 保持阻断并暴露原因 |

并行执行仍遵守副作用约束：只有全部 runnable member 都是 `read_only` 时才进入线程池；包含写操作的批次退化为确定顺序执行。这里的“部分恢复”不是放宽并发写权限，而是让中断后的状态可解释、可验证。

## 与 Turn 恢复的配合

1. 初次执行在任何 member 启动前写入 `tool.batch_started`；
2. 每个 member 继续使用稳定 Invocation identity，并附带 batch id/sequence/index；
3. 并行 future 全部收束后，Runtime 才逐个生成 tool result 消息；
4. 任一 member 因缺少 durable completion 被阻断时，写入 `tool.batch_suspended`；
5. `recover_turn()` 从 checkpoint 重建历史，再次到达相同 batch sequence；
6. Batch ledger 校验 fingerprint，Invocation ledger 分别执行 reuse、safe retry 或 suspend；
7. 所有成员形成 tool result 后写入 `tool.batch_completed`，模型才能继续推理。

安全未完成成员不需要人工解锁；非幂等或未知副作用成员仍必须通过现有 `tool.invocation_resolved` 协议处理。人工决策事件会保留 batch id、sequence 和 index，因此可以回到批次视角定位。

## 可观测性

持久化并发事件包括：

- `tool.batch_started`；
- `tool.batch_suspended` / `tool.batch_resumed`；
- `tool.batch_completed` / `tool.batch_replayed`；
- `tool.batch_conflict`。

`tool.batch_completed` 同时记录结果数量，以及本次 attempt 中实际执行、durable reuse 和 blocked 的成员数。Task graph stats 聚合各生命周期事件，可直接用于故障注入报告。

## 当前边界

- Batch ledger 与 Invocation ledger 都使用 session append-only JSONL，单进程内由锁保护；
- Logical Turn 由跨进程 Execution Lease 和 fencing token 限制为单 owner；
- 线程池只负责同一 owner 内的只读成员并发，不改变 Lease 所有权；
- 外部副作用与本地 completion event 之间仍不存在分布式事务；
- Provider 必须在恢复时保持批次逻辑位置和成员内容一致，否则恢复会安全失败。
