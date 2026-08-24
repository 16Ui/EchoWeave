from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Protocol


ExtensionHook = Literal[
    "before_retrieval",
    "after_retrieval",
    "before_memory",
    "after_memory",
    "before_provider_request",
    "after_provider_response",
    "before_tool_call",
    "after_tool_result",
    "beforeToolCall",
    "afterToolCall",
]

ALL_EXTENSION_HOOKS: tuple[ExtensionHook, ...] = (
    "before_retrieval",
    "after_retrieval",
    "before_memory",
    "after_memory",
    "before_provider_request",
    "after_provider_response",
    "before_tool_call",
    "after_tool_result",
    "beforeToolCall",
    "afterToolCall",
)


@dataclass(frozen=True)
class SkillSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    source: str = "builtin"


@dataclass(frozen=True)
class McpServerConfig:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = 10.0


@dataclass(frozen=True)
class RetrievalChunk:
    source: str
    text: str
    score: float


@dataclass(frozen=True)
class MemoryChunk:
    source: str
    text: str
    score: float


ToolSource = Literal["builtin", "workspace", "user", "extension"]
ToolConflictPolicy = Literal["builtin_wins", "extension_wins", "error_on_conflict"]


@dataclass(frozen=True)
class ExtensionToolSpec:
    tool: Any
    source: ToolSource = "extension"
    conflict_policy: ToolConflictPolicy | None = None


@dataclass
class ExtensionContext:
    session_id: str | None = None
    turn_id: str | None = None
    trace_id: str | None = None
    event_id: str | None = None
    parent_event_id: str | None = None
    turn_input: str = ""
    messages: list[dict[str, Any]] | None = None
    provider_request: dict[str, Any] | None = None
    provider_response: dict[str, Any] | None = None
    tool_call: dict[str, Any] | None = None
    tool_result: Any = None
    tool_execution_mode: str | None = None
    tool_batch: dict[str, Any] | None = None
    retrieval_hits: list[dict[str, Any]] | None = None
    memory_hits: list[dict[str, Any]] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


ExtensionHookHandler = Callable[[ExtensionContext], ExtensionContext | None]


class SkillProvider(Protocol):
    def list_skills(self) -> list[SkillSpec]:
        ...

    def execute(self, name: str, arguments: dict[str, Any]) -> str:
        ...


class McpProvider(Protocol):
    def list_servers(self) -> list[McpServerConfig]:
        ...

    def call(self, server: str, method: str, params: dict[str, Any] | None = None) -> str:
        ...


class RetrievalProvider(Protocol):
    def retrieve(self, query: str, top_k: int = 3) -> list[RetrievalChunk]:
        ...


class MemoryProvider(Protocol):
    def retrieve(self, query: str, history: list[dict[str, Any]] | None = None, top_k: int = 3) -> list[MemoryChunk]:
        ...

    def remember(self, text: str, metadata: dict[str, Any] | None = None) -> None:
        ...
