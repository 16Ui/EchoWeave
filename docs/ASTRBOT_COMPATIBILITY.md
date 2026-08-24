# AstrBot 设计吸收与插件兼容

调研基线：AstrBot 官方文档与 `AstrBotDevs/AstrBot` 主分支，2026-08-24。

## 值得吸收的设计

### 分阶段异步 Pipeline

AstrBot 将消息处理拆成顺序 Stage，并允许事件停止传播。支持前后处理的 Stage 使用异步生成器形成近似
洋葱模型。这种设计适合权限、唤醒判断、插件处理、LLM 和回复装饰等横切逻辑。

EchoWeave 应吸收“显式阶段 + 可停止传播”，但不复制“是否实际 yield 决定后续 Stage 是否运行”的隐式
控制流。EchoWeave 的阶段结果应明确返回 `continue / stop / fail`，后续再接入统一结果模型。

### 声明式过滤器和命令

AstrBot 用装饰器声明 command、command group、消息类型、平台与权限过滤器，多个过滤器使用 AND 语义。
插件作者只描述处理条件，调度器负责匹配，开发体验明显优于插件自行解析全部消息。

EchoWeave 将兼容这些声明的语义，但内部应编译为自己的 Handler Descriptor，不把 AstrBot 的全局注册表
直接带入 Runtime。

### Context 能力门面

插件通过 Context 获取发送消息、Provider、LLM Tool、配置和存储等能力。它把插件与大量核心对象隔开，
是良好的兼容接缝。

EchoWeave 对应设计应是 capability-scoped `PluginContext`：只给插件声明并获批的能力，不暴露整个 Runtime。

### Manifest、配置 Schema 与生命周期

AstrBot 使用 `metadata.yaml` 描述插件身份、版本、仓库、支持平台和 AstrBot 版本范围；使用
`_conf_schema.json` 驱动配置；插件可实现异步 `terminate()`；插件也可以捆绑 Skills。这些约定适合生态化，
应优先兼容。

## 不直接复制的部分

- 不在兼容性检查阶段 import 插件；Python 顶层代码可能立即执行。
- 不允许插件直接依赖 `astrbot.core.*`；这会把内部路径当作公共协议，重载和升级容易失效。
- 不自动把插件 `requirements.txt` 安装进 EchoWeave 主环境；后续应使用独立环境或进程。
- 不使用模块路径前缀作为插件身份；身份固定为官方市场规范中的 `author/name`。
- 不宣称未知插件“完全兼容”；扫描结果和运行能力分开表达。

## 当前兼容矩阵

| AstrBot 能力 | 当前状态 | EchoWeave 行为 |
|---|---|---|
| `metadata.yaml` | 已兼容 | 安全解析身份、版本、仓库、平台和版本约束，保留未知字段 |
| `_conf_schema.json` | 已兼容基础格式 | 加载前验证 JSON object，不执行 UI 特殊字段 |
| 插件 `skills/**/SKILL.md` | 已识别 | 作为只读资源发现，尚未自动启用 |
| `astrbot.api.*` import | 已识别 | 进入 API 兼容候选，不代表可执行 |
| command / group / message / platform / permission filter | 基础子集可执行 | 编译为 `AstrBotHandlerDescriptor`；支持基础命令、别名、参数和单值过滤器 |
| LLM、Tool 与生命周期 Hooks | 已识别 | 报告警告，等待 Hook 语义映射 |
| `initialize()` / `terminate()` | 已兼容 | 独立 Worker 随 Runtime Host 启停并逆序关闭 |
| `astrbot.core.*` | 阻断 | 要求插件改用公共 API 或专用桥接器 |
| 动态 import | 阻断 | 兼容加载器不接受不可静态判断的入口 |
| Plugin Pages / Web API | 未兼容 | 需要独立路由和权限模型，不能直接挂载 |
| 通过检查的基础命令插件 | 显式启用后可运行 | `allow_execution=True` 后在独立 Worker 进程执行 |
| 任意 AstrBot 插件 | 未兼容 | 高级 Context、Provider、消息组件、Web API 和未知 Hook 仍不执行 |

