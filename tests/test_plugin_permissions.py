from __future__ import annotations

import json
from pathlib import Path

import pytest

from echoweave_runtime.extensions.plugin_permissions import (
    evaluate_plugin_permissions,
    load_plugin_permissions,
)


def test_permission_decision_requires_both_declaration_and_grant() -> None:
    decision = evaluate_plugin_permissions(
        requested={"network", "filesystem-write"},
        declared={"network"},
        granted={"filesystem-write"},
    )

    assert decision.allowed is False
    assert decision.undeclared == frozenset({"filesystem-write"})
    assert decision.ungranted == frozenset({"network"})


def test_permission_manifest_rejects_unknown_capability(tmp_path: Path) -> None:
    (tmp_path / "echoweave.permissions.json").write_text(
        json.dumps({"schema_version": 1, "capabilities": ["read-everything"]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown plugin capabilities"):
        load_plugin_permissions(tmp_path)
