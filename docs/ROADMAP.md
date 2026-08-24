# EchoWeave 演进路线

EchoWeave 的主线是可扩展的个人 Agent Runtime。实习经验只用于增强工程可靠性，Coding Agent 作为内置能力继续深化，两者都不改变项目的个人产品定位。

## M0：项目迁移与统一命名（已完成）

- 从 UiDeus 当前实现迁移到 EchoWeave 仓库；
- 统一 Python 包、CLI、环境变量、Docker 服务和文档命名；
- 移除旧 UiBot 兼容层；
- 排除密钥、本地配置、用户状态、日志与会话沙箱；
- 保持完整测试集通过。

## M1：统一事件与生命周期

- [已完成] 建立 `InboundMessage` / `OutboundMessage` / `Attachment` 规范模型，移除主链重复 DTO；
- [已完成] 使用版本化 `AgentEvent` 统一 Web SSE 与 Runtime JSONL 信封，预留工具、流式、失败和取消事件；
- [已完成首个切片] 增加 Runtime Host，落实顺序启动、逆序关闭、失败回滚，并接入 Web Gateway；
- 将 Channel、Provider、Plugin 逐步接入统一生命周期；
- 将入口层与 Agent Loop 解耦；
- 为异常、取消、超时和重试建立统一结果模型。

## M2：插件与能力系统

- Plugin Manifest 声明版本、依赖、权限和支持平台；
- [已完成首个切片] AstrBot Manifest/配置/Skills 静态兼容检查与 API 使用分级，不执行未审查插件；
- [已完成基础运行桥] AstrBot command/filter 编译、独立 Worker、Runtime Host 生命周期和超时终止；
- [已完成权限切片] 插件能力声明与用户授权双重校验、隔离模式、环境白名单和协议限额；
- 支持安装、启停、热重载和失败隔离；
- 区分 Plugin、Skill、Tool 和 Agent Profile；
- 提供最小插件 SDK、示例插件和契约测试。

## M3：个人 Agent 工作台

- Web Chat 与管理面板；
- Provider、Channel、Plugin、Session 和权限配置；
- 会话级模型、Skill 与工作区绑定；
- Trace、错误、Token、延迟和工具轨迹查看。

## M4：旗舰 Coding Workspace

- 仓库级上下文构建和结构化任务状态；
- 受控文件修改、Shell、测试和 Git 工具；
- 高风险审批、Checkpoint、Resume 与失败恢复；
- 轨迹级 Eval 和可复现软件修复任务集。

## M5：可靠性与公开证据

- timeout、retry、circuit breaker 与降级契约；
- 插件和 Provider 故障注入；
- Golden Set、回归阈值和 CI 门禁；
- 架构说明、演示视频和性能/可靠性报告。
