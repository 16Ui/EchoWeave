from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterable

from echoweave_runtime.models.streaming import ModelStreamEvent
from echoweave_runtime.types import AgentResponse


@dataclass(frozen=True)
class StreamOptions:
    temperature: float | None = None
    max_tokens: int | None = None
    thinking_budget: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ModelClient(ABC):
    @abstractmethod
    def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        options: StreamOptions | None = None,
    ) -> AgentResponse:
        raise NotImplementedError

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        options: StreamOptions | None = None,
    ) -> AgentResponse:
        return self.generate(messages, tools, options=options)

    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        options: StreamOptions | None = None,
    ) -> Iterable[ModelStreamEvent]:
        response = self.complete(messages, tools, options=options)
        if response.text:
            yield ModelStreamEvent(type="text_start", payload={})
            yield ModelStreamEvent(type="text_delta", payload={"delta": response.text})
            yield ModelStreamEvent(type="text_end", payload={})
        if response.tool_calls:
            for tool_call in response.tool_calls:
                yield ModelStreamEvent(type="tool_call_start", payload={"id": tool_call.id, "name": tool_call.name})
                yield ModelStreamEvent(type="tool_call_delta", payload={"id": tool_call.id, "input": tool_call.input})
                yield ModelStreamEvent(
                    type="tool_call_end",
                    payload={"id": tool_call.id, "name": tool_call.name, "input": tool_call.input},
                )
        yield ModelStreamEvent(
            type="message_done",
            payload={
                "text": response.text,
                "tool_calls": [
                    {"id": tool_call.id, "name": tool_call.name, "input": tool_call.input}
                    for tool_call in (response.tool_calls or [])
                ],
                "content": response.content,
            },
        )
