from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from echoweave_social.schema import EchoWeaveEvent


DEFAULT_ADMIN_ONLY_COMMANDS = (
    "approve",
    "approvals",
    "bind",
    "deny",
    "rag:index",
    "retry",
    "revoke",
    "skill:global",
)


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    reason: str = ""
    reason_code: str = ""
    silent: bool = False


@dataclass(frozen=True)
class AccessPolicy:
    admins: tuple[str, ...] = ()
    allowed_users: tuple[str, ...] = ()
    allowed_groups: tuple[str, ...] = ()
    blocked_users: tuple[str, ...] = ()
    require_mention_in_group: bool = False
    bot_ids: tuple[str, ...] = ()
    admin_only_commands: tuple[str, ...] = DEFAULT_ADMIN_ONLY_COMMANDS

    def check(self, event: EchoWeaveEvent) -> AccessDecision:
        sender = _normalize_id(event.sender_id)
        conversation_kind, conversation_id = _split_conversation(event.conversation_id)
        admins = _normalize_set(self.admins)
        allowed_users = _normalize_set(self.allowed_users)
        allowed_groups = _normalize_set(self.allowed_groups)
        blocked_users = _normalize_set(self.blocked_users)

        if sender in blocked_users:
            return AccessDecision(False, "Sender is blocked.", "blocked_user")

        is_admin = sender in admins
        if allowed_users and not is_admin and sender not in allowed_users:
            return AccessDecision(False, "Sender is not allowed to use EchoWeave.", "user_not_allowed")

        if conversation_kind == "group":
            group_id = _normalize_id(conversation_id)
            if allowed_groups and group_id not in allowed_groups:
                return AccessDecision(False, "Group is not allowed to use EchoWeave.", "group_not_allowed")
            if self.require_mention_in_group and not _is_command(event.text) and not _mentions_bot(event, self.bot_ids):
                return AccessDecision(False, "Group message did not mention EchoWeave.", "mention_required", silent=True)

        command_key = command_access_key(event.text)
        if command_key is not None and _is_admin_only(command_key, self.admin_only_commands) and not is_admin:
            return AccessDecision(False, "This command requires an EchoWeave admin.", "admin_required")

        return AccessDecision(True)


def command_access_key(text: str) -> str | None:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    body = stripped[1:].strip()
    if not body:
        return None
    parts = body.split()
    command = parts[0].lower()
    if command == "rag" and len(parts) >= 2 and parts[1].lower() in {"index", "reindex"}:
        return "rag:index"
    if command == "skill" and len(parts) >= 2 and parts[1].lower() == "global":
        return "skill:global"
    return command


def _is_admin_only(command_key: str, admin_only_commands: tuple[str, ...]) -> bool:
    configured = {item.strip().lower() for item in admin_only_commands if item.strip()}
    if command_key in configured:
        return True
    command = command_key.split(":", 1)[0]
    return command in configured


def _is_command(text: str) -> bool:
    return text.strip().startswith("/")


def _split_conversation(conversation_id: str) -> tuple[str, str]:
    kind, sep, value = conversation_id.partition(":")
    if sep and kind:
        return kind.lower(), value
    return "private", conversation_id


def _normalize_id(value: object) -> str:
    return str(value or "").strip()


def _normalize_set(values: tuple[str, ...]) -> set[str]:
    return {_normalize_id(value) for value in values if _normalize_id(value)}


def _mentions_bot(event: EchoWeaveEvent, bot_ids: tuple[str, ...]) -> bool:
    configured_bot_ids = _normalize_set(bot_ids)
    raw = event.raw
    if _message_segments_mention_bot(raw.get("message"), configured_bot_ids):
        return True
    if _message_segments_mention_bot(raw.get("message_obj"), configured_bot_ids):
        return True
    text = event.text.strip()
    return text.startswith("@EchoWeave") or text.startswith("@机器人")


def _message_segments_mention_bot(value: Any, bot_ids: set[str]) -> bool:
    if not isinstance(value, list):
        return False
    for segment in value:
        if not isinstance(segment, dict) or segment.get("type") != "at":
            continue
        data = segment.get("data")
        if not isinstance(data, dict):
            continue
        qq = _normalize_id(data.get("qq"))
        if qq == "all":
            return True
        if bot_ids:
            if qq in bot_ids:
                return True
        elif qq:
            return True
    return False
