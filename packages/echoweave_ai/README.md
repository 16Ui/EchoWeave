# echoweave_ai

`echoweave_ai` 是 EchoWeave 的多平台 AI provider 适配层。

它提供注册表形式的模型接入：

- `AIProviderRegistration`：声明 provider 名称、别名、默认模型、factory 和能力。
- `register_ai_provider()`：运行时注册新的 AI provider。
- `register_openai_compatible_provider()`：注册 OpenAI-compatible 平台。
- `register_ai_providers_from_config()`：从配置或 Web 管理端 JSON 注册声明式 provider。
- `create_ai_model_from_profile()`：从 `model_profiles` 的配置字典创建模型客户端。
- `list_ai_providers()`：列出当前可用 provider。

内置 provider：

- `demo`
- `anthropic`
- `openai`
- `openai-compatible`
- `deepseek`
- `openrouter`
- `siliconflow`
- `ollama`

示例：

```python
from echoweave_ai import AIProviderRegistration, ProviderCapabilities, register_ai_provider

register_ai_provider(
    AIProviderRegistration(
        name="my-provider",
        aliases=("my-openai-compatible",),
        default_model="my-model",
        factory=lambda profile, model: (client, ProviderCapabilities()),
    )
)
```

社交平台、Web 工作台和本地 coding-agent 可以继续使用 `model_profiles`，新增 provider 后
只需要在 profile 中写入对应 `provider` 名称。
推荐使用清晰 profile 名，例如 `deepseek-chat`、`openai-gpt-4.1-mini`、
`ollama-qwen-coder`，并补充 `label` 和 `description` 供 Web 下拉框展示。

Web 管理端支持填写 `AI providers JSON`，适合注册 DeepSeek 兼容网关、OpenRouter、
SiliconFlow、本地 Ollama、LM Studio、vLLM 或公司内部 OpenAI-compatible 服务：

```json
{
  "local-lmstudio": {
    "type": "openai-compatible",
    "base_url": "http://127.0.0.1:1234/v1",
    "api_key_env": "LOCAL_LLM_API_KEY",
    "default_model": "qwen2.5-coder:7b"
  }
}
```

任意 Python factory 仍应通过 `register_ai_provider()` 在代码侧注册，网页端只负责安全的
声明式 provider 配置。

## English Brief

`echoweave_ai` owns the model-provider registry for EchoWeave. New providers can be
registered with `register_ai_provider()` and then selected through
`model_profiles`.
