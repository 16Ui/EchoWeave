# EchoWeave Agent Runtime

EchoWeave Agent Runtime 管理可恢复 Turn、模型请求与工具副作用，使一次任务在故障后仍能判断所有权、已完成工作和安全恢复边界。

## Language

**Logical Turn**:
由一个用户请求发起、可包含多个执行 attempt 的稳定任务身份。
_Avoid_: Request, retry turn

**Execution Attempt**:
Logical Turn 的一次实际推进过程；恢复会创建新 attempt，但不会创建新的 Logical Turn。
_Avoid_: Retry, new turn

**Execution Owner**:
当前被授权推进某个 Logical Turn 的 Runtime 实例身份。
_Avoid_: Singleton, global worker

**Execution Lease**:
Execution Owner 对 Logical Turn 持有的、带过期时间的独占执行权。
_Avoid_: Lock, session ownership

**Heartbeat**:
Execution Owner 在 Lease 有效期内更新存活时间的信号；它证明进程仍在维护所有权，不证明外部副作用已经完成。
_Avoid_: Health check, progress event

**Takeover**:
旧 Lease 过期后，新 Execution Owner 以更高 fencing token 接管同一个 Logical Turn。
_Avoid_: Force retry, resume

**Fencing Token**:
Lease 每次重新授予时单调递增的代次，用于识别已经失去所有权的旧执行者。
_Avoid_: Attempt number, trace ID

**Tool Batch**:
模型在 Logical Turn 的一个稳定批次位置产生的有序 Tool Invocation 集合。
_Avoid_: Thread pool, parallel task list

**Indeterminate Invocation**:
已经开始、但缺少 durable outcome，且无法证明安全重放的 Tool Invocation。
_Avoid_: Failed tool, pending tool
