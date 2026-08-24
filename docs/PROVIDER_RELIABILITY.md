# Provider 可靠性契约

EchoWeave 将 Provider 网络恢复与 Turn/工具恢复分成两层。Provider 层只能重试一次明确的模型请求；它不会重新提交用户消息、重建 Turn 或重放已经开始的工具调用。这样可以吸收短暂网络抖动，同时不把“重试”变成隐式重复整个 Agent Loop。

## 两级预算

每次模型请求受 `max_attempts` 限制；同一个 Turn 内的所有模型请求还共享 `max_retries_per_turn`。例如一次 Turn 先调用模型产生工具、执行工具后再调用模型生成答案，两次请求消耗同一个 Turn budget。

```python
from echoweave_runtime import (
    CircuitBreakerPolicy,
    ProviderReliabilityConfig,
    ProviderRetryPolicy,
)

reliability = ProviderReliabilityConfig(
    retry=ProviderRetryPolicy(
        max_attempts=3,
        max_retries_per_turn=4,
        base_delay_seconds=0.25,
        max_delay_seconds=4.0,
        max_retry_after_seconds=30.0,
        jitter_ratio=0.1,
    ),
    circuit_breaker=CircuitBreakerPolicy(
        failure_threshold=3,
        recovery_timeout_seconds=30.0,
    ),
)
```

默认仅重试 timeout、连接错误、`408`、`429` 和 `5xx`。认证、权限、参数等普通 `4xx` 不重试，也不累计熔断失败。退避采用有上限的指数增长和 jitter；Provider 返回 `Retry-After` 时优先采用它，但仍受本地上限约束。

## 流式安全边界

流式请求只有在尚未向 Runtime 交付任何 stream event 时可以重试。一旦已经输出文本片段或工具调用片段，连接中断会记录 `provider.stream_interrupted` 并直接失败，不重新发起请求。

该限制避免两类问题：

- 用户看到重复文本；
- 已被增量解析的工具调用在新响应中再次出现。

因此，Provider 重试不替代 Turn 恢复。部分流式输出后的失败由结构化 `TurnOutcome` 和 checkpoint 机制接管。

## 熔断状态机

熔断状态按 `provider_key` 在当前 Runtime 进程内共享：

```text
closed --连续临时故障达到阈值--> open
open --恢复窗口到期--> half-open
half-open --探测成功--> closed
half-open --探测失败--> open
```

`open` 状态拒绝真实请求并抛出 `ProviderCircuitOpenError`，其中包含 Provider key 和建议等待时间。`AgentCore` 将它转换为 `TurnFailureKind.PROVIDER`，标记 `retryable=True`，应用层无需分析 SDK 私有异常。

当前熔断状态没有跨进程持久化。这是明确边界：单进程可以阻止持续故障放大；多副本部署仍需要共享状态或由网关统一限流。

## 可观测事件

可靠性事件会写入 session JSONL，并通过 Runtime observer/SSE 发出：

- `provider.retry_scheduled`：记录 attempt、delay、delay source 和剩余 Turn budget；
- `provider.retry_exhausted`：区分单请求 attempt 耗尽与 Turn budget 耗尽；
- `provider.stream_interrupted`：明确标记已经出现部分输出；
- `provider.circuit_opened` / `provider.circuit_rejected`；
- `provider.circuit_half_open` / `provider.circuit_closed`。

Task graph stats 同时聚合 Provider retry、retry exhausted、circuit open 和 stream interrupted 次数，便于后续可靠性报表与故障注入评估。

## 与工具幂等的关系

Provider 可靠性发生在单次模型请求边界内；Tool Invocation ledger 发生在副作用执行边界。两者不互相越权：

1. 模型请求在输出前暂时失败，可以在预算内重试；
2. 模型已经产生工具调用后，工具是否能重放只由 invocation ledger 和副作用等级决定；
3. 整个 Turn 失败后，只能通过 `recover_turn()` 创建新 attempt；
4. 非幂等工具状态不确定时，恢复仍会暂停并等待人工决策。

这套分层不宣称 exactly-once，但明确限制了每一层允许自动重试的范围。
