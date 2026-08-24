from __future__ import annotations

"""Compatibility imports for the pre-M1 social message names.

New code imports the canonical contracts from :mod:`echoweave_runtime.events`.
"""

from echoweave_runtime.events import InboundMessage, OutboundMessage


EchoWeaveEvent = InboundMessage
EchoWeaveReply = OutboundMessage

__all__ = ["EchoWeaveEvent", "EchoWeaveReply"]
