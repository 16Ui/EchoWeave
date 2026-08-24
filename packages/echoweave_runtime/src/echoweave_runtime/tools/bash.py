from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from echoweave_runtime.governance import evaluate_runtime_command, evaluate_runtime_path, record_runtime_audit
from echoweave_runtime.sandbox import DockerSandboxProfile
from echoweave_runtime.tools.policy import PolicyVerdict, ShellCommandPolicy, default_shell_command_policy
from echoweave_runtime.tools_base import resolve_path


class BashTool:
    name = "bash"
    effect = "non_idempotent"
    description = "Run a shell command"
    input_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "cwd": {"type": "string"},
            "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 600},
        },
        "required": ["command"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        cwd: Path,
        policy: ShellCommandPolicy = default_shell_command_policy,
        approval_callback: Callable[..., bool] | None = None,
        container_sandbox: DockerSandboxProfile | None = None,
    ) -> None:
        self.workspace_root = cwd.resolve()
        self.cwd = self.workspace_root
        self.policy = policy
        # approval_callback(command, reason) -> True=approved, False=denied
        self.approval_callback = approval_callback
        self.container_sandbox = container_sandbox or DockerSandboxProfile()

    def execute(self, arguments: dict[str, str]) -> str:
        run_cwd = self.cwd
        raw_cwd = arguments.get("cwd")
        if raw_cwd:
            run_cwd = resolve_path(self.workspace_root, raw_cwd)
            if not run_cwd.is_dir():
                raise ValueError("cwd must be a directory")

        command = arguments["command"]
        timeout_seconds = int(arguments.get("timeout_seconds") or 120)
        command_category = self.policy.classify(command)
        cd_target = _parse_cd_command(command)
        if cd_target is not None:
            return self._change_directory(cd_target, run_cwd, command)
        result = self.policy.check(command)
        harness_result = evaluate_runtime_command(command, workspace=self.workspace_root)
        if harness_result.decision == "deny":
            record_runtime_audit(
                "command",
                "policy",
                status="blocked",
                subject=command,
                workspace=run_cwd,
                metadata={
                    "verdict": "deny",
                    "reason": harness_result.reason,
                    "reason_code": harness_result.reason_code,
                    "matched_rules": list(harness_result.matched_rules),
                    "category": command_category,
                },
            )
            raise PermissionError(f"blocked by harness policy: {harness_result.reason}")
        if harness_result.decision == "escalate" and result.verdict == PolicyVerdict.ALLOW:
            result = type(result)(
                verdict=PolicyVerdict.REQUIRE_APPROVAL,
                reason=harness_result.reason,
                reason_code=harness_result.reason_code,
                matched_rules=harness_result.matched_rules,
            )
        record_runtime_audit(
            "command",
            "policy",
            status="ok" if result.verdict == PolicyVerdict.ALLOW else "blocked" if result.verdict == PolicyVerdict.DENY else "escalate",
            subject=command,
            workspace=run_cwd,
            metadata={
                "verdict": result.verdict.value,
                "reason": result.reason,
                "reason_code": result.reason_code,
                "matched_rules": list(result.matched_rules),
                "category": command_category,
            },
        )
        if result.verdict == PolicyVerdict.DENY:
            raise PermissionError(f"blocked by shell policy: {result.reason}")
        if result.verdict == PolicyVerdict.REQUIRE_APPROVAL:
            approved = self._request_approval(command, result.reason, run_cwd)
            if not approved:
                raise PermissionError(f"blocked by shell policy: {result.reason} (denied)")

        started = time.perf_counter()
        sandboxed = self.container_sandbox.enabled
        run_args: str | list[str]
        run_args = arguments["command"]
        shell = True
        execution_cwd = run_cwd
        if sandboxed:
            run_args = self.container_sandbox.wrap_command(command, workspace=self.workspace_root, cwd=run_cwd)
            shell = False
            execution_cwd = self.workspace_root
        try:
            result = subprocess.run(
                run_args,
                cwd=execution_cwd,
                shell=shell,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            output = ((exc.stdout or "") if isinstance(exc.stdout, str) else "") + (
                (exc.stderr or "") if isinstance(exc.stderr, str) else ""
            )
            rendered = _render_command_output(output, returncode=None, timed_out=True)
            record_runtime_audit(
                "command",
                "execute",
                status="error",
                subject=command,
                workspace=run_cwd,
                latency_ms=(time.perf_counter() - started) * 1000,
                metadata={
                    "reason": str(exc),
                    "reason_code": "command.timeout",
                    "timeout_seconds": timeout_seconds,
                    "category": command_category,
                    "sandbox": self.container_sandbox.diagnostics() if sandboxed else {"enabled": False},
                    "output_chars": len(output),
                    "output_truncated": len(rendered) < len(output),
                },
            )
            return rendered
        except Exception as exc:
            record_runtime_audit(
                "command",
                "execute",
                status="error",
                subject=command,
                workspace=run_cwd,
                latency_ms=(time.perf_counter() - started) * 1000,
                metadata={"reason": str(exc), "reason_code": "command.execute_failed", "category": command_category},
            )
            raise
        output = (result.stdout or "") + (result.stderr or "")
        rendered = _render_command_output(output, returncode=result.returncode)
        record_runtime_audit(
            "command",
            "execute",
            status="ok" if result.returncode == 0 else "error",
            subject=command,
            workspace=run_cwd,
            latency_ms=(time.perf_counter() - started) * 1000,
            metadata={
                "returncode": result.returncode,
                "category": command_category,
                "timeout_seconds": timeout_seconds,
                "sandbox": self.container_sandbox.diagnostics() if sandboxed else {"enabled": False},
                "output_chars": len(output.strip()),
                "rendered_chars": len(rendered),
                "output_truncated": len(rendered) < len(output.strip()),
            },
        )
        return rendered or f"Command exited with code {result.returncode}"

    def _request_approval(self, command: str, reason: str, run_cwd: Path) -> bool:
        if self.approval_callback is None:
            return False
        try:
            return bool(self.approval_callback(command, reason, run_cwd))
        except TypeError:
            return bool(self.approval_callback(command, reason))

    def _change_directory(self, raw_target: str, run_cwd: Path, command: str) -> str:
        started = time.perf_counter()
        try:
            target = (run_cwd / raw_target).resolve()
            if target != self.workspace_root and self.workspace_root not in target.parents:
                raise ValueError("path escapes working directory")
            decision = evaluate_runtime_path(str(target), workspace=self.workspace_root)
            if not decision.allowed:
                raise PermissionError(f"blocked by harness policy: {decision.reason}")
            if not target.is_dir():
                raise ValueError("cd target must be a directory")
            self.cwd = target
        except Exception as exc:
            record_runtime_audit(
                "command",
                "execute",
                status="error",
                subject=command,
                workspace=run_cwd,
                latency_ms=(time.perf_counter() - started) * 1000,
                metadata={"reason": str(exc), "reason_code": "command.cd_failed", "category": "navigation"},
            )
            raise
        record_runtime_audit(
            "command",
            "execute",
            status="ok",
            subject=command,
            workspace=self.cwd,
            latency_ms=(time.perf_counter() - started) * 1000,
            metadata={"category": "navigation", "cwd": str(self.cwd)},
        )
        return f"Changed directory to {self.cwd}"


def _render_command_output(
    output: str,
    *,
    returncode: int | None,
    timed_out: bool = False,
    max_chars: int = 12000,
) -> str:
    rendered = output.strip()
    if len(rendered) > max_chars:
        head_size = max_chars // 2
        tail_size = max_chars - head_size
        rendered = (
            rendered[:head_size].rstrip()
            + f"\n... output truncated, original_chars={len(output.strip())} ...\n"
            + rendered[-tail_size:].lstrip()
        )
    if timed_out:
        suffix = "Command timed out"
    elif returncode is None:
        suffix = ""
    else:
        suffix = f"Command exited with code {returncode}"
    if suffix and (timed_out or returncode != 0 or not rendered):
        return f"{rendered}\n{suffix}" if rendered else suffix
    return rendered


def _parse_cd_command(command: str) -> str | None:
    stripped = command.strip()
    lowered = stripped.lower()
    if lowered in {"cd", "chdir"}:
        return "."
    for prefix in ("cd ", "chdir "):
        if lowered.startswith(prefix):
            target = stripped[len(prefix) :].strip()
            if len(target) >= 2 and target[0] == target[-1] and target[0] in {'"', "'"}:
                target = target[1:-1]
            return target or "."
    return None
