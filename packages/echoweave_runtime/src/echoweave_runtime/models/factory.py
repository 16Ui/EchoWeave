from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from echoweave_runtime.config import (
    DEFAULT_MODEL_BY_PROVIDER,
    DEFAULT_PROVIDER_CAPABILITIES,
    SUPPORTED_PROVIDERS,
    get_openai_compatible_client_kwargs,
)
from echoweave_runtime.models.anthropic import AnthropicModelClient
from echoweave_runtime.models.base import ModelClient
from echoweave_runtime.models.facade import ProviderModelFacade
from echoweave_runtime.models.openai import OpenAIModelClient


ProviderFactory = Callable[[str], ModelClient]


@dataclass(frozen=True)
class ProviderCapabilities:
    supports_generate: bool = True
    supports_complete: bool = True
    supports_stream: bool = True


@dataclass(frozen=True)
class ProviderRegistration:
    name: str
    factory: ProviderFactory
    default_model: str
    capabilities: ProviderCapabilities = ProviderCapabilities()


class ProviderRegistry:
    def __init__(
        self,
        registrations: dict[str, ProviderRegistration],
        supported_providers: tuple[str, ...],
    ) -> None:
        self._registrations = registrations
        self._supported_providers = supported_providers

    def get_capabilities(self, provider: str) -> ProviderCapabilities:
        registration = self._registrations.get(provider)
        if registration is None:
            raise ValueError(f"Unsupported provider: {provider}")
        return registration.capabilities

    def create_client(self, provider: str, model: str | None) -> ModelClient:
        if provider not in self._supported_providers:
            raise ValueError(f"Unsupported provider: {provider}")
        registration = self._registrations.get(provider)
        if registration is None:
            raise ValueError(f"Unsupported provider: {provider}")
        selected_model = model or registration.default_model
        return registration.factory(selected_model)


def _create_anthropic_model_client(model: str) -> ModelClient:
    return ProviderModelFacade(AnthropicModelClient(model=model))


def _create_openai_model_client(model: str) -> ModelClient:
    return ProviderModelFacade(OpenAIModelClient(model=model))


def _create_openai_compatible_model_client(provider: str) -> ProviderFactory:
    def factory(model: str) -> ModelClient:
        return ProviderModelFacade(
            OpenAIModelClient(
                model=model,
                **get_openai_compatible_client_kwargs(provider),
            )
        )

    return factory


def _provider_capabilities_for(provider: str) -> ProviderCapabilities:
    raw_capabilities = DEFAULT_PROVIDER_CAPABILITIES.get(provider)
    if not isinstance(raw_capabilities, dict):
        return ProviderCapabilities()
    return ProviderCapabilities(
        supports_generate=bool(raw_capabilities.get("supports_generate", True)),
        supports_complete=bool(raw_capabilities.get("supports_complete", True)),
        supports_stream=bool(raw_capabilities.get("supports_stream", True)),
    )


def _build_provider_registry() -> ProviderRegistry:
    registrations = {
        "anthropic": ProviderRegistration(
            name="anthropic",
            factory=_create_anthropic_model_client,
            default_model=DEFAULT_MODEL_BY_PROVIDER["anthropic"],
            capabilities=_provider_capabilities_for("anthropic"),
        ),
        "openai": ProviderRegistration(
            name="openai",
            factory=_create_openai_model_client,
            default_model=DEFAULT_MODEL_BY_PROVIDER["openai"],
            capabilities=_provider_capabilities_for("openai"),
        ),
    }
    for provider in ("openai-compatible", "deepseek", "openrouter", "siliconflow", "ollama"):
        registrations[provider] = ProviderRegistration(
            name=provider,
            factory=_create_openai_compatible_model_client(provider),
            default_model=DEFAULT_MODEL_BY_PROVIDER[provider],
            capabilities=_provider_capabilities_for(provider),
        )
    return ProviderRegistry(registrations=registrations, supported_providers=SUPPORTED_PROVIDERS)


DEFAULT_PROVIDER_REGISTRY = _build_provider_registry()


def create_model_client(provider: str, model: str | None) -> ModelClient:
    return DEFAULT_PROVIDER_REGISTRY.create_client(provider, model)


def get_provider_capabilities(provider: str) -> ProviderCapabilities:
    return DEFAULT_PROVIDER_REGISTRY.get_capabilities(provider)
