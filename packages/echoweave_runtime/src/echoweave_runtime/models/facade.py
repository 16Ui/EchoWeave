from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from echoweave_runtime.models.base import ModelClient, StreamOptions
from echoweave_runtime.models.streaming import ModelStreamEvent
from echoweave_runtime.types import AgentResponse


class ProviderModelFacade(ModelClient):
    def __init__(self, client: ModelClient) -> None:
        self._client = client

    def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        options: StreamOptions | None = None,
    ) -> AgentResponse:
        return self._client.generate(messages, tools, options=options)

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        options: StreamOptions | None = None,
    ) -> AgentResponse:
        return self._client.complete(messages, tools, options=options)

    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        options: StreamOptions | None = None,
    ) -> Iterable[ModelStreamEvent]:
        return self._client.stream(messages, tools, options=options)
