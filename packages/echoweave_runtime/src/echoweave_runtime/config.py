from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


CLAUDE_CODE_CONFIG_PATH = Path.home() / ".claude" / "config.json"
CLAUDE_CODE_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
ANTHROPIC_ENV_KEYS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")
OPENAI_ENV_KEYS = ("OPENAI_API_KEY", "OPENAI_BASE_URL")
ECHOWEAVE_ENV_KEYS = (
    "ECHOWEAVE_PROVIDER",
    "ECHOWEAVE_MODEL",
    "ECHOWEAVE_ASSISTANT_NAME",
    "ECHOWEAVE_COMPACT_KEEP_TAIL",
    "ECHOWEAVE_EXPORT_DEFAULT_KIND",
    "ECHOWEAVE_MANIFEST_PATH",
    "ECHOWEAVE_MEMORY_EXACT_MATCH_WEIGHT",
    "ECHOWEAVE_MEMORY_TOKEN_OVERLAP_WEIGHT",
    "ECHOWEAVE_MEMORY_RECENCY_WEIGHT",
    "ECHOWEAVE_TOOL_EXECUTION_MODE",
)
SUPPORTED_PROVIDERS = (
    "anthropic",
    "openai",
    "openai-compatible",
    "deepseek",
    "openrouter",
    "siliconflow",
    "ollama",
)
DEFAULT_PROVIDER = "anthropic"
DEFAULT_MODEL_BY_PROVIDER = {
    "anthropic": "claude-opus-4-6",
    "openai": "gpt-4.1",
    "openai-compatible": "gpt-4.1",
    "deepseek": "deepseek-chat",
    "openrouter": "openai/gpt-4.1",
    "siliconflow": "deepseek-ai/DeepSeek-V3",
    "ollama": "qwen2.5-coder:7b",
}
DEFAULT_PROVIDER_CAPABILITIES = {
    "anthropic": {
        "supports_generate": True,
        "supports_complete": False,
        "supports_stream": True,
    },
    "openai": {
        "supports_generate": True,
        "supports_complete": False,
        "supports_stream": True,
    },
    "openai-compatible": {
        "supports_generate": True,
        "supports_complete": False,
        "supports_stream": True,
    },
    "deepseek": {
        "supports_generate": True,
        "supports_complete": False,
        "supports_stream": True,
    },
    "openrouter": {
        "supports_generate": True,
        "supports_complete": False,
        "supports_stream": True,
    },
    "siliconflow": {
        "supports_generate": True,
        "supports_complete": False,
        "supports_stream": True,
    },
    "ollama": {
        "supports_generate": True,
        "supports_complete": False,
        "supports_stream": True,
    },
}
DEFAULT_COMPACT_KEEP_TAIL = 8
DEFAULT_EXPORT_DEFAULT_KIND = "snapshot"
DEFAULT_MANIFEST_PATH = ".echoweave-packages.json"
DEFAULT_MEMORY_EXACT_MATCH_WEIGHT = 1.0
DEFAULT_MEMORY_TOKEN_OVERLAP_WEIGHT = 1.0
DEFAULT_MEMORY_RECENCY_WEIGHT = 0.15
DEFAULT_TOOL_EXECUTION_MODE = "sequential"
SUPPORTED_TOOL_EXECUTION_MODES = ("sequential", "parallel", "streaming")
SUPPORTED_EXPORT_KINDS = ("events", "snapshot", "tree", "task_graph")


@dataclass(frozen=True)
class RuntimeSettings:
    provider: str
    model: str
    compact_keep_tail: int
    tool_execution_mode: str
    export_default_kind: str
    manifest_path: Path
    memory_exact_match_weight: float
    memory_token_overlap_weight: float
    memory_recency_weight: float


def _pick_string(*values: object) -> str | None:
    for value in values:
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
    return None


