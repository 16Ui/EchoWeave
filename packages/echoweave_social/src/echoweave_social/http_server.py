"""Legacy compatibility shim.

HTTP/Web server code lives in `echoweave_web.server`. This module intentionally
does not import the web package at module import time, keeping `echoweave_social`
usable without pulling in the Web layer.
"""

from __future__ import annotations

from typing import Any

__all__ = ["HubWebhookServer", "_read_chunked_body", "re_match_approval_action"]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(name)
    from echoweave_web import server

    return getattr(server, name)
