from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from echoweave_runtime.extensions.base import McpServerConfig


class LocalMcpProvider:
    def __init__(self, cwd: Path, config_path: Path | None = None) -> None:
        self.cwd = cwd.resolve()
        self.config_path = (config_path or (self.cwd / ".echoweave" / "mcp_servers.json")).resolve()
        self._servers: dict[str, McpServerConfig] = {}
        self._diagnostics: dict[str, Any] = {
            "config_path": self.config_path.as_posix(),
            "status": "not_found",
            "loaded_servers": 0,
            "invalid_entries": 0,
        }
        self._load_servers()

    def _load_servers(self) -> None:
        if not self.config_path.exists():
            self._servers = {}
            self._diagnostics = {
                "config_path": self.config_path.as_posix(),
                "status": "not_found",
                "loaded_servers": 0,
                "invalid_entries": 0,
            }
            return
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self._servers = {}
            self._diagnostics = {
                "config_path": self.config_path.as_posix(),
                "status": "parse_error",
                "loaded_servers": 0,
                "invalid_entries": 0,
                "error": f"{type(exc).__name__}: {exc}",
            }
            return

        raw_servers = data.get("servers") if isinstance(data, dict) else None
        if not isinstance(raw_servers, list):
            self._servers = {}
            self._diagnostics = {
                "config_path": self.config_path.as_posix(),
                "status": "invalid_schema",
                "loaded_servers": 0,
                "invalid_entries": 0,
            }
            return
        parsed: dict[str, McpServerConfig] = {}
        invalid_entries = 0
        for item in raw_servers:
            if not isinstance(item, dict):
                invalid_entries += 1
                continue
            name = item.get("name")
            command = item.get("command")
            if not isinstance(name, str) or not name.strip():
                invalid_entries += 1
                continue
            if not isinstance(command, str) or not command.strip():
                invalid_entries += 1
                continue
            raw_args = item.get("args", [])
            args = [str(part) for part in raw_args] if isinstance(raw_args, list) else []
            raw_env = item.get("env", {})
            env = {str(k): str(v) for k, v in raw_env.items()} if isinstance(raw_env, dict) else {}
            timeout_seconds = float(item.get("timeout_seconds", 10.0))
            parsed[name] = McpServerConfig(
                name=name,
                command=command,
                args=args,
                env=env,
                timeout_seconds=timeout_seconds,
            )
        self._servers = parsed
        self._diagnostics = {
            "config_path": self.config_path.as_posix(),
            "status": "ok",
            "loaded_servers": len(parsed),
            "invalid_entries": invalid_entries,
        }

    def diagnostics(self) -> dict[str, Any]:
        return dict(self._diagnostics)

    def list_servers(self) -> list[McpServerConfig]:
        return list(self._servers.values())

    def call(self, server: str, method: str, params: dict[str, Any] | None = None) -> str:
        config = self._servers.get(server)
        if config is None:
            raise ValueError(f"unknown mcp server: {server}")
        if not method.strip():
            raise ValueError("method is required")

        request = {
            "jsonrpc": "2.0",
            "id": "echoweave",
            "method": method,
            "params": params or {},
        }

        env = os.environ.copy()
        env.update(config.env)

        process = subprocess.Popen(
            [config.command, *config.args],
            cwd=self.cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        try:
            stdout, stderr = process.communicate(json.dumps(request, ensure_ascii=False) + "\n", timeout=config.timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.communicate()
            raise RuntimeError(f"mcp call timeout: {server}/{method}") from exc

        stdout = (stdout or "").strip()
        stderr = (stderr or "").strip()

        if process.returncode != 0:
            detail = stderr or stdout or f"exit code {process.returncode}"
            raise RuntimeError(f"mcp server failed: {detail}")

        response_line = ""
        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith("{"):
                response_line = line
                break
        if not response_line:
            raise RuntimeError("mcp server returned no JSON response")

        try:
            response = json.loads(response_line)
        except json.JSONDecodeError as exc:
            raise RuntimeError("mcp server returned invalid JSON") from exc

        if isinstance(response, dict) and response.get("error"):
            raise RuntimeError(f"mcp error: {response['error']}")

        result = response.get("result") if isinstance(response, dict) else response
        if isinstance(result, str):
            return result
        return json.dumps(result, ensure_ascii=False)
