# 统一事件协议

## 设计问题

早期实现中，同一条入站消息会依次变成 `EchoWeaveEvent` 和 `SocialMessage`，回复也存在两套 DTO；
Web SSE 与 Runtime JSONL 又分别定义了自己的事件信封。这些模型字段高度重叠，却会在边界处复制数据，
容易造成附件、关联 ID 或新字段只在部分链路生效。

## 决策

协议分成两个层次，不使用无边界的“万能事件字典”：

1. `InboundMessage` / `OutboundMessage` 表达稳定的消息语义和路由信息；
2. `AgentEvent` 表达消息、流式增量、工具调用和失败等事实的传输信封。

```text
Channel payload
  -> PlatformAdapter
  -> InboundMessage
  -> Backend / Agent facade（同一个对象，不再复制）
  -> OutboundMessage
  -> PlatformAdapter
  -> Channel payload

                         +-> AgentEvent -> Web SSE
Inbound/OutboundMessage -+-> AgentEvent -> Runtime JSONL / Observer
```

`Attachment` 只保存类型、URI、媒体类型、名称和大小等引用信息，不把二进制内容塞入事件流。实际文件仍由
Channel 或受控对象存储管理，从而避免大消息复制和不同平台的临时 URL 泄漏到 Agent 内部。

## 协议不变量

- 平台、会话和发送者/目标 ID 必须非空，错误在边界处立即暴露；
- 消息附件使用不可变 tuple，事件 payload 在构造时归一化为 JSON 数据；
- 事件类型使用稳定的小写命名空间，例如 `message.inbound`、`stream.delta`；
- 每个事件包含全局 `event_id`、UTC 时间、来源和 `schema_version`；
- `correlation_id` 关联同一消息或 turn，`sequence` 表达流式事件内部顺序；
- SSE 自增 `cursor` 只用于断线续传，不充当跨进程事件 ID；
- 旧 `EchoWeaveEvent` / `EchoWeaveReply` 仅是规范模型的导入别名；旧 `SocialMessage` 构造器只负责参数名兼容。

## 为什么不使用继承树或事件枚举

当前阶段只有两种稳定消息方向，因此不引入多层事件继承体系。事件类型也保持可扩展字符串，并通过格式
校验和集中常量约束常用名称，避免插件每增加一种事件都必须修改核心枚举。等跨进程协议真正需要兼容多版
消费者时，再引入 schema registry；现在提前加入会增加维护成本，却没有实际验证对象。

## 验证证据

契约测试覆盖：

- 附件消息序列化往返；
- 路由身份校验；
- 事件版本、关联 ID、序号和 JSON 往返；
- 非法事件名拒绝；
- SSE cursor 与 event ID 分离；
- 旧导入路径映射到规范模型；
- Runtime JSONL 使用同一 `AgentEvent` 信封。
