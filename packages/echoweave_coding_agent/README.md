# echoweave_coding_agent

`echoweave_coding_agent` 是 EchoWeave 的本地 AI Coding Agent 应用层。

它负责把本地工作区、工具注册表、session store、扩展 provider 和 `AgentCore`
组装成可直接使用的 Coding Agent。

## 职责

- `CodingAgentConfig`：声明 workspace、model client、session store、RAG/extension、审批回调等配置。
- `CodingAgent`：本地代码工作入口。
- `run(prompt)`：执行一轮本地编码任务。
- `list_sessions()`、`create_checkpoint()`、`replay_from_checkpoint()`：会话和回放能力。
- `echoweave_coding_agent.cli`：本地 coding-agent CLI、TUI、session browser、eval、
  package 管理等应用级命令。旧的 `echoweave_runtime.cli` 只保留兼容转发。

底层文件/命令工具、模型 SDK、RAG provider 仍由 `echoweave_runtime` 提供；Agent 编排由
`echoweave_agent_core` 提供。

## 示例

```python
from pathlib import Path

from echoweave_coding_agent import CodingAgent, CodingAgentConfig
from echoweave_runtime.models.demo import AgentResponse, SequenceModelClient

agent = CodingAgent.from_config(
    CodingAgentConfig(
        workspace=Path("D:/games/EchoWeave"),
        model_client=SequenceModelClient([AgentResponse(text="ok")]),
    )
)

result = agent.run("查看当前项目结构", resume=False)
print(result.text)
```

## CLI

安装为 console script 后可使用：

```powershell
echoweave-coding run --cwd D:\games\EchoWeave --prompt "查看项目结构"
```

源码环境可使用：

```powershell
python -m echoweave_coding_agent.cli run --cwd D:\games\EchoWeave --prompt "查看项目结构"
```

## English Brief

`echoweave_coding_agent` is the local AI coding application layer. It composes a
workspace, tools, sessions, extensions, and `AgentCore` into a usable coding
agent.