def _coerce_positive_int(value: object, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _coerce_non_negative_float(value: object, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0.0 else default


def _normalize_manifest_path(cwd: Path, value: object) -> Path:
    raw = _pick_string(value)
    path = Path(raw or DEFAULT_MANIFEST_PATH).expanduser()
    if not path.is_absolute():
        path = (cwd / path).resolve()
    return path


def resolve_settings(
    cwd: Path,
    cli_provider: str | None = None,
    cli_model: str | None = None,
    session_overrides: dict[str, Any] | None = None,
) -> RuntimeSettings:
    overrides = session_overrides or {}

    provider = (
        _pick_string(
            overrides.get("provider"),
            cli_provider,
            os.getenv("ECHOWEAVE_PROVIDER"),
        )
        or DEFAULT_PROVIDER
    ).lower()
    if provider not in SUPPORTED_PROVIDERS:
        provider = DEFAULT_PROVIDER

    provider_model_env = os.getenv("ANTHROPIC_MODEL") if provider == "anthropic" else None
    model = (
        _pick_string(
            overrides.get("model"),
            cli_model,
            os.getenv("ECHOWEAVE_MODEL"),
            provider_model_env,
        )
        or DEFAULT_MODEL_BY_PROVIDER[provider]
    )

    compact_keep_tail = _coerce_positive_int(
        overrides.get(
            "compact_keep_tail",
            os.getenv("ECHOWEAVE_COMPACT_KEEP_TAIL"),
        ),
        DEFAULT_COMPACT_KEEP_TAIL,
    )

    tool_execution_mode = (
        _pick_string(
            overrides.get("tool_execution_mode"),
            os.getenv("ECHOWEAVE_TOOL_EXECUTION_MODE"),
        )
        or DEFAULT_TOOL_EXECUTION_MODE
    ).lower()
    if tool_execution_mode not in SUPPORTED_TOOL_EXECUTION_MODES:
        tool_execution_mode = DEFAULT_TOOL_EXECUTION_MODE

    export_default_kind = (
        _pick_string(
            overrides.get("export_default_kind"),
            os.getenv("ECHOWEAVE_EXPORT_DEFAULT_KIND"),
        )
        or DEFAULT_EXPORT_DEFAULT_KIND
    ).lower()
    if export_default_kind not in SUPPORTED_EXPORT_KINDS:
        export_default_kind = DEFAULT_EXPORT_DEFAULT_KIND

    manifest_path = _normalize_manifest_path(
        cwd,
        overrides.get(
            "manifest_path",
            os.getenv("ECHOWEAVE_MANIFEST_PATH"),
        ),
    )

    memory_exact_match_weight = _coerce_non_negative_float(
        overrides.get(
            "memory_exact_match_weight",
            os.getenv("ECHOWEAVE_MEMORY_EXACT_MATCH_WEIGHT"),
        ),
        DEFAULT_MEMORY_EXACT_MATCH_WEIGHT,
    )
    memory_token_overlap_weight = _coerce_non_negative_float(
        overrides.get(
            "memory_token_overlap_weight",
            os.getenv("ECHOWEAVE_MEMORY_TOKEN_OVERLAP_WEIGHT"),
        ),
        DEFAULT_MEMORY_TOKEN_OVERLAP_WEIGHT,
    )
    memory_recency_weight = _coerce_non_negative_float(
        overrides.get(
            "memory_recency_weight",
            os.getenv("ECHOWEAVE_MEMORY_RECENCY_WEIGHT"),
        ),
        DEFAULT_MEMORY_RECENCY_WEIGHT,
    )

    return RuntimeSettings(
        provider=provider,
        model=model,
        compact_keep_tail=compact_keep_tail,
        tool_execution_mode=tool_execution_mode,
        export_default_kind=export_default_kind,
        manifest_path=manifest_path,
        memory_exact_match_weight=memory_exact_match_weight,
        memory_token_overlap_weight=memory_token_overlap_weight,
        memory_recency_weight=memory_recency_weight,
    )


def load_env(cwd: Path) -> None:
    load_dotenv(cwd / ".env")
    load_dotenv()
    load_claude_code_env()


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _load_env_mapping(data: dict[str, Any]) -> None:
    env = data.get("env")
    if not isinstance(env, dict):
        return

    for key in (*ANTHROPIC_ENV_KEYS, "ANTHROPIC_MODEL", *OPENAI_ENV_KEYS, *ECHOWEAVE_ENV_KEYS):
        value = env.get(key)
        if isinstance(value, str) and value:
            os.environ.setdefault(key, value)


def load_claude_code_env() -> None:
    config_data = _read_json_object(CLAUDE_CODE_CONFIG_PATH)
    settings_data = _read_json_object(CLAUDE_CODE_SETTINGS_PATH)

    _load_env_mapping(config_data)
    _load_env_mapping(settings_data)

    primary_api_key = config_data.get("primaryApiKey")
    if (
        isinstance(primary_api_key, str)
        and primary_api_key
        and not os.getenv("ANTHROPIC_API_KEY")
        and not os.getenv("ANTHROPIC_AUTH_TOKEN")
    ):
        os.environ["ANTHROPIC_API_KEY"] = primary_api_key


def get_anthropic_client_kwargs() -> dict[str, str]:
    kwargs: dict[str, str] = {}
    for key, field in (
        ("ANTHROPIC_API_KEY", "api_key"),
        ("ANTHROPIC_AUTH_TOKEN", "auth_token"),
        ("ANTHROPIC_BASE_URL", "base_url"),
    ):
        value = os.getenv(key)
        if value:
            kwargs[field] = value
    return kwargs


def get_openai_client_kwargs() -> dict[str, str]:
    kwargs: dict[str, str] = {}
    for key, field in (
        ("OPENAI_API_KEY", "api_key"),
        ("OPENAI_BASE_URL", "base_url"),
    ):
        value = os.getenv(key)
        if value:
            kwargs[field] = value
    return kwargs


def get_openai_compatible_client_kwargs(provider: str) -> dict[str, str]:
    normalized = provider.lower().replace("_", "-")
    defaults = {
        "openai-compatible": {"base_url": os.getenv("OPENAI_BASE_URL"), "api_key_env": "OPENAI_API_KEY"},
        "deepseek": {"base_url": "https://api.deepseek.com", "api_key_env": "DEEPSEEK_API_KEY"},
        "openrouter": {"base_url": "https://openrouter.ai/api/v1", "api_key_env": "OPENROUTER_API_KEY"},
        "siliconflow": {"base_url": "https://api.siliconflow.cn/v1", "api_key_env": "SILICONFLOW_API_KEY"},
        "ollama": {"base_url": "http://127.0.0.1:11434/v1", "api_key_env": "OLLAMA_API_KEY"},
    }
    config = defaults.get(normalized, defaults["openai-compatible"])
    env_prefix = normalized.upper().replace("-", "_")
    api_key = os.getenv(str(config["api_key_env"]))
    if normalized == "ollama" and not api_key:
        api_key = "ollama"
    base_url = os.getenv(f"{env_prefix}_BASE_URL") or config["base_url"]
    kwargs: dict[str, str] = {}
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = str(base_url)
    return kwargs


def has_anthropic_credentials() -> bool:
    kwargs = get_anthropic_client_kwargs()
    return bool(kwargs.get("api_key") or kwargs.get("auth_token"))


def has_openai_credentials() -> bool:
    kwargs = get_openai_client_kwargs()
    return bool(kwargs.get("api_key"))


def has_provider_credentials(provider: str) -> bool:
    if provider == "anthropic":
        return has_anthropic_credentials()
    if provider == "openai":
        return has_openai_credentials()
    if provider in {"openai-compatible", "deepseek", "openrouter", "siliconflow", "ollama"}:
        return bool(get_openai_compatible_client_kwargs(provider).get("api_key"))
    return False


def get_agent_home() -> Path:
    return Path.home() / ".echoweave"


def get_sessions_dir() -> Path:
    return get_agent_home() / "sessions"