`metadata-compatible` 表示元数据、配置或 Skills 可被读取；`api-candidate` 表示使用了可映射的 AstrBot
公共 API；`blocked` 表示存在核心内部依赖、动态加载或结构错误。当前所有报告的 `execution_ready` 都是
`false`，防止把“可分析”误写成“可安全执行”。实际运行必须另外构造 `AstrBotPluginProcess` 并显式传入
`allow_execution=True`。

## 最小运行桥

基础兼容插件在独立 Python Worker 中运行：

```text
InboundMessage
  -> JSON request + request_id
  -> AstrBot Worker
  -> AstrMessageEvent
  -> AstrBotHandlerDescriptor filters
  -> async handler / async generator
  -> MessageEventResult
  -> JSON response
  -> OutboundMessage
```

Worker 提供最小 `Star`、`Context`、`AstrMessageEvent`、`MessageEventResult`、`filter` 和 `register`
外观，因此官方 helloworld 风格插件无需修改 import。插件 stdout/stderr 与协议通道分离，Unicode 通过
ASCII JSON 转义跨越 Windows 代码页；请求超时会终止 Worker。

独立进程是故障边界，不是安全沙箱。插件仍继承当前用户的文件和网络权限，所以未经信任的插件不能仅凭
静态检查结果执行。下一阶段需要权限清单和受限进程环境。

## 权限与受限进程

插件通过根目录下的 `echoweave.permissions.json` 声明敏感能力：

```json
{
  "schema_version": 1,
  "capabilities": ["network", "filesystem-write"]
}
```

当前能力包括 `network`、`filesystem-write`、`host-process`、`native-code` 和 `environment`。启动需要
同时满足：静态分析发现的能力已在插件 Manifest 声明，并且用户通过 `granted_capabilities` 明确授权。
声明不等于授权，授权也不能替代插件声明。

能力分析覆盖插件目录内全部 Python 源文件，而不只检查 `main.py`；`.git`、虚拟环境、缓存和
`node_modules` 等非插件源码目录会被排除，符号链接也不能逃出插件根目录。

Worker 使用 Python isolated mode (`-I`) 启动，工作目录固定为插件根目录。宿主环境只传递启动所需的
系统和临时目录变量；API Key、Token 和其他业务环境变量默认不继承。插件确需环境配置时，必须声明并
获批 `environment`，且只能收到调用者通过 `plugin_environment` 显式传入的键值。

JSON 协议对请求和响应分别设置大小上限，防止插件用异常大的消息占满通道；单次执行超时仍会终止整个
Worker，避免超时任务继续在后台运行。

这些控制属于应用层策略和最小权限启动，并不能拦截插件绕过静态分析后直接调用操作系统 API。完整执行
不可信插件仍需要 Windows AppContainer、容器或独立低权限账户等 OS 级边界。

## 推荐实现顺序

1. 映射消息组件以及 `event.send()` 的主动发送语义；
2. 映射 LLM/Tool Hooks，保持 EchoWeave Hook 的结构化结果；
3. 接入可选 OS 级沙箱后，再允许来源不完全可信的插件；
4. 通过更多真实开源插件样本建立兼容回归集，再扩大 API 面；
5. 最后评估 Plugin Pages 和 Web API，不让插件直接共享管理端权限。

## 官方依据

- 插件最小结构与 `terminate()`：https://docs.astrbot.app/en/dev/star/guides/simple.html
- 命令、过滤器和 Hooks：https://docs.astrbot.app/en/dev/star/guides/listen-message-event.html
- Manifest、版本范围和 Skills：https://docs.astrbot.app/en/dev/star/plugin-new.html
- 配置 Schema：https://docs.astrbot.app/en/dev/star/guides/plugin-config.html
- Pipeline Scheduler：https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/core/pipeline/scheduler.py
- Plugin Metadata：https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/core/star/star.py
