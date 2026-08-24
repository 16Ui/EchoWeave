from __future__ import annotations

import json
from typing import Any

from echoweave_runtime.config import get_openai_client_kwargs
from echoweave_runtime.context.prompt_builder import get_system_prompt
from echoweave_runtime.models.base import ModelClient, StreamOptions
from echoweave_runtime.models.streaming import ModelStreamEvent
from echoweave_runtime.types import AgentResponse, ToolCall

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - handled at runtime when dependency is missing
    OpenAI = None  # type: ignore[assignment]


def _build_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
            },
        }
        for tool in tools
    ]


def _parse_tool_arguments(arguments: str | None) -> dict[str, Any]:
    if not arguments:
        return {}
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _extract_message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                value = block.get("text")
                if isinstance(value, str):
                    parts.append(value)
        return "\n".join(parts)
    return ""


def _to_openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = [{"role": "system", "content": get_system_prompt()}]
    pending_tool_call_message: dict[str, Any] | None = None
    pending_tool_call_ids: set[str] = set()
    pending_tool_results: list[dict[str, Any]] = []

    def flush_pending_tool_call(*, complete: bool) -> None:
        nonlocal pending_tool_call_message, pending_tool_call_ids, pending_tool_results
        if pending_tool_call_message is None:
            return
        if complete and not pending_tool_call_ids:
            converted.append(pending_tool_call_message)
            converted.extend(pending_tool_results)
        else:
            converted.append(_incomplete_tool_call_context(pending_tool_call_message, pending_tool_results, pending_tool_call_ids))
        pending_tool_call_message = None
        pending_tool_call_ids = set()
        pending_tool_results = []

    for message in messages:
        role = str(message.get("role", "user"))
        content = message.get("content")

        if isinstance(content, str):
            flush_pending_tool_call(complete=False)
            converted.append({"role": "assistant" if role == "assistant" else "user", "content": content})
            continue

        if not isinstance(content, list):
            flush_pending_tool_call(complete=False)
            converted.append({"role": "assistant" if role == "assistant" else "user", "content": ""})
            continue

        text_parts: list[str] = []
        tool_uses: list[dict[str, Any]] = []
        tool_results: list[dict[str, Any]] = []

        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text":
                text = block.get("text")
                if isinstance(text, str) and text:
                    text_parts.append(text)
            elif block_type == "tool_use":
                tool_id = block.get("id")
                name = block.get("name")
                if isinstance(tool_id, str) and isinstance(name, str):
                    tool_uses.append(
                        {
                            "id": tool_id,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                            },
                        }
                    )
            elif block_type == "tool_result":
                tool_use_id = block.get("tool_use_id")
                if isinstance(tool_use_id, str):
                    tool_results.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_use_id,
                            "content": str(block.get("content", "")),
                        }
                    )

        if tool_results:
            valid_results: list[dict[str, Any]] = []
            orphaned_results: list[str] = []
            for result in tool_results:
                tool_call_id = str(result.get("tool_call_id") or "")
                if tool_call_id in pending_tool_call_ids:
                    valid_results.append(result)
                    pending_tool_call_ids.discard(tool_call_id)
                else:
                    orphaned_results.append(f"[tool_result {tool_call_id or 'unknown'}]\n{result.get('content', '')}")
            if pending_tool_call_message is not None:
                pending_tool_results.extend(valid_results)
                if not pending_tool_call_ids:
                    flush_pending_tool_call(complete=True)
            else:
                converted.extend(valid_results)
            if orphaned_results:
                flush_pending_tool_call(complete=False)
                converted.append(
                    {
                        "role": "user",
                        "content": "历史中存在没有对应 tool_calls 的工具结果，已作为普通上下文保留：\n"
                        + "\n\n".join(orphaned_results),
                    }
                )
            continue

        if tool_uses:
            flush_pending_tool_call(complete=False)
            pending_tool_call_ids = {str(tool_use["id"]) for tool_use in tool_uses}
            pending_tool_call_message = {
                "role": "assistant",
                "content": "\n".join(text_parts) if text_parts else "",
                "tool_calls": tool_uses,
            }
            continue

        flush_pending_tool_call(complete=False)
        converted.append({"role": "assistant" if role == "assistant" else "user", "content": "\n".join(text_parts)})

    flush_pending_tool_call(complete=False)
    return converted


def _incomplete_tool_call_context(
    assistant_message: dict[str, Any],
    tool_results: list[dict[str, Any]],
    missing_tool_call_ids: set[str],
) -> dict[str, Any]:
    parts: list[str] = []
    assistant_text = str(assistant_message.get("content") or "")
    if assistant_text:
        parts.append(assistant_text)
    parts.append("历史中存在未完整配对的工具调用，已作为普通上下文保留，避免 OpenAI tool_calls 协议错误。")
    for tool_call in assistant_message.get("tool_calls") or []:
        if not isinstance(tool_call, dict):
            continue
        function = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
        parts.append(
            "[tool_call {id}] {name} {arguments}".format(
                id=tool_call.get("id") or "unknown",
                name=function.get("name") or "unknown",
                arguments=function.get("arguments") or "{}",
            )
        )
    for result in tool_results:
        parts.append(f"[tool_result {result.get('tool_call_id') or 'unknown'}]\n{result.get('content', '')}")
    if missing_tool_call_ids:
        parts.append("缺失 tool_result: " + ", ".join(sorted(missing_tool_call_ids)))
    return {"role": "user", "content": "\n\n".join(parts)}


