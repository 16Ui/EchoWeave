from __future__ import annotations

import json
from typing import Any

from anthropic import Anthropic

from echoweave_runtime.config import get_anthropic_client_kwargs
from echoweave_runtime.context.prompt_builder import get_system_prompt
from echoweave_runtime.models.base import ModelClient, StreamOptions
from echoweave_runtime.models.streaming import ModelStreamEvent
from echoweave_runtime.types import AgentResponse, ToolCall


_THINK_START = "<think>"
_THINK_END = "</think>"


def _partial_tag_suffix_length(text: str, tag: str) -> int:
    max_size = min(len(text), len(tag) - 1)
    for size in range(max_size, 0, -1):
        if tag.startswith(text[-size:]):
            return size
    return 0


class _ThinkTagFilter:
    def __init__(self) -> None:
        self._buffer = ""
        self._inside_think = False

    def feed(self, chunk: str) -> str:
        self._buffer += chunk
        visible_parts: list[str] = []

        while self._buffer:
            if self._inside_think:
                end_index = self._buffer.find(_THINK_END)
                if end_index >= 0:
                    self._buffer = self._buffer[end_index + len(_THINK_END) :]
                    self._inside_think = False
                    continue
                keep = _partial_tag_suffix_length(self._buffer, _THINK_END)
                self._buffer = self._buffer[-keep:] if keep else ""
                break

            start_index = self._buffer.find(_THINK_START)
            if start_index >= 0:
                if start_index > 0:
                    visible_parts.append(self._buffer[:start_index])
                self._buffer = self._buffer[start_index + len(_THINK_START) :]
                self._inside_think = True
                continue

            keep = _partial_tag_suffix_length(self._buffer, _THINK_START)
            if keep:
                visible_parts.append(self._buffer[:-keep])
                self._buffer = self._buffer[-keep:]
            else:
                visible_parts.append(self._buffer)
                self._buffer = ""
            break

        return "".join(visible_parts)

    def flush(self) -> str:
        if self._inside_think:
            self._buffer = ""
            return ""
        remaining = self._buffer
        self._buffer = ""
        return remaining


def _strip_think_tags(text: str) -> str:
    filter_state = _ThinkTagFilter()
    visible = filter_state.feed(text)
    return visible + filter_state.flush()


