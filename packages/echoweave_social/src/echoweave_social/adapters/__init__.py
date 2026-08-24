from __future__ import annotations

from echoweave_social.adapters.astrbot_event import AstrBotEventAdapter
from echoweave_social.adapters.base import PlatformAdapter
from echoweave_social.adapters.feishu import FeishuAdapter
from echoweave_social.adapters.generic_webhook import GenericWebhookAdapter
from echoweave_social.adapters.onebot_v11 import OneBotV11Adapter
from echoweave_social.adapters.wechat_official import WeChatOfficialAdapter

__all__ = [
    "AstrBotEventAdapter",
    "FeishuAdapter",
    "GenericWebhookAdapter",
    "OneBotV11Adapter",
    "PlatformAdapter",
    "WeChatOfficialAdapter",
]
