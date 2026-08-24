from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from echoweave_runtime.models.base import ModelClient, StreamOptions
from echoweave_runtime.models.streaming import ModelStreamEvent
from echoweave_runtime.types import AgentResponse, ToolCall


@dataclass
class SequenceModelClient(ModelClient):
    responses: list[AgentResponse]

    def __post_init__(self) -> None:
        self._index = 0

    def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        options: StreamOptions | None = None,
    ) -> AgentResponse:
        if self._index >= len(self.responses):
            raise RuntimeError("demo response sequence is exhausted")
        response = self.responses[self._index]
        self._index += 1
        if response.content is None and response.tool_calls:
            return AgentResponse(
                text=response.text,
                tool_calls=response.tool_calls,
                content=[
                    {"type": "tool_use", "id": call.id, "name": call.name, "input": call.input}
                    for call in response.tool_calls
                ],
            )
        return response

    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        options: StreamOptions | None = None,
    ):
        response = self.generate(messages, tools, options=options)
        if response.text:
            yield ModelStreamEvent(type="text_start", payload={})
            midpoint = max(1, len(response.text) // 2)
            yield ModelStreamEvent(type="text_delta", payload={"delta": response.text[:midpoint]})
            yield ModelStreamEvent(type="text_delta", payload={"delta": response.text[midpoint:]})
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


class EchoTurnModelClient(ModelClient):
    def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        options: StreamOptions | None = None,
    ) -> AgentResponse:
        user_messages = [message for message in messages if message.get("role") == "user"]
        latest = user_messages[-1]["content"] if user_messages else ""
        if not isinstance(latest, str):
            latest = str(latest)
        return AgentResponse(text=f"echo: {latest}")


class CompactionDemoModelClient(ModelClient):
    def __init__(self) -> None:
        self.calls = 0

    def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        options: StreamOptions | None = None,
    ) -> AgentResponse:
        self.calls += 1
        return AgentResponse(text=f"turn-{self.calls}")


def tool_response(tool_id: str, name: str, input: dict[str, Any]) -> AgentResponse:
    return AgentResponse(tool_calls=[ToolCall(id=tool_id, name=name, input=input)])


def build_code_demo_model(source_file: Path, test_file: Path, pytest_command: str) -> SequenceModelClient:
    source_name = source_file.name
    test_name = test_file.name
    return SequenceModelClient(
        responses=[
            tool_response("code-demo-1", "read", {"path": source_name}),
            AgentResponse(text=f"inspected {source_name}"),
            tool_response("code-demo-2", "read", {"path": test_name}),
            AgentResponse(text=f"inspected {test_name}"),
            tool_response("code-demo-3", "bash", {"command": pytest_command}),
            AgentResponse(text="captured failing pytest output"),
            tool_response(
                "code-demo-4",
                "edit",
                {"path": source_name, "old": "return a + b + 1", "new": "return a + b"},
            ),
            AgentResponse(text=f"fixed {source_name}"),
            tool_response("code-demo-5", "bash", {"command": pytest_command}),
            AgentResponse(text="pytest passes after the fix"),
        ]
    )
