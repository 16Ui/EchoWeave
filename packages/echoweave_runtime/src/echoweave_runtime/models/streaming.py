from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

StreamEventType = Literal[
    "text_start",
    "text_delta",
    "text_end",
    "tool_call_start",
    "tool_call_delta",
    "tool_call_end",
    "message_done",
    "message_error",
]


@dataclass
class ModelStreamEvent:
    type: StreamEventType
    payload: dict[str, Any]
