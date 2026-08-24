from __future__ import annotations

import json
from pathlib import Path

import pytest

from echoweave_runtime.extensions.astrbot_compat import (
    inspect_astrbot_plugin,
    load_astrbot_manifest,
)


def _write_manifest(root: Path, extra: str = "") -> None:
    (root / "metadata.yaml").write_text(
        "\n".join(
            [
                "name: astrbot_plugin_example",
                "display_name: Example",
                "desc: Compatibility fixture",
                "author: tester",
                "version: 1.2.3",
                "repo: https://github.com/example/plugin",
                "astrbot_version: '>=4.16,<5'",
                "support_platforms:",
                "  - aiocqhttp",
                "  - lark",
                extra,
            ]
        ),
        encoding="utf-8",
    )


def test_astrbot_manifest_preserves_identity_and_unknown_fields(tmp_path: Path) -> None:
    _write_manifest(tmp_path, "custom_market_field: retained")

    manifest = load_astrbot_manifest(tmp_path / "metadata.yaml")

    assert manifest.plugin_id == "tester/astrbot_plugin_example"
    assert manifest.support_platforms == ("aiocqhttp", "lark")
    assert manifest.astrbot_version == ">=4.16,<5"
    assert manifest.unknown_fields == {"custom_market_field": "retained"}


def test_astrbot_manifest_accepts_omitted_platform_declaration(tmp_path: Path) -> None:
    _write_manifest(tmp_path)
    content = (tmp_path / "metadata.yaml").read_text(encoding="utf-8")
    content = content.replace("support_platforms:\n  - aiocqhttp\n  - lark\n", "")
    (tmp_path / "metadata.yaml").write_text(content, encoding="utf-8")

    assert load_astrbot_manifest(tmp_path / "metadata.yaml").support_platforms == ()


def test_inspector_recognizes_basic_api_without_executing_plugin(tmp_path: Path) -> None:
    _write_manifest(tmp_path)
    (tmp_path / "main.py").write_text(
        """
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star

class Example(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    @filter.command("hello")
    async def hello(self, event: AstrMessageEvent):
        yield event.plain_result("hello")
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "_conf_schema.json").write_text(
        json.dumps({"enabled": {"type": "bool", "default": True}}),
        encoding="utf-8",
    )
    skill = tmp_path / "skills" / "helper" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# helper", encoding="utf-8")

    report = inspect_astrbot_plugin(tmp_path)

    assert report.status == "api-candidate"
    assert report.execution_ready is False
    assert report.decorators == ("command",)
    assert report.has_config_schema is True
    assert report.bundled_skills == (skill.resolve(),)
    assert report.blockers == ()


def test_inspector_blocks_direct_core_import_and_reports_capabilities(tmp_path: Path) -> None:
    _write_manifest(tmp_path)
    (tmp_path / "main.py").write_text(
        """
import os
import socket
from astrbot.core.star.star_manager import StarManager
""".strip(),
        encoding="utf-8",
    )

    report = inspect_astrbot_plugin(tmp_path)

    assert report.status == "blocked"
    assert "host-process" in report.requested_capabilities
    assert "network" in report.requested_capabilities
    assert any("direct AstrBot core import" in item for item in report.blockers)


def test_inspector_blocks_dynamic_imports(tmp_path: Path) -> None:
    _write_manifest(tmp_path)
    (tmp_path / "main.py").write_text("plugin = __import__('runtime_plugin')", encoding="utf-8")

    report = inspect_astrbot_plugin(tmp_path)

    assert report.status == "blocked"
    assert "dynamic imports are not allowed by the compatibility loader" in report.blockers


def test_manifest_rejects_ambiguous_plugin_identity(tmp_path: Path) -> None:
    _write_manifest(tmp_path)
    content = (tmp_path / "metadata.yaml").read_text(encoding="utf-8")
    (tmp_path / "metadata.yaml").write_text(content.replace("author: tester", "author: team/tester"), encoding="utf-8")

    with pytest.raises(ValueError, match="slash or control"):
        load_astrbot_manifest(tmp_path / "metadata.yaml")


def test_invalid_config_schema_is_rejected_before_plugin_import(tmp_path: Path) -> None:
    _write_manifest(tmp_path)
    (tmp_path / "main.py").write_text("from astrbot.api.star import Star", encoding="utf-8")
    (tmp_path / "_conf_schema.json").write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="root must be an object"):
        inspect_astrbot_plugin(tmp_path)
