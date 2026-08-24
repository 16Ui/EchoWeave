from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from echoweave_runtime.models.anthropic import AnthropicModelClient
from echoweave_runtime.models.base import ModelClient
from echoweave_runtime.models.demo import EchoTurnModelClient
from echoweave_runtime.models.facade import ProviderModelFacade
from echoweave_runtime.models.factory import ProviderCapabilities
from echoweave_runtime.models.openai import OpenAIModelClient


AIProviderFactory = Callable[[dict[str, Any], str | None], tuple[ModelClient, ProviderCapabilities | None]]


@dataclass(frozen=True)
class AIProviderRegistration:
    name: str
    factory: AIProviderFactory
    default_model: str | None = None
    aliases: tuple[str, ...] = ()
    capabilities: ProviderCapabilities | None = None
    description: str = ""


class AIProviderRegistry:
    def __init__(self) -> None:
        self._registrations: dict[str, AIProviderRegistration] = {}

    def register(self, registration: AIProviderRegistration) -> None:
        for name in (registration.name, *registration.aliases):
            self._registrations[_normalize_provider(name)] = registration

    def get(self, provider: str) -> AIProviderRegistration:
        normalized = _normalize_provider(provider)
        registration = self._registrations.get(normalized)
        if registration is None:
            raise ValueError(f"Unsupported provider: {provider}")
        return registration

    def create(
        self,
        profile: dict[str, Any],
        *,
        default_provider: str = "demo",
        default_model: str | None = None,
    ) -> tuple[ModelClient, ProviderCapabilities | None]:
        provider = str(profile.get("provider") or default_provider)
        registration = self.get(provider)
        model = _profile_model(profile, default_model or registration.default_model)
        return registration.factory(profile, model)

    def list(self) -> list[dict[str, Any]]:
        seen: set[int] = set()
        providers: list[dict[str, Any]] = []
        for registration in self._registrations.values():
            ident = id(registration)
            if ident in seen:
                continue
            seen.add(ident)
            providers.append(
                {
                    "name": registration.name,
                    "aliases": list(registration.aliases),
                    "default_model": registration.default_model,
                    "description": registration.description,
                    "capabilities": None
                    if registration.capabilities is None
                    else {
                        "supports_generate": registration.capabilities.supports_generate,
                        "supports_complete": registration.capabilities.supports_complete,
                        "supports_stream": registration.capabilities.supports_stream,
                    },
                }
            )
        return providers


def _normalize_provider(provider: str) -> str:
    return provider.lower().replace("_", "-").strip()


def _profile_model(profile: dict[str, Any], fallback: str | None) -> str | None:
    raw = profile["model"] if "model" in profile else fallback
    return str(raw) if raw else None


def _demo_factory(profile: dict[str, Any], model: str | None):
    return EchoTurnModelClient(), None


def _anthropic_factory(profile: dict[str, Any], model: str | None):
    _require_api_key(profile, "ANTHROPIC_API_KEY", "anthropic")
    return (
        ProviderModelFacade(AnthropicModelClient(model=model or "claude-opus-4-6")),
        ProviderCapabilities(supports_generate=True, supports_complete=False, supports_stream=True),
    )


def _openai_factory(profile: dict[str, Any], model: str | None):
    api_key = _require_api_key(profile, "OPENAI_API_KEY", "openai")
    return (
        ProviderModelFacade(
            OpenAIModelClient(
                model=model or "gpt-4.1",
                api_key=api_key,
                base_url=_base_url(profile, "OPENAI_BASE_URL", None),
            )
        ),
        ProviderCapabilities(supports_generate=True, supports_complete=False, supports_stream=True),
    )


def _openai_compatible_factory(provider: str, default_model: str, default_base_url: str | None, default_api_key_env: str):
    def factory(profile: dict[str, Any], model: str | None):
        api_key = _api_key(profile, default_api_key_env)
        if provider == "ollama" and not api_key:
            api_key = "ollama"
        if not api_key:
            env_name = _api_key_env(profile, default_api_key_env)
            raise ValueError(
                f"模型 provider '{provider}' 需要 API key。请设置环境变量 {env_name}，"
                f"或在该 model profile 中配置 api_key/api_key_env。"
            )
        return (
            ProviderModelFacade(
                OpenAIModelClient(
                    model=model or default_model,
                    api_key=api_key,
                    base_url=_base_url(profile, f"{provider.upper().replace('-', '_')}_BASE_URL", default_base_url),
                )
            ),
            ProviderCapabilities(supports_generate=True, supports_complete=False, supports_stream=True),
        )

    return factory


def _api_key(profile: dict[str, Any], default_env: str) -> str | None:
    raw_api_key = profile.get("api_key")
    if isinstance(raw_api_key, str) and raw_api_key:
        return raw_api_key
    return os.getenv(_api_key_env(profile, default_env))


def _api_key_env(profile: dict[str, Any], default_env: str) -> str:
    env_name = profile.get("api_key_env")
    if not isinstance(env_name, str) or not env_name:
        env_name = default_env
    return env_name


