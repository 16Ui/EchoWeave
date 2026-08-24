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

## 状态机约束

```text
created -> running -> completed
   |          |  \
   |          |   -> waiting_for_tool -> running
   |          -> suspended -> running
   +-----------> failed / timed_out / cancelled
```

终态不能重新进入运行态。`waiting_for_tool` 和 `suspended` 已纳入协议，但尚未作为自动恢复依据；在工具幂等语义完成前，不应把“存在 checkpoint”误称为“可以安全重放”。

## 恢复边界

当前已保证：

- recoverable turn 在调用模型前创建起始 checkpoint；
- 成功、运行失败与超时都形成结构化终态；
- checkpoint 即使遇到后续失败仍会保留；
- 旧调用方不会因 API 改造改变异常类型；
- 故障注入测试覆盖成功关联、超时分类和终态转换约束。

当前尚未保证：

- 工具副作用的 exactly-once；
- 进程重启后自动续跑；
- Provider 级重试预算与退避；
- 并行工具批次的部分完成恢复。

## 下一阶段

下一切片将在现有 `tool_call/tool_result/tool_error` 事件上增加稳定 invocation key 与工具副作用分类：纯读工具可安全重放，写入工具需要结果账本或补偿策略，高风险工具默认禁止自动重放。完成后再实现 `recover_turn(checkpoint_id)`，避免产生“恢复成功但副作用执行两次”的隐蔽错误。
