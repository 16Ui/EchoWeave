"""Model-provider boundary for EchoWeave.

This package names the model layer while re-exporting the embedded runtime
implementation for compatibility.
"""

from echoweave_ai.providers import (  # noqa: F401
    AIProviderRegistration,
    AIProviderRegistry,
    create_ai_model_from_profile,
    list_ai_providers,
    register_ai_providers_from_config,
    register_ai_provider,
    register_openai_compatible_provider,
)
from echoweave_runtime.models.factory import ProviderCapabilities, create_model_client, get_provider_capabilities  # noqa: F401

__all__ = [
    "AIProviderRegistration",
    "AIProviderRegistry",
    "ProviderCapabilities",
    "create_ai_model_from_profile",
    "create_model_client",
    "get_provider_capabilities",
    "list_ai_providers",
    "register_ai_providers_from_config",
    "register_ai_provider",
    "register_openai_compatible_provider",
]
