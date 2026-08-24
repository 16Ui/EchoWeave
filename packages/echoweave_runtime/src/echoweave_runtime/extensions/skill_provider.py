from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Callable

from echoweave_runtime.extensions.base import SkillSpec
from echoweave_runtime.tools.grep import GrepTool


class LocalSkillProvider:
    """本地 Skill 提供器：聚合 builtin/user/workspace 三类来源并处理覆盖优先级。"""

    def __init__(
        self,
        cwd: Path,
        config_path: Path | None = None,
        user_config_path: Path | None = None,
    ) -> None:
        self.cwd = cwd.resolve()
        self.workspace_config_path = (config_path or (self.cwd / ".echoweave" / "skills.json")).resolve()
        self.user_config_path = (user_config_path or (Path.home() / ".echoweave" / "skills.json")).resolve()
        self._skills: dict[str, tuple[SkillSpec, Callable[[dict[str, Any]], Any]]] = {}
        self._register_builtin_skills()
        self._register_config_skills(self.user_config_path, source_label="user")
        self._register_config_skills(self.workspace_config_path, source_label="workspace")

    def _source_precedence(self, source: str) -> int:
        """来源优先级：workspace > user > builtin。"""
        if source.startswith("workspace:"):
            return 3
        if source.startswith("user:"):
            return 2
        if source.startswith("builtin:") or source == "builtin":
            return 1
        return 0

    def _register_builtin_skills(self) -> None:
        self.register(
            SkillSpec(
                name="search_workspace",
                description="Search workspace files with regex pattern",
                input_schema={
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string"},
                        "path": {"type": "string"},
                        "glob": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["pattern"],
                },
                source="builtin:core",
            ),
            self._search_workspace,
        )
        self.register(
            SkillSpec(
                name="run_pytest_smoke",
                description="Run a lightweight pytest smoke command",
                input_schema={
                    "type": "object",
                    "properties": {
                        "target": {"type": "string"},
                    },
                    "required": [],
                },
                source="builtin:core",
            ),
            self._run_pytest_smoke,
        )

    def _register_config_skills(self, config_path: Path, source_label: str) -> None:
        """从配置文件注册别名技能：可覆写 description/schema，并注入默认参数。"""
        if not config_path.exists():
            return
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(data, dict):
            return
        raw_skills = data.get("skills")
        if not isinstance(raw_skills, list):
            return

        source = f"{source_label}:{config_path.as_posix()}"
        for item in raw_skills:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            builtin = item.get("builtin")
            if not isinstance(name, str) or not name.strip():
                continue
            if not isinstance(builtin, str) or not builtin.strip():
                continue
            target = self._skills.get(builtin)
            if target is None:
                continue
            target_spec, target_executor = target
            defaults = item.get("defaults", {})
            if not isinstance(defaults, dict):
                defaults = {}

            description = item.get("description")
            if not isinstance(description, str) or not description.strip():
                description = f"Alias for {builtin}"

            schema = item.get("input_schema")
            if not isinstance(schema, dict):
                schema = dict(target_spec.input_schema)

            self.register(
                SkillSpec(
                    name=name,
                    description=description,
                    input_schema=schema,
                    source=source,
                ),
                self._build_alias_executor(target_executor, defaults),
            )

    def _build_alias_executor(
        self,
        target_executor: Callable[[dict[str, Any]], Any],
        defaults: dict[str, Any],
    ) -> Callable[[dict[str, Any]], Any]:
        def _executor(arguments: dict[str, Any]) -> Any:
            payload = dict(defaults)
            payload.update(arguments)
            return target_executor(payload)

        return _executor

    def register(
        self,
        spec: SkillSpec,
        executor: Callable[[dict[str, Any]], Any],
    ) -> None:
        """注册技能；当同名冲突时仅允许高优先级来源覆盖低优先级来源。"""
        existing = self._skills.get(spec.name)
        if existing is not None:
            existing_spec, _ = existing
            if self._source_precedence(spec.source) < self._source_precedence(existing_spec.source):
                return
        self._skills[spec.name] = (spec, executor)

    def list_skills(self) -> list[SkillSpec]:
        return [spec for spec, _ in self._skills.values()]

    def execute(self, name: str, arguments: dict[str, Any]) -> str:
        record = self._skills.get(name)
        if record is None:
            raise ValueError(f"unknown skill: {name}")
        _, executor = record
        result = executor(arguments)
        if isinstance(result, str):
            return result
        return json.dumps(result, ensure_ascii=False)

    def _search_workspace(self, arguments: dict[str, Any]) -> str:
        grep_tool = GrepTool(self.cwd)
        payload: dict[str, Any] = {"pattern": str(arguments.get("pattern", ""))}
        if not payload["pattern"]:
            raise ValueError("pattern is required")
        if "path" in arguments:
            payload["path"] = str(arguments["path"])
        if "glob" in arguments:
            payload["glob"] = str(arguments["glob"])
        if "limit" in arguments:
            payload["limit"] = int(arguments["limit"])
        return grep_tool.execute(payload)

    def _run_pytest_smoke(self, arguments: dict[str, Any]) -> str:
        target = str(arguments.get("target", "")).strip()
        command = ["python", "-m", "pytest"]
        if target:
            command.append(target)
        process = subprocess.run(
            command,
            cwd=self.cwd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = ((process.stdout or "") + (process.stderr or "")).strip()
        if process.returncode != 0:
            if output:
                return f"{output}\nCommand exited with code {process.returncode}"
            return f"Command exited with code {process.returncode}"
        return output or f"Command exited with code {process.returncode}"


class FilteredSkillProvider:
    def __init__(
        self,
        provider: LocalSkillProvider,
        enabled_names: set[str] | None = None,
    ) -> None:
        self.provider = provider
        self.enabled_names = set(enabled_names or set())

    def list_skills(self) -> list[SkillSpec]:
        if not self.enabled_names:
            return []
        return [spec for spec in self.provider.list_skills() if spec.name in self.enabled_names]

    def execute(self, name: str, arguments: dict[str, Any]) -> str:
        if name not in self.enabled_names:
            raise ValueError(f"skill is not enabled for this conversation: {name}")
        return self.provider.execute(name, arguments)
