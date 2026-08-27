# Runtime 生命周期

EchoWeave 使用 `RuntimeHost` 统一管理长生命周期资源。首个接入组件是 Web Gateway，后续 Channel、
Provider 和 Plugin 应沿用同一协议，不再由各入口自行拼接清理逻辑。

Execution Lease heartbeat 由进程内唯一调度线程统一维护，而不是每个 Turn 创建一个线程。协调器按 SessionStore root 复用；它们属于并发基础设施，不代表跨进程的唯一 owner。跨进程所有权只由持久化 Lease 和 fencing token 决定。

`OrphanRecoveryScheduler` 是第二个可选生命周期组件：一个 dispatcher thread 负责扫描，固定大小 worker pool 负责恢复。关闭时先停止新扫描，再等待已开始的恢复结束。

## 生命周期契约

受管组件只需要提供稳定且唯一的 `name`，以及同步的 `start()`、`stop()` 方法：

```python
class LifecycleComponent(Protocol):
    @property
    def name(self) -> str: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
```

`RuntimeHost` 保证：

- 组件按注册顺序启动，按相反顺序关闭；
- 某个组件启动失败时，已经启动的组件会立即逆序回滚；
- 回滚和关闭会尝试所有待关闭组件，不因单个清理失败提前退出；
- 原始启动异常通过异常链保留，额外的回滚异常以结构化列表提供；
- 重复启动和关闭是幂等的，运行后不允许再动态注册组件；
- 状态显式经历 `created / starting / running / stopping / stopped / failed`。

## 当前接入

`HubWebhookServer.serve()` 的外部调用方式保持不变，内部由 `HttpServerComponent` 负责端口绑定、
阻塞服务和 socket 释放，再交给 `RuntimeHost` 管理。这使 CLI 入口只负责配置和组装，资源所有权
留在运行时层。

需要长期运行和进程崩溃恢复时，可在 Web/Channel 组件之前注册 `OrphanRecoveryScheduler`。它默认不隐式启动，避免一次性 CLI 因扫描历史会话而产生意外副作用。

## 后续接入顺序

建议保持以下依赖顺序注册，并由 Runtime Host 自动逆序关闭：

```text
Runtime infrastructure
  -> Model Provider / Retrieval Provider
  -> Plugin
  -> Channel
  -> Web / CLI entrypoint
```

异步组件暂时通过同步适配器接入。等 Channel 和 Plugin 契约稳定后，再增加异步生命周期协议，避免
同时维护两套尚未被实际使用的抽象。