def _extract_response_content(content: list[Any]) -> tuple[list[str], list[dict[str, Any]], list[ToolCall]]:
    text_parts: list[str] = []
    content_blocks: list[dict[str, Any]] = []
    tool_calls: list[ToolCall] = []

    for block in content:
        if block.type == "text":
            visible_text = _strip_think_tags(block.text)
            if visible_text:
                text_parts.append(visible_text)
                content_blocks.append({"type": "text", "text": visible_text})
        elif block.type == "tool_use":
            tool_call = ToolCall(id=block.id, name=block.name, input=block.input)
            tool_calls.append(tool_call)
            content_blocks.append(
                {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
            )

    return text_parts, content_blocks, tool_calls


class AnthropicModelClient(ModelClient):
    def __init__(self, model: str = "claude-opus-4-6") -> None:
        self.client = Anthropic(**get_anthropic_client_kwargs())
        self.model = model

    def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        options: StreamOptions | None = None,
    ) -> AgentResponse:
        max_tokens = options.max_tokens if options and options.max_tokens is not None else 8096
        request_kwargs: dict[str, Any] = {
            "model": self.model,
            "system": get_system_prompt(),
            "max_tokens": max_tokens,
            "messages": messages,
            "tools": tools,
        }
        if options and options.temperature is not None:
            request_kwargs["temperature"] = options.temperature
        response = self.client.messages.create(**request_kwargs)
        text_parts, content_blocks, tool_calls = _extract_response_content(response.content)
        return AgentResponse(
            text="\n".join(text_parts).strip(),
            tool_calls=tool_calls or None,
            content=content_blocks or None,
        )

    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        options: StreamOptions | None = None,
    ):
        max_tokens = options.max_tokens if options and options.max_tokens is not None else 8096
        request_kwargs: dict[str, Any] = {
            "model": self.model,
            "system": get_system_prompt(),
            "max_tokens": max_tokens,
            "messages": messages,
            "tools": tools,
            "stream": True,
        }
        if options and options.temperature is not None:
            request_kwargs["temperature"] = options.temperature
        response = self.client.messages.create(**request_kwargs)
        text_open = False
        block_types: dict[int, str] = {}
        tool_states: dict[int, dict[str, Any]] = {}
        text_filters: dict[int, _ThinkTagFilter] = {}
        collected_text_parts: list[str] = []
        collected_content_blocks: list[dict[str, Any]] = []
        collected_tool_calls: list[dict[str, Any]] = []
        for event in response:
            event_type = getattr(event, "type", "")
            if event_type == "content_block_start":
                index = int(event.index)
                block = event.content_block
                block_types[index] = block.type
                if block.type == "text":
                    text_filters[index] = _ThinkTagFilter()
                elif block.type == "tool_use":
                    tool_states[index] = {"id": block.id, "name": block.name, "input": {}}
                    yield ModelStreamEvent(
                        type="tool_call_start",
                        payload={"id": block.id, "name": block.name},
                    )
            elif event_type == "content_block_delta":
                index = int(event.index)
                block_type = block_types.get(index)
                delta = event.delta
                delta_type = getattr(delta, "type", "")
                if block_type == "text" and delta_type == "text_delta":
                    filter_state = text_filters.setdefault(index, _ThinkTagFilter())
                    visible_delta = filter_state.feed(delta.text)
                    if visible_delta:
                        if not text_open:
                            text_open = True
                            yield ModelStreamEvent(type="text_start", payload={})
                        collected_text_parts.append(visible_delta)
                        yield ModelStreamEvent(type="text_delta", payload={"delta": visible_delta})
                elif block_type == "tool_use" and delta_type == "input_json_delta" and index in tool_states:
                    partial = getattr(delta, "partial_json", "")
                    if partial:
                        state = tool_states[index]
                        state.setdefault("partial_json", "")
                        state["partial_json"] += partial
                        yield ModelStreamEvent(
                            type="tool_call_delta",
                            payload={"id": state["id"], "partial_json": partial},
                        )
            elif event_type == "content_block_stop":
                index = int(event.index)
                block_type = block_types.pop(index, "")
                if index in tool_states:
                    state = tool_states.pop(index)
                    partial_json = state.pop("partial_json", "")
                    if partial_json:
                        try:
                            state["input"] = json.loads(partial_json)
                        except json.JSONDecodeError:
                            state["input"] = {}
                    tool_entry = {"id": state["id"], "name": state["name"], "input": state.get("input", {})}
                    collected_tool_calls.append(tool_entry)
                    collected_content_blocks.append({"type": "tool_use", **tool_entry})
                    yield ModelStreamEvent(
                        type="tool_call_end",
                        payload=tool_entry,
                    )
                elif block_type == "text":
                    visible_tail = text_filters.pop(index, _ThinkTagFilter()).flush()
                    if visible_tail:
                        if not text_open:
                            text_open = True
                            yield ModelStreamEvent(type="text_start", payload={})
                        collected_text_parts.append(visible_tail)
                        yield ModelStreamEvent(type="text_delta", payload={"delta": visible_tail})
                    if text_open:
                        # Collect the full text for this block into content_blocks
                        block_text = "".join(
                            p for p in collected_text_parts
                        )
                        if block_text:
                            collected_content_blocks.append({"type": "text", "text": block_text})
                        yield ModelStreamEvent(type="text_end", payload={})
                        text_open = False
            elif event_type == "message_stop":
                break
        # Build message_done from collected stream data (no second HTTP request needed)
        full_text = "".join(collected_text_parts).strip()
        yield ModelStreamEvent(
            type="message_done",
            payload={
                "text": full_text,
                "tool_calls": collected_tool_calls,
                "content": collected_content_blocks,
            },
        )