class OpenAIModelClient(ModelClient):
    def __init__(
        self,
        model: str = "gpt-4.1",
        *,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        if OpenAI is None:
            raise RuntimeError("openai package is not installed")
        client_kwargs = get_openai_client_kwargs()
        if api_key:
            client_kwargs["api_key"] = api_key
        if base_url:
            client_kwargs["base_url"] = base_url
        self.client = OpenAI(**client_kwargs)
        self.model = model

    def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        options: StreamOptions | None = None,
    ) -> AgentResponse:
        request_kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": _to_openai_messages(messages),
            "tools": _build_openai_tools(tools),
        }
        if options and options.max_tokens is not None:
            request_kwargs["max_tokens"] = options.max_tokens
        if options and options.temperature is not None:
            request_kwargs["temperature"] = options.temperature
        response = self.client.chat.completions.create(**request_kwargs)
        message = response.choices[0].message
        text = _extract_message_text(message.content).strip()
        tool_calls: list[ToolCall] = []
        content_blocks: list[dict[str, Any]] = []

        if text:
            content_blocks.append({"type": "text", "text": text})

        for tool_call in message.tool_calls or []:
            parsed_input = _parse_tool_arguments(tool_call.function.arguments)
            tool_calls.append(
                ToolCall(id=tool_call.id, name=tool_call.function.name, input=parsed_input)
            )
            content_blocks.append(
                {
                    "type": "tool_use",
                    "id": tool_call.id,
                    "name": tool_call.function.name,
                    "input": parsed_input,
                }
            )

        return AgentResponse(
            text=text,
            tool_calls=tool_calls or None,
            content=content_blocks or None,
        )

    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        options: StreamOptions | None = None,
    ):
        request_kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": _to_openai_messages(messages),
            "tools": _build_openai_tools(tools),
            "stream": True,
        }
        if options and options.max_tokens is not None:
            request_kwargs["max_tokens"] = options.max_tokens
        if options and options.temperature is not None:
            request_kwargs["temperature"] = options.temperature
        stream = self.client.chat.completions.create(**request_kwargs)

        text_open = False
        text_parts: list[str] = []
        tool_states: dict[int, dict[str, Any]] = {}

        for chunk in stream:
            if not getattr(chunk, "choices", None):
                continue
            choice = chunk.choices[0]
            delta = getattr(choice, "delta", None)
            if delta is None:
                continue

            content_delta = getattr(delta, "content", None)
            if isinstance(content_delta, str) and content_delta:
                if not text_open:
                    text_open = True
                    yield ModelStreamEvent(type="text_start", payload={})
                text_parts.append(content_delta)
                yield ModelStreamEvent(type="text_delta", payload={"delta": content_delta})

            delta_tool_calls = getattr(delta, "tool_calls", None) or []
            for delta_tool_call in delta_tool_calls:
                index = int(getattr(delta_tool_call, "index", 0))
                state = tool_states.setdefault(index, {"id": None, "name": None, "arguments": "", "started": False})

                call_id = getattr(delta_tool_call, "id", None)
                if isinstance(call_id, str) and call_id:
                    state["id"] = call_id

                function = getattr(delta_tool_call, "function", None)
                if function is not None:
                    function_name = getattr(function, "name", None)
                    if isinstance(function_name, str) and function_name:
                        state["name"] = function_name
                    arguments_delta = getattr(function, "arguments", None)
                    if isinstance(arguments_delta, str) and arguments_delta:
                        state["arguments"] += arguments_delta
                        if state["started"] and state["id"]:
                            yield ModelStreamEvent(
                                type="tool_call_delta",
                                payload={"id": state["id"], "partial_json": arguments_delta},
                            )

                if state["name"] and not state["started"]:
                    state["started"] = True
                    if state["id"] is None:
                        state["id"] = f"tool-{index}"
                    yield ModelStreamEvent(
                        type="tool_call_start",
                        payload={"id": state["id"], "name": state["name"]},
                    )
                    if state["arguments"]:
                        yield ModelStreamEvent(
                            type="tool_call_delta",
                            payload={"id": state["id"], "partial_json": state["arguments"]},
                        )

        if text_open:
            yield ModelStreamEvent(type="text_end", payload={})

        collected_tool_calls: list[dict[str, Any]] = []
        content_blocks: list[dict[str, Any]] = []
        full_text = "".join(text_parts).strip()
        if full_text:
            content_blocks.append({"type": "text", "text": full_text})

        for index in sorted(tool_states):
            state = tool_states[index]
            if not state.get("name"):
                continue
            tool_id = state.get("id") or f"tool-{index}"
            parsed_input = _parse_tool_arguments(str(state.get("arguments", "")))
            tool_call = {
                "id": tool_id,
                "name": str(state["name"]),
                "input": parsed_input,
            }
            collected_tool_calls.append(tool_call)
            content_blocks.append({"type": "tool_use", **tool_call})
            yield ModelStreamEvent(type="tool_call_end", payload=tool_call)

        yield ModelStreamEvent(
            type="message_done",
            payload={
                "text": full_text,
                "tool_calls": collected_tool_calls,
                "content": content_blocks,
            },
        )
