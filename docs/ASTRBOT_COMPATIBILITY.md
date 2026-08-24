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
| command / group / message / platform / permission filter | 已识别 | 下一阶段编译为 EchoWeave Handler Descriptor |
| LLM、Tool 与生命周期 Hooks | 已识别 | 报告警告，等待 Hook 语义映射 |
| `terminate()` | 规划兼容 | 将接入 Runtime Host 的逆序关闭 |
| `astrbot.core.*` | 阻断 | 要求插件改用公共 API 或专用桥接器 |
| 动态 import | 阻断 | 兼容加载器不接受不可静态判断的入口 |
| Plugin Pages / Web API | 未兼容 | 需要独立路由和权限模型，不能直接挂载 |
| 直接运行任意 AstrBot 插件 | 未兼容 | 必须先通过分析、权限声明和执行隔离 |

`metadata-compatible` 表示元数据、配置或 Skills 可被读取；`api-candidate` 表示使用了可映射的 AstrBot
公共 API；`blocked` 表示存在核心内部依赖、动态加载或结构错误。当前所有报告的 `execution_ready` 都是
`false`，防止把“可分析”误写成“可安全执行”。

## 推荐实现顺序

1. 把基础 command 和过滤器编译为 EchoWeave Handler Descriptor；
2. 提供最小 `AstrMessageEvent`、`MessageEventResult`、`Star`、`Context` 兼容外观；
3. 用 Runtime Host 管理插件初始化与 `terminate()`；
4. 增加插件权限清单、独立进程与超时/故障隔离；
5. 通过真实开源插件样本建立兼容回归集，再扩大 API 面。

## 官方依据

- 插件最小结构与 `terminate()`：https://docs.astrbot.app/en/dev/star/guides/simple.html
- 命令、过滤器和 Hooks：https://docs.astrbot.app/en/dev/star/guides/listen-message-event.html
- Manifest、版本范围和 Skills：https://docs.astrbot.app/en/dev/star/plugin-new.html
- 配置 Schema：https://docs.astrbot.app/en/dev/star/guides/plugin-config.html
- Pipeline Scheduler：https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/core/pipeline/scheduler.py
- Plugin Metadata：https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/core/star/star.py
