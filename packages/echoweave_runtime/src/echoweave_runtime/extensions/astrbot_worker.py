from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import logging
import shlex
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from echoweave_runtime.extensions.astrbot_sdk import (  # noqa: E402
    AstrMessageEvent,
    Context,
    MessageEventResult,
    Star,
    compile_handlers,
    filter,
    register,
)


_MAX_RESPONSE_BYTES = 1_048_576


class AstrBotWorkerRuntime:
    def __init__(self, plugin_root: Path) -> None:
        self.plugin_root = plugin_root
        self.plugin: Star | None = None

    async def start(self) -> None:
        _install_astrbot_shims()
        module = _load_plugin_module(self.plugin_root / "main.py")
        plugin_classes = [
            value
            for value in vars(module).values()
            if inspect.isclass(value) and issubclass(value, Star) and value is not Star
        ]
        if len(plugin_classes) != 1:
            raise RuntimeError(f"expected exactly one AstrBot Star class, found {len(plugin_classes)}")
        self.plugin = plugin_classes[0](Context())
        await self.plugin.initialize()

    async def dispatch(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.plugin is None:
            raise RuntimeError("plugin is not initialized")
        event = AstrMessageEvent(payload)
        replies: list[MessageEventResult] = []
        handled = False
        for descriptor in compile_handlers(self.plugin):
            handler = descriptor.handler
            declarations = descriptor.filters
            if not _matches_non_command_filters(declarations, event, payload):
                continue
            command = descriptor.command
            arguments: list[str] = []
            if command is not None:
                matched, arguments = _match_command(event.message_str, command.value, command.options.get("aliases", ()))
                if not matched:
                    continue
            elif any(item.kind == "command_group" for item in declarations):
                continue
            handled = True
            replies.extend(await _invoke_handler(handler, event, arguments))
            replies.extend(event._results)
            event._results.clear()
            if event.is_stopped():
                break
        return {"handled": handled, "replies": [reply.to_dict() for reply in replies]}

    async def stop(self) -> None:
        if self.plugin is not None:
            await self.plugin.terminate()
            self.plugin = None


async def _invoke_handler(handler: Any, event: AstrMessageEvent, raw_arguments: list[str]) -> list[MessageEventResult]:
    arguments = _convert_arguments(handler, raw_arguments)
    result = handler(event, *arguments)
    values: list[Any] = []
    if inspect.isasyncgen(result):
        async for value in result:
            values.append(value)
    elif inspect.isawaitable(result):
        value = await result
        if value is not None:
            values.append(value)
    elif result is not None:
        values.append(result)
    return [value for value in values if isinstance(value, MessageEventResult)]


def _convert_arguments(handler: Any, values: list[str]) -> list[Any]:
    parameters = list(inspect.signature(handler).parameters.values())[1:]
    if len(values) < sum(1 for item in parameters if item.default is inspect.Parameter.empty):
        raise ValueError("not enough command arguments")
    if len(values) > len(parameters) and not any(item.kind is inspect.Parameter.VAR_POSITIONAL for item in parameters):
        raise ValueError("too many command arguments")
    converted: list[Any] = []
    for index, value in enumerate(values):
        parameter = parameters[min(index, len(parameters) - 1)]
        annotation = parameter.annotation
        if annotation in {int, "int"}:
            converted.append(int(value))
        elif annotation in {float, "float"}:
            converted.append(float(value))
        elif annotation in {bool, "bool"}:
            converted.append(value.lower() in {"1", "true", "yes", "on"})
        else:
            converted.append(value)
    return converted


def _match_command(message: str, name: str, aliases: tuple[str, ...]) -> tuple[bool, list[str]]:
    try:
        tokens = shlex.split(message.strip())
    except ValueError:
        return False, []
    if not tokens:
        return False, []
    candidate = tokens[0].lstrip("/")
    names = {name, *aliases}
    for declared in names:
        declared_tokens = declared.split()
        message_tokens = [candidate, *tokens[1:]]
        if message_tokens[: len(declared_tokens)] == declared_tokens:
            return True, message_tokens[len(declared_tokens) :]
    return False, []


def _matches_non_command_filters(declarations: tuple[Any, ...], event: AstrMessageEvent, payload: dict[str, Any]) -> bool:
    for declaration in declarations:
        if declaration.kind == "message_type" and declaration.value != "all":
            actual = "group" if event.get_group_id() else "private"
            if declaration.value != actual:
                return False
        elif declaration.kind == "permission" and declaration.value == "admin":
            if not bool(payload.get("is_admin")):
                return False
        elif declaration.kind == "platform" and declaration.value != "all":
            platform = event.platform.removeprefix("astrbot:")
            if declaration.value != platform:
                return False
    return True


def _install_astrbot_shims() -> None:
    astrbot = ModuleType("astrbot")
    api = ModuleType("astrbot.api")
    event = ModuleType("astrbot.api.event")
    star = ModuleType("astrbot.api.star")
    api.logger = logging.getLogger("astrbot.plugin")
    event.filter = filter
    event.AstrMessageEvent = AstrMessageEvent
    event.MessageEventResult = MessageEventResult
    star.Context = Context
    star.Star = Star
    star.register = register
    astrbot.api = api
    sys.modules.update(
        {"astrbot": astrbot, "astrbot.api": api, "astrbot.api.event": event, "astrbot.api.star": star}
    )


def _load_plugin_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("echoweave_astrbot_plugin.main", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load plugin entrypoint: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _serve(plugin_root: Path, protocol_output: Any) -> None:
    runtime = AstrBotWorkerRuntime(plugin_root)
    try:
        await runtime.start()
        _write(protocol_output, {"type": "ready"})
    except Exception as exc:
        _write(protocol_output, {"type": "startup_error", "error": str(exc), "error_type": type(exc).__name__})
        return

    for line in sys.stdin:
        request: dict[str, Any] = {}
        try:
            request = json.loads(line)
            request_id = request.get("id")
            operation = request.get("operation")
            if operation == "dispatch":
                result = await runtime.dispatch(dict(request.get("message") or {}))
                _write(protocol_output, {"type": "response", "id": request_id, "result": result})
            elif operation == "shutdown":
                await runtime.stop()
                _write(protocol_output, {"type": "stopped", "id": request_id})
                return
            else:
                raise ValueError(f"unknown worker operation: {operation}")
        except Exception as exc:
            _write(
                protocol_output,
                {"type": "error", "id": request.get("id"),
                 "error": str(exc), "error_type": type(exc).__name__},
            )


def _write(output: Any, value: dict[str, Any]) -> None:
    # Keep the wire format ASCII-only so Windows pipe code pages cannot corrupt
    # protocol frames containing plugin-generated Unicode text.
    encoded = json.dumps(value, ensure_ascii=True) + "\n"
    if len(encoded.encode("ascii")) > _MAX_RESPONSE_BYTES:
        encoded = json.dumps(
            {
                "type": "error",
                "id": value.get("id"),
                "error": f"AstrBot plugin response exceeds {_MAX_RESPONSE_BYTES} bytes",
                "error_type": "ResponseTooLarge",
            }
        ) + "\n"
    output.write(encoded)
    output.flush()


def main() -> None:
    global _MAX_RESPONSE_BYTES
    if len(sys.argv) > 2:
        _MAX_RESPONSE_BYTES = int(sys.argv[2])
    protocol_output = sys.stdout
    sys.stdout = sys.stderr
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    asyncio.run(_serve(Path(sys.argv[1]).resolve(), protocol_output))


if __name__ == "__main__":
    main()