def _require_api_key(profile: dict[str, Any], default_env: str, provider: str) -> str:
    api_key = _api_key(profile, default_env)
    if not api_key:
        env_name = _api_key_env(profile, default_env)
        raise ValueError(
            f"模型 provider '{provider}' 需要 API key。请设置环境变量 {env_name}，"
            f"或在该 model profile 中配置 api_key/api_key_env。"
        )
    return api_key


def _base_url(profile: dict[str, Any], env_name: str, fallback: str | None) -> str | None:
    raw_base_url = profile.get("base_url")
    if isinstance(raw_base_url, str) and raw_base_url:
        return raw_base_url
    return os.getenv(env_name) or fallback


DEFAULT_AI_PROVIDER_REGISTRY = AIProviderRegistry()
DEFAULT_AI_PROVIDER_REGISTRY.register(
    AIProviderRegistration(
        name="demo",
        aliases=("echo",),
        factory=_demo_factory,
        description="Local echo/demo provider for development and tests.",
    )
)
DEFAULT_AI_PROVIDER_REGISTRY.register(
    AIProviderRegistration(
        name="anthropic",
        default_model="claude-opus-4-6",
        factory=_anthropic_factory,
        capabilities=ProviderCapabilities(supports_generate=True, supports_complete=False, supports_stream=True),
        description="Anthropic Claude provider.",
    )
)
DEFAULT_AI_PROVIDER_REGISTRY.register(
    AIProviderRegistration(
        name="openai",
        default_model="gpt-4.1",
        factory=_openai_factory,
        capabilities=ProviderCapabilities(supports_generate=True, supports_complete=False, supports_stream=True),
        description="OpenAI provider.",
    )
)
for _provider, _model, _base, _env in (
    ("openai-compatible", "gpt-4.1", None, "OPENAI_API_KEY"),
    ("deepseek", "deepseek-chat", "https://api.deepseek.com", "DEEPSEEK_API_KEY"),
    ("openrouter", "openai/gpt-4.1", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    ("siliconflow", "deepseek-ai/DeepSeek-V3", "https://api.siliconflow.cn/v1", "SILICONFLOW_API_KEY"),
    ("ollama", "qwen2.5-coder:7b", "http://127.0.0.1:11434/v1", "OLLAMA_API_KEY"),
):
    DEFAULT_AI_PROVIDER_REGISTRY.register(
        AIProviderRegistration(
            name=_provider,
            default_model=_model,
            factory=_openai_compatible_factory(_provider, _model, _base, _env),
            capabilities=ProviderCapabilities(supports_generate=True, supports_complete=False, supports_stream=True),
            description="OpenAI-compatible chat completions provider.",
        )
    )


def register_ai_provider(registration: AIProviderRegistration) -> None:
    DEFAULT_AI_PROVIDER_REGISTRY.register(registration)


def register_openai_compatible_provider(
    name: str,
    *,
    default_model: str,
    base_url: str | None = None,
    api_key_env: str = "OPENAI_API_KEY",
    aliases: tuple[str, ...] = (),
    description: str = "Declarative OpenAI-compatible provider.",
) -> None:
    DEFAULT_AI_PROVIDER_REGISTRY.register(
        AIProviderRegistration(
            name=name,
            aliases=aliases,
            default_model=default_model,
            factory=_openai_compatible_factory(name, default_model, base_url, api_key_env),
            capabilities=ProviderCapabilities(supports_generate=True, supports_complete=False, supports_stream=True),
            description=description,
        )
    )


def register_ai_providers_from_config(config: dict[str, Any] | None) -> None:
    if not isinstance(config, dict):
        return
    for name, raw in config.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(raw, dict):
            continue
        provider_type = str(raw.get("type") or raw.get("kind") or "openai-compatible").lower()
        if provider_type not in {"openai-compatible", "openai_compatible", "openai"}:
            continue
        default_model = str(raw.get("default_model") or raw.get("model") or "").strip()
        if not default_model:
            continue
        aliases = raw.get("aliases")
        register_openai_compatible_provider(
            name.strip(),
            default_model=default_model,
            base_url=str(raw.get("base_url")) if raw.get("base_url") else None,
            api_key_env=str(raw.get("api_key_env") or "OPENAI_API_KEY"),
            aliases=tuple(str(alias).strip() for alias in aliases if str(alias).strip()) if isinstance(aliases, list) else (),
            description=str(raw.get("description") or "Configured from EchoWeave admin panel."),
        )


def list_ai_providers() -> list[dict[str, Any]]:
    return DEFAULT_AI_PROVIDER_REGISTRY.list()


def create_ai_model_from_profile(
    profile: dict[str, Any],
    *,
    default_provider: str = "demo",
    default_model: str | None = None,
) -> tuple[ModelClient, ProviderCapabilities | None]:
    return DEFAULT_AI_PROVIDER_REGISTRY.create(
        profile,
        default_provider=default_provider,
        default_model=default_model,
    )
