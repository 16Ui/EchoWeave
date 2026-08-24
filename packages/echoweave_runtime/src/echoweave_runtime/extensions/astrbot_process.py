from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
from collections import deque
from pathlib import Path
from typing import Any
from uuid import uuid4

from echoweave_runtime.events import InboundMessage, OutboundMessage
from echoweave_runtime.extensions.astrbot_compat import (
    AstrBotCompatibilityReport,
    inspect_astrbot_plugin,
)


class AstrBotPluginError(RuntimeError):
    pass


class AstrBotPluginProcess:
    """Opt-in lifecycle component for an AstrBot plugin compatibility worker.

    The subprocess is a fault boundary, not an OS security sandbox. Callers must
    explicitly opt in after reviewing the static compatibility report.
    """

    def __init__(
        self,
        plugin_root: str | Path,
        *,
        allow_execution: bool = False,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.plugin_root = Path(plugin_root).expanduser().resolve()
        self.report: AstrBotCompatibilityReport = inspect_astrbot_plugin(self.plugin_root)
        self.allow_execution = allow_execution
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self.startup_timeout_seconds = max(3.0, self.timeout_seconds)
        self._process: subprocess.Popen[str] | None = None
        self._responses: queue.Queue[dict[str, Any]] = queue.Queue()
        self._stderr_lines: deque[str] = deque(maxlen=200)
        self._request_lock = threading.Lock()
        self._reader_threads: list[threading.Thread] = []

    @property
    def name(self) -> str:
        return f"astrbot-plugin:{self.report.manifest.plugin_id}"

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def recent_logs(self) -> tuple[str, ...]:
        return tuple(self._stderr_lines)

    def start(self) -> None:
        if self.running:
            return
        if not self.allow_execution:
            raise AstrBotPluginError("AstrBot plugin execution requires explicit allow_execution=True")
        if self.report.blockers:
            raise AstrBotPluginError("AstrBot plugin is blocked: " + "; ".join(self.report.blockers))

        runtime_source_root = Path(__file__).resolve().parents[2]
        self._process = subprocess.Popen(
            [sys.executable, "-m", "echoweave_runtime.extensions.astrbot_worker", str(self.plugin_root)],
            cwd=runtime_source_root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert self._process.stdout is not None
        assert self._process.stderr is not None
        stdout_reader = threading.Thread(target=self._read_stdout, args=(self._process.stdout,), daemon=True)
        stderr_reader = threading.Thread(target=self._read_stderr, args=(self._process.stderr,), daemon=True)
        self._reader_threads = [stdout_reader, stderr_reader]
        stdout_reader.start()
        stderr_reader.start()

        try:
            ready = self._responses.get(timeout=self.startup_timeout_seconds)
        except queue.Empty as exc:
            self._terminate_worker()
            raise AstrBotPluginError("AstrBot plugin worker startup timed out") from exc
        if ready.get("type") != "ready":
            self._terminate_worker()
            raise AstrBotPluginError(str(ready.get("error") or "AstrBot plugin worker failed to start"))

    def dispatch(self, message: InboundMessage, *, is_admin: bool = False) -> tuple[OutboundMessage, ...]:
        response = self._request(
            "dispatch",
            {"message": {**message.to_dict(), "is_admin": is_admin}},
        )
        result = response.get("result")
        if not isinstance(result, dict):
            raise AstrBotPluginError("AstrBot worker returned an invalid dispatch result")
        replies = result.get("replies")
        if not isinstance(replies, list):
            raise AstrBotPluginError("AstrBot worker replies must be a list")
        return tuple(
            OutboundMessage(
                text=str(item.get("text") or ""),
                platform=message.platform,
                conversation_id=message.conversation_id,
                target_id=message.reply_target_id or message.conversation_id,
                metadata={
                    "compatibility": "astrbot",
                    "plugin_id": self.report.manifest.plugin_id,
                    "result_type": item.get("result_type"),
                    **(item.get("metadata") if isinstance(item.get("metadata"), dict) else {}),
                },
            )
            for item in replies
            if isinstance(item, dict)
        )

    def stop(self) -> None:
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            try:
                self._request("shutdown", {}, expected_type="stopped")
            except AstrBotPluginError:
                self._terminate_worker()
            else:
                try:
                    process.wait(timeout=self.timeout_seconds)
                except subprocess.TimeoutExpired:
                    self._terminate_worker()
        self._process = None

    def _request(
        self,
        operation: str,
        payload: dict[str, Any],
        *,
        expected_type: str = "response",
    ) -> dict[str, Any]:
        with self._request_lock:
            process = self._process
            if process is None or process.poll() is not None or process.stdin is None:
                raise AstrBotPluginError("AstrBot plugin worker is not running")
            request_id = uuid4().hex
            request = {"id": request_id, "operation": operation, **payload}
            try:
                process.stdin.write(json.dumps(request, ensure_ascii=True) + "\n")
                process.stdin.flush()
                response = self._responses.get(timeout=self.timeout_seconds)
            except (BrokenPipeError, OSError, queue.Empty) as exc:
                self._terminate_worker()
                raise AstrBotPluginError(f"AstrBot plugin worker failed during {operation}") from exc
            if response.get("id") != request_id:
                raise AstrBotPluginError("AstrBot plugin worker response correlation failed")
            if response.get("type") == "error":
                raise AstrBotPluginError(str(response.get("error") or "AstrBot plugin error"))
            if response.get("type") != expected_type:
                raise AstrBotPluginError(f"unexpected AstrBot worker response: {response.get('type')}")
            return response

    def _read_stdout(self, stream: Any) -> None:
        for line in stream:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                self._stderr_lines.append(f"invalid worker protocol line: {line.rstrip()}")
                continue
            if isinstance(value, dict):
                self._responses.put(value)

    def _read_stderr(self, stream: Any) -> None:
        for line in stream:
            self._stderr_lines.append(line.rstrip())

    def _terminate_worker(self) -> None:
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        self._process = None


__all__ = ["AstrBotPluginError", "AstrBotPluginProcess"]
