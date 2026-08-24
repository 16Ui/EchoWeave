from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from enum import Enum
from types import SimpleNamespace
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class FilterDeclaration:
    kind: str
    value: Any = None
    options: dict[str, Any] = field(default_factory=dict)


def _declare(handler: Callable[..., Any], declaration: FilterDeclaration) -> Callable[..., Any]:
    declarations = list(getattr(handler, "__echoweave_astrbot_filters__", ()))
    declarations.append(declaration)
    setattr(handler, "__echoweave_astrbot_filters__", tuple(declarations))
    return handler


class EventMessageType(str, Enum):
    ALL = "all"
    PRIVATE_MESSAGE = "private"
    GROUP_MESSAGE = "group"


class PermissionType(str, Enum):
    MEMBER = "member"
    ADMIN = "admin"


class PlatformAdapterType(str, Enum):
    ALL = "all"
    AIOCQHTTP = "aiocqhttp"
    LARK = "lark"
    QQOFFICIAL = "qq_official"
    WEBCHAT = "webchat"
    WEIXIN_OFFICIAL_ACCOUNT = "weixin_official_account"


class _CommandGroup:
    def __init__(self, name: str) -> None:
        self.name = name

    def __call__(self, handler: Callable[..., Any]) -> Callable[..., Any]:
        return _declare(handler, FilterDeclaration("command_group", self.name))

    def command(self, name: str, **options: Any):
        return _command_decorator(f"{self.name} {name}", options)

    def group(self, name: str) -> "_CommandGroup":
        return _CommandGroup(f"{self.name} {name}")


def _command_decorator(name: str, options: dict[str, Any]):
    aliases = options.get("alias", ())
    if isinstance(aliases, str):
        aliases = (aliases,)

    def decorator(handler: Callable[..., Any]) -> Callable[..., Any]:
        return _declare(
            handler,
            FilterDeclaration(
                "command",
                " ".join(name.strip().split()),
                {"aliases": tuple(str(item) for item in aliases)},
            ),
        )

    return decorator


class FilterFacade:
    EventMessageType = EventMessageType
    PermissionType = PermissionType
    PlatformAdapterType = PlatformAdapterType

    @staticmethod
    def command(name: str, **options: Any):
        return _command_decorator(name, options)

    @staticmethod
    def command_group(name: str) -> _CommandGroup:
        return _CommandGroup(name)

    @staticmethod
    def event_message_type(value: EventMessageType):
        return lambda handler: _declare(handler, FilterDeclaration("message_type", value.value))

    @staticmethod
    def permission_type(value: PermissionType):
        return lambda handler: _declare(handler, FilterDeclaration("permission", value.value))

    @staticmethod
    def platform_adapter_type(value: PlatformAdapterType):
        return lambda handler: _declare(handler, FilterDeclaration("platform", value.value))


filter = FilterFacade()


@dataclass(frozen=True, slots=True)
class MessageEventResult:
    text: str
    result_type: str = "plain"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "result_type": self.result_type, "metadata": self.metadata}


@dataclass(frozen=True, slots=True)
class AstrBotHandlerDescriptor:
    name: str
    handler: Callable[..., Any]
    filters: tuple[FilterDeclaration, ...]

    @property
    def command(self) -> FilterDeclaration | None:
        return next((item for item in self.filters if item.kind == "command"), None)


class AstrMessageEvent:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.platform = str(payload.get("platform") or "astrbot")
        self.unified_msg_origin = str(payload.get("conversation_id") or "astrbot:unknown")
        self.session_id = self.unified_msg_origin
        self.message_str = str(payload.get("text") or "")
        self.sender_id = str(payload.get("sender_id") or "unknown")
        self.message_id = payload.get("message_id")
        self.raw = dict(payload.get("raw") or {})
        sender_name = self.raw.get("sender_name") or self.raw.get("nickname") or self.sender_id
        self.message_obj = SimpleNamespace(
            message_id=self.message_id,
            session_id=self.session_id,
            message_str=self.message_str,
            sender=SimpleNamespace(user_id=self.sender_id, nickname=str(sender_name)),
        )
        self._results: list[MessageEventResult] = []
        self._stopped = False

    def get_sender_id(self) -> str:
        return self.sender_id

    def get_sender_name(self) -> str:
        return str(self.message_obj.sender.nickname)

    def get_session_id(self) -> str:
        return self.session_id

    def get_group_id(self) -> str | None:
        if self.unified_msg_origin.startswith("group:"):
            return self.unified_msg_origin.split(":", 1)[1]
        return None

    def get_messages(self) -> list[Any]:
        messages = self.raw.get("message")
        return list(messages) if isinstance(messages, list) else []

    def plain_result(self, text: object) -> MessageEventResult:
        return MessageEventResult(str(text))

    async def send(self, result: MessageEventResult | None) -> None:
        if result is not None:
            self._results.append(result)

    def stop_event(self) -> None:
        self._stopped = True

    def is_stopped(self) -> bool:
        return self._stopped


class Context:
    def __init__(self) -> None:
        self.metadata: dict[str, Any] = {}


class Star:
    def __init__(self, context: Context) -> None:
        self.context = context

    async def initialize(self) -> None:
        return None

    async def terminate(self) -> None:
        return None


def register(*metadata: Any, **named_metadata: Any):
    def decorator(plugin_class: type[Star]) -> type[Star]:
        setattr(plugin_class, "__echoweave_astrbot_registration__", {"args": metadata, **named_metadata})
        return plugin_class

    return decorator


def handler_declarations(handler: Callable[..., Any]) -> tuple[FilterDeclaration, ...]:
    target = handler.__func__ if inspect.ismethod(handler) else handler
    return tuple(getattr(target, "__echoweave_astrbot_filters__", ()))


def compile_handlers(plugin: Star) -> tuple[AstrBotHandlerDescriptor, ...]:
    descriptors: list[AstrBotHandlerDescriptor] = []
    for name, handler in inspect.getmembers(plugin, predicate=callable):
        declarations = handler_declarations(handler)
        if declarations:
            descriptors.append(AstrBotHandlerDescriptor(name, handler, declarations))
    return tuple(descriptors)


__all__ = [
    "AstrMessageEvent",
    "AstrBotHandlerDescriptor",
    "Context",
    "EventMessageType",
    "FilterDeclaration",
    "MessageEventResult",
    "PermissionType",
    "PlatformAdapterType",
    "Star",
    "filter",
    "compile_handlers",
    "handler_declarations",
    "register",
]
