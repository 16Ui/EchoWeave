# 可恢复 Agent Runtime

EchoWeave 不把“捕获异常后再调用一次模型”视为恢复。可恢复执行需要先回答三个问题：一轮任务进行到了哪里、哪些副作用已经发生、重新执行时从哪里继续。因此当前实现先建立 durable turn 协议，再逐步加入幂等工具调用和自动恢复策略。

## 当前协议

`AgentCore.execute_turn()` 是可恢复入口，返回 `TurnOutcome`，不会把运行异常直接抛给应用层。Outcome 包含：

- 稳定的 `turn_id` 与 `trace_id`；
- `created -> running -> completed/failed/timed_out/cancelled` 状态；
- 起始 checkpoint、session 定位和执行耗时；
- 结构化 `TurnFailure`：失败类别、阶段、异常类型、消息和 `retryable` 提示；
- 成功时的原 `TurnResult`。

状态转换以 `turn.state_changed` 事件追加到 session JSONL。AgentCore 和底层 Runtime 共用同一组 `turn_id/trace_id`，因此模型、工具、策略和扩展事件可以归并到同一条执行轨迹。

```python
outcome = core.execute_turn(TurnRequest(prompt="inspect this repository", resume=False))
if outcome.succeeded:
    print(outcome.require_result().text)
else:
    print(outcome.failure.to_dict())
```

旧的 `AgentCore.run_turn()` 保持兼容：成功时仍返回 `TurnResult`，失败时重新抛出原始异常。Coding Agent 和 Social Agent 主链已经改用 recoverable 入口，应用边界可以读取结构化结果，同时保留原有用户体验。

失败或超时后，可以从该 Turn 的起始 checkpoint 发起受控恢复：

```python
recovered = core.recover_turn(
    RecoverTurnRequest(
        session_path=failed.session_path,
        checkpoint_id=failed.checkpoint["id"],
    )
)
```

Coding Agent 对应提供 `agent.recover(session_path, checkpoint_id)`。

## 状态机约束

```text
created -> running -> completed
   |          |  \
   |          |   -> waiting_for_tool -> running
   |          -> suspended -> running
   +-----------> failed / timed_out / cancelled
```

终态不能重新进入运行态。`waiting_for_tool` 和 `suspended` 已纳入协议，但尚未作为自动恢复依据。

## Tool Invocation 账本

串行、并行和流式工具路径共用 append-only invocation ledger。每次有效工具调用由两部分标识：

- identity：`session_id + turn_id + tool_call_id`，表示逻辑调用位置；
- fingerprint：工具名和规范化参数的 SHA-256，表示调用内容。

两者共同生成稳定 `invocation_key`。相同 identity 如果出现不同 fingerprint，会写入 `tool.invocation_blocked` 并拒绝执行，避免模型或恢复逻辑悄悄改变已经开始的调用。账本事件包括：

- `tool.invocation_started`：副作用执行前持久化；
- `tool.invocation_completed`：保存成功结果或结构化错误；
- `tool.invocation_reused`：检测到 durable result，直接复用而不再次执行工具；
- `tool.invocation_blocked`：参数冲突、并发重复或无法判断副作用是否已发生。

工具需要声明副作用等级：`read_only`、`idempotent_write`、`non_idempotent` 或 `unknown`。扩展工具未声明时默认为 `unknown`。分类也可以根据参数动态收紧，例如 `write(overwrite=false)` 会从 `idempotent_write` 降级为 `non_idempotent`。

进程崩溃后如果只留下 started 事件：只读和幂等写工具允许产生下一次 attempt；非幂等和未知工具进入 indeterminate 状态，默认禁止自动重放。并行批次也使用同一账本锁，防止同一 Runtime 内的重复调用同时穿透。

如果工具已经返回，但 `tool.invocation_completed` 写入失败，Runtime 会立即把当前 Turn 停在 `suspended`，不会继续让模型生成成功回复。该规则同时适用于首次执行和恢复 attempt。

## 受控恢复

`recover_turn()` 会验证 checkpoint 和原 Turn 的关系，并从 checkpoint 的 inclusive event index 重建当时的 history/summary。恢复不会修改失败 attempt 的终态，而是：

1. 保持原 `turn_id`，生成新的 `trace_id`；
2. 为同一逻辑 Turn 创建 attempt 2、3……；
3. 写入 `history_reset`，把失败尝试产生的消息从后续可见上下文中排除；
4. 使用 checkpoint 后落盘的原始用户输入，不允许恢复时偷偷替换任务；
5. 让 invocation ledger 对模型重新产生的工具调用执行复用、重试或阻断；
6. 写入 `turn.recovery_started` / `turn.recovery_finished` 作为审计边界。

