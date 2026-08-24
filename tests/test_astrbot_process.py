from __future__ import annotations

from pathlib import Path
import json

import pytest

from echoweave_runtime.events import InboundMessage
from echoweave_runtime.extensions.astrbot_process import AstrBotPluginError, AstrBotPluginProcess
from echoweave_runtime.lifecycle import RuntimeHost


def _write_plugin(root: Path, source: str) -> None:
    root.mkdir(parents=True)
    (root / "metadata.yaml").write_text(
        "\n".join(
            [
                "name: astrbot_plugin_process_fixture",
                "desc: Process compatibility fixture",
                "author: echoweave-tests",
                "version: 1.0.0",
            ]
        ),
        encoding="utf-8",
    )
    (root / "main.py").write_text(source.strip(), encoding="utf-8")


def _message(text: str) -> InboundMessage:
    return InboundMessage(
        platform="astrbot:aiocqhttp",
        conversation_id="group:42",
        sender_id="7",
        text=text,
        message_id="m-1",
        raw={"sender_name": "Echo User", "message": [{"type": "text", "data": {"text": text}}]},
    )


def _write_permissions(root: Path, *capabilities: str) -> None:
    (root / "echoweave.permissions.json").write_text(
        json.dumps({"schema_version": 1, "capabilities": list(capabilities)}),
        encoding="utf-8",
    )


def test_plugin_execution_requires_explicit_opt_in(tmp_path: Path) -> None:
    root = tmp_path / "plugin"
    _write_plugin(root, "from astrbot.api.star import Star\nclass Plugin(Star):\n    pass")

    component = AstrBotPluginProcess(root)

    with pytest.raises(AstrBotPluginError, match="allow_execution"):
        component.start()


def test_official_style_command_runs_in_worker_and_obeys_lifecycle(tmp_path: Path) -> None:
    root = tmp_path / "plugin"
    _write_plugin(
        root,
        """
from pathlib import Path
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register

@register("fixture", "tester", "fixture", "1.0.0")
class Plugin(Star):
    async def initialize(self):
        Path(__file__).with_name("initialized.txt").write_text("yes", encoding="utf-8")

    @filter.command("hello", alias={"hi"})
    async def hello(self, event: AstrMessageEvent):
        print("plugin output must not corrupt protocol")
        yield event.plain_result(f"Hello, {event.get_sender_name()}: {event.message_str}")

    async def terminate(self):
        Path(__file__).with_name("terminated.txt").write_text("yes", encoding="utf-8")
""",
    )
    _write_permissions(root, "filesystem-write")
    component = AstrBotPluginProcess(
        root,
        allow_execution=True,
        granted_capabilities=frozenset({"filesystem-write"}),
    )
    host = RuntimeHost().register(component)

    host.start()
    replies = component.dispatch(_message("/hi"))
    host.stop()

    assert replies[0].text == "Hello, Echo User: /hi"
    assert replies[0].metadata["plugin_id"] == "echoweave-tests/astrbot_plugin_process_fixture"
    assert (root / "initialized.txt").read_text(encoding="utf-8") == "yes"
    assert (root / "terminated.txt").read_text(encoding="utf-8") == "yes"
    assert any("plugin output must not corrupt protocol" in line for line in component.recent_logs)


def test_command_arguments_and_admin_filter_are_mapped(tmp_path: Path) -> None:
    root = tmp_path / "plugin"
    _write_plugin(
        root,
        """
from __future__ import annotations
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Star

class Plugin(Star):
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("add")
    async def add(self, event: AstrMessageEvent, left: int, right: int):
        yield event.plain_result(str(left + right))
""",
    )
    component = AstrBotPluginProcess(root, allow_execution=True)
    component.start()
    try:
        assert component.dispatch(_message("/add 2 3"), is_admin=False) == ()
        assert component.dispatch(_message("/add 2 3"), is_admin=True)[0].text == "5"
    finally:
        component.stop()


def test_worker_protocol_round_trips_unicode_independent_of_console_codepage(tmp_path: Path) -> None:
    root = tmp_path / "plugin"
    _write_plugin(
        root,
        """
from astrbot.api.event import filter
from astrbot.api.star import Star

class Plugin(Star):
    @filter.command("你好")
    async def hello(self, event):
        yield event.plain_result("你好，来自插件")
""",
    )
    component = AstrBotPluginProcess(root, allow_execution=True)
    component.start()
    try:
        assert component.dispatch(_message("/你好"))[0].text == "你好，来自插件"
    finally:
        component.stop()


def test_worker_timeout_terminates_faulting_plugin(tmp_path: Path) -> None:
    root = tmp_path / "plugin"
    _write_plugin(
        root,
        """
import asyncio
from astrbot.api.event import filter
from astrbot.api.star import Star

class Plugin(Star):
    @filter.command("hang")
    async def hang(self, event):
        await asyncio.sleep(5)
        yield event.plain_result("late")
""",
    )
    component = AstrBotPluginProcess(root, allow_execution=True, timeout_seconds=0.2)
    component.start()

    with pytest.raises(AstrBotPluginError, match="failed during dispatch"):
        component.dispatch(_message("/hang"))

    assert component.running is False


def test_sensitive_capability_requires_declaration_and_runtime_grant(tmp_path: Path) -> None:
    root = tmp_path / "plugin"
    _write_plugin(
        root,
        """
import socket
from astrbot.api.star import Star
class Plugin(Star):
    pass
""",
    )

    undeclared = AstrBotPluginProcess(root, allow_execution=True, granted_capabilities=frozenset({"network"}))
    with pytest.raises(AstrBotPluginError, match="not declared"):
        undeclared.start()

    _write_permissions(root, "network")
    ungranted = AstrBotPluginProcess(root, allow_execution=True)
    with pytest.raises(AstrBotPluginError, match="not granted"):
        ungranted.start()


def test_worker_receives_only_explicit_plugin_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "plugin"
    _write_plugin(
        root,
        """
import os
from astrbot.api.event import filter
from astrbot.api.star import Star

class Plugin(Star):
    @filter.command("env")
    async def env(self, event):
        yield event.plain_result(os.getenv("ECHOWEAVE_TEST_SECRET", "missing"))
""",
    )
    _write_permissions(root, "environment")
    monkeypatch.setenv("ECHOWEAVE_TEST_SECRET", "must-not-leak")
    component = AstrBotPluginProcess(
        root,
        allow_execution=True,
        granted_capabilities=frozenset({"environment"}),
        plugin_environment={"PLUGIN_MODE": "test"},
    )
    component.start()
    try:
        assert component.dispatch(_message("/env"))[0].text == "missing"
    finally:
        component.stop()


def test_worker_rejects_response_over_protocol_limit(tmp_path: Path) -> None:
    root = tmp_path / "plugin"
    _write_plugin(
        root,
        """
from astrbot.api.event import filter
from astrbot.api.star import Star

class Plugin(Star):
    @filter.command("large")
    async def large(self, event):
        yield event.plain_result("x" * 4096)
""",
    )
    component = AstrBotPluginProcess(root, allow_execution=True, max_response_bytes=1024)
    component.start()
    try:
        with pytest.raises(AstrBotPluginError, match="response exceeds"):
            component.dispatch(_message("/large"))
    finally:
        component.stop()