已完成 Turn 不允许恢复。状态仍为 `running/created/waiting_for_tool/suspended` 的 Turn 默认也不允许恢复；只有操作者确认旧 executor 已不存在后，才能显式设置 `allow_incomplete=True`，避免两个执行器同时推进同一 Turn。

恢复期间遇到 indeterminate 工具时，Runtime 不再把它降级成普通 tool error 交给模型继续推理，而是把 attempt 停在 `suspended`，返回 `TurnFailureKind.INDETERMINATE_TOOL`。这保证了“模型回复完成”不会掩盖副作用状态未知的问题。

## 人工处置 indeterminate 调用

暂停后可以通过 `list_indeterminate_tool_invocations()` 获取 invocation key、工具、副作用等级、attempt 和阻断原因，再调用 `resolve_tool_invocation()` 写入 append-only 人工决策：

- `confirm_completed`：操作者确认外部副作用已经完成，并提供 `{status: ok, content: ...}` 或 `{status: error, error: ...}`。下次恢复会复用补录结果，不重新调用工具；
- `allow_retry`：操作者确认上一次副作用没有发生，仅授权下一个 invocation attempt 执行一次。授权一经消费即失效，如果仍没有 durable completion，Turn 会再次暂停；
- `abandon_turn`：放弃该调用和整个 Turn，状态从 `suspended` 进入 `cancelled`，之后禁止恢复。

```python
pending = core.list_indeterminate_tool_invocations(session_path)
core.resolve_tool_invocation(
    ResolveToolInvocationRequest(
        session_path=session_path,
        invocation_key=pending[0]["invocation_key"],
        resolution="confirm_completed",
        outcome={"status": "ok", "content": "external operation confirmed"},
        actor="operator-id",
        note="verified in external system",
    )
)
recovered = core.recover_turn(
    RecoverTurnRequest(session_path=session_path, checkpoint_id=checkpoint_id)
)
```

人工决策以 `tool.invocation_resolved` 事件保存，并记录 actor/note。Coding Agent 同样暴露 list/resolve 方法。只有 `suspended` Turn 可以接受这类决策，避免把人工覆盖变成任意篡改正常执行结果的后门。

## 恢复边界

当前已保证：

- recoverable turn 在调用模型前创建起始 checkpoint；
- 成功、运行失败与超时都形成结构化终态；
- checkpoint 即使遇到后续失败仍会保留；
- 旧调用方不会因 API 改造改变异常类型；
- 已完成调用可复用 durable result，避免重复副作用；
- 未完成调用按照工具副作用等级决定安全重试或阻断；
- checkpoint 可重建历史，同一逻辑 Turn 可通过新 attempt 受控恢复；
- indeterminate 工具会暂停恢复，而不是被 Agent Loop 静默吞掉；
- 人工补录、单次重试授权和放弃操作均形成可审计事件；
- Provider 临时故障只重试当前模型请求，不重放整个 Agent Loop；
- 单请求 attempt 上限与 Turn 级共享 retry budget 同时约束重试放大；
- 流式调用仅在尚未输出任何事件时重试，部分输出后中断会明确失败；
- Provider 连续临时故障触发进程内 closed/open/half-open 熔断；
- 并行工具批次使用稳定逻辑位置和成员指纹，恢复时不能悄悄替换批次内容；
- 批次部分完成后，durable member 复用结果，安全 member 重试，危险 indeterminate member 暂停；
- 故障注入测试覆盖成功关联、超时、checkpoint 故障、调用冲突、结果复用和中断重放决策。

当前尚未保证：

- 外部系统与本地账本之间的事务型 exactly-once；
- 进程重启后自动续跑；
- 跨进程共享或持久化的 Provider 熔断状态；
- 多进程同时接管同一批次的分布式租约。

这里仍然不宣称严格 exactly-once：工具完成副作用后、`tool.invocation_completed` 落盘前仍存在不可消除的崩溃窗口。除非外部工具支持事务或同一个 idempotency key，这个窗口只能通过“安全工具重试、危险工具阻断、人工确认或补偿操作”处理。

Provider 可靠性层的分类、预算、流式边界、熔断状态和事件协议见
[PROVIDER_RELIABILITY.md](PROVIDER_RELIABILITY.md)，批次级协议见
[PARALLEL_BATCH_RECOVERY.md](PARALLEL_BATCH_RECOVERY.md)。下一切片将继续处理进程重启后的运行接管。
