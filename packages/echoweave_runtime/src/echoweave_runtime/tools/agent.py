from __future__ import annotations

import fnmatch
import difflib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from echoweave_runtime.governance import record_runtime_audit
from echoweave_runtime.models.base import ModelClient
from echoweave_runtime.tools_base import resolve_path


class AgentTool:
    name = "agent"
    description = "Run an isolated sub-agent task over the workspace"
    input_schema = {
        "type": "object",
        "properties": {
            "role": {
                "type": "string",
                "enum": ["explore", "plan", "verify", "summarize", "worker"],
                "description": "Sub-agent role. Read-only roles return reports; worker applies deterministic edits in an isolated copy and returns a patch.",
            },
            "task": {"type": "string", "description": "Task for the sub-agent."},
            "path": {"type": "string", "description": "Optional workspace-relative path to inspect."},
            "pattern": {"type": "string", "description": "Optional filename glob or text hint."},
            "edits": {
                "type": "array",
                "description": "Worker-only deterministic edits. Each item uses path plus old_string/new_string.",
            },
            "max_files": {"type": "integer", "minimum": 1, "maximum": 80},
            "max_chars": {"type": "integer", "minimum": 500, "maximum": 20000},
            "use_model": {
                "type": "boolean",
                "description": "If true and a model is bound by the runtime, ask the model to summarize the isolated report.",
            },
        },
        "required": ["role", "task"],
        "additionalProperties": False,
    }

    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd.resolve()
        self._model_client: ModelClient | None = None

    def bind_model(self, model_client: ModelClient) -> None:
        self._model_client = model_client

    def execute(self, arguments: dict[str, Any]) -> str:
        role = str(arguments["role"])
        task = str(arguments["task"]).strip()
        raw_path = str(arguments.get("path") or ".")
        pattern = str(arguments.get("pattern") or "").strip()
        edits = arguments.get("edits") or []
        max_files = int(arguments.get("max_files") or 24)
        max_chars = int(arguments.get("max_chars") or 8000)
        use_model = bool(arguments.get("use_model", False))
        file_count = 0
        try:
            root = resolve_path(self.cwd, raw_path)
            if role == "worker":
                report, file_count = self._run_worker(root, task, edits, max_chars=max_chars)
            else:
                files = _collect_files(root, pattern=pattern, max_files=max_files)
                file_count = len(files)
                report = _render_report(role, task, self.cwd, root, files, pattern=pattern, max_chars=max_chars)
            if use_model and role != "worker":
                report = self._summarize_with_model(role, task, report)
        except Exception as exc:
            record_runtime_audit(
                "agent",
                "subtask",
                status="error",
                subject=role,
                workspace=self.cwd,
                metadata={"task": task, "path": raw_path, "reason": str(exc), "reason_code": _reason_code(exc)},
            )
            raise
        record_runtime_audit(
            "agent",
            "subtask",
            status="ok",
            subject=role,
            workspace=self.cwd,
            metadata={
                "task": task,
                "path": str(root),
                "pattern": pattern,
                "files": file_count,
                "read_only": role != "worker",
                "isolated": True,
                "applied_to_workspace": False if role == "worker" else None,
            },
        )
        return report

    def _run_worker(self, root: Path, task: str, edits: Any, *, max_chars: int) -> tuple[str, int]:
        if not isinstance(edits, list) or not edits:
            return (
                "Sub-agent role: worker\n"
                f"Task: {task}\n"
                f"Workspace: {self.cwd}\n"
                f"Scope: {root}\n"
                "Isolated: true\n"
                "Applied to workspace: false\n\n"
                "No worker edits were provided. Supply edits with path, old_string, and new_string to produce an isolated patch.",
                0,
            )
        with tempfile.TemporaryDirectory(prefix="echoweave-worker-") as temp_dir:
            worker_root = Path(temp_dir) / "workspace"
            _copy_scope(self.cwd, root, worker_root)
            patch_blocks = _apply_worker_edits(self.cwd, root, worker_root, edits)
        return _render_worker_report(task, self.cwd, root, patch_blocks, max_chars=max_chars), len(patch_blocks)

    def _summarize_with_model(self, role: str, task: str, report: str) -> str:
        if self._model_client is None:
            return report + "\n\n[model_summary skipped: no model client bound]"
        prompt = (
            f"You are a read-only {role} sub-agent. Summarize the report for the parent coding agent.\n"
            "Do not request writes or command execution. Return concise findings, risks, and suggested next steps.\n\n"
            f"Task: {task}\n\n"
            f"Report:\n{report}"
        )
        response = self._model_client.generate([{"role": "user", "content": prompt}], [], options=None)
        text = (response.text or "").strip()
        if not text:
            return report + "\n\n[model_summary empty]"
        return (
            f"Sub-agent role: {role}\n"
            f"Task: {task}\n"
            "Read-only: true\n"
            "Model summary:\n"
            f"{text}\n\n"
            "Raw isolated report:\n"
            f"{report}"
        )


def _collect_files(root: Path, *, pattern: str, max_files: int) -> list[Path]:
    if root.is_file():
        return [root]
    if not root.exists():
        raise FileNotFoundError(f"path not found: {root}")
    if not root.is_dir():
        raise ValueError(f"path is not readable: {root}")
    ignored_dirs = {".git", ".venv", "venv", "__pycache__", "node_modules", ".pytest_cache", "dist", "build"}
    files: list[Path] = []
    for current_raw, dirnames, filenames in os.walk(root):
        current = Path(current_raw)
        dirnames[:] = [name for name in dirnames if name not in ignored_dirs]
        for filename in filenames:
            path = current / filename
            if _is_binaryish(path):
                continue
            if pattern and not (fnmatch.fnmatch(filename, pattern) or pattern.lower() in str(path).lower()):
                continue
            files.append(path)
            if len(files) >= max_files:
                return files
    return files


def _render_report(
    role: str,
    task: str,
    workspace: Path,
    root: Path,
    files: list[Path],
    *,
    pattern: str,
    max_chars: int,
) -> str:
    lines = [
        f"Sub-agent role: {role}",
        f"Task: {task}",
        f"Workspace: {workspace}",
        f"Scope: {root}",
        f"Read-only: true",
        f"Matched files: {len(files)}",
    ]
    if pattern:
        lines.append(f"Pattern: {pattern}")
    lines.append("")
    if role == "plan":
        lines.extend(
            [
                "Suggested approach:",
                "1. Read the files listed below before editing.",
                "2. Prefer exact unique edit replacements for small changes.",
                "3. Run the narrowest verification command first.",
                "",
            ]
        )
    elif role == "verify":
        lines.extend(
            [
                "Verification focus:",
                "- Check tests, entrypoints, configuration, and recently changed files.",
                "- Treat missing tests or unclear commands as risks to report.",
                "",
            ]
        )
    elif role == "summarize":
        lines.extend(["Workspace summary:", ""])
    else:
        lines.extend(["Exploration findings:", ""])

    used_chars = sum(len(line) + 1 for line in lines)
    for file in files:
        rel = _safe_relative(file, workspace)
        snippet = _read_snippet(file, limit=1200)
        block = f"--- {rel} ---\n{snippet}\n"
        if used_chars + len(block) > max_chars:
            lines.append(f"... truncated; {len(files)} files matched, increase max_chars for more detail ...")
            break
        lines.append(block)
        used_chars += len(block)
    if not files:
        lines.append("No matching readable files found.")
    return "\n".join(lines).strip()


def _copy_scope(workspace: Path, root: Path, worker_root: Path) -> None:
    if root.is_file():
        rel = root.relative_to(workspace)
        (worker_root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root, worker_root / rel)
        return
    if not root.exists():
        raise FileNotFoundError(f"path not found: {root}")
    if not root.is_dir():
        raise ValueError(f"path is not readable: {root}")
    ignored_dirs = {".git", ".venv", "venv", "__pycache__", "node_modules", ".pytest_cache", "dist", "build"}
    for current_raw, dirnames, filenames in os.walk(root):
        current = Path(current_raw)
        dirnames[:] = [name for name in dirnames if name not in ignored_dirs]
        for filename in filenames:
            source = current / filename
            if _is_binaryish(source):
                continue
            rel = source.relative_to(workspace)
            target = worker_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def _apply_worker_edits(workspace: Path, root: Path, worker_root: Path, edits: list[Any]) -> list[str]:
    patches: list[str] = []
    for index, item in enumerate(edits, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"worker edit #{index} must be an object")
        raw_path = item.get("path")
        old = item.get("old_string", item.get("old"))
        new = item.get("new_string", item.get("new"))
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError(f"worker edit #{index} path is required")
        if not isinstance(old, str) or not old:
            raise ValueError(f"worker edit #{index} old_string is required")
        if not isinstance(new, str):
            raise ValueError(f"worker edit #{index} new_string is required")
        source_path = resolve_path(workspace, raw_path)
        _ensure_within_scope(root, source_path)
        rel = source_path.relative_to(workspace)
        worker_path = worker_root / rel
        if not worker_path.exists():
            raise FileNotFoundError(f"worker edit target not copied into isolated workspace: {rel}")
        original_text = source_path.read_text(encoding="utf-8", errors="replace")
        worker_text = worker_path.read_text(encoding="utf-8", errors="replace")
        count = worker_text.count(old)
        if count == 0:
            raise ValueError(f"worker edit target text not found in {rel}")
        if count > 1:
            raise ValueError(f"worker edit target text is not unique in {rel}: {count} matches")
        updated = worker_text.replace(old, new, 1)
        worker_path.write_text(updated, encoding="utf-8")
        patches.append(_build_unified_diff(original_text, updated, rel))
    return patches


def _ensure_within_scope(root: Path, path: Path) -> None:
    if root.is_file():
        if path != root:
            raise ValueError(f"worker edit path outside scoped file: {path}")
        return
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"worker edit path outside scope: {path}") from exc


def _build_unified_diff(before: str, after: str, rel: Path) -> str:
    diff = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"a/{rel.as_posix()}",
        tofile=f"b/{rel.as_posix()}",
    )
    return "".join(diff).rstrip()


def _render_worker_report(task: str, workspace: Path, root: Path, patches: list[str], *, max_chars: int) -> str:
    header = [
        "Sub-agent role: worker",
        f"Task: {task}",
        f"Workspace: {workspace}",
        f"Scope: {root}",
        "Isolated: true",
        "Applied to workspace: false",
        f"Patch files: {len(patches)}",
        "",
        "Worker patch:",
        "",
    ]
    body = "\n\n".join(patch for patch in patches if patch)
    if not body:
        body = "No textual diff produced."
    text = "\n".join(header) + body
    if len(text) <= max_chars:
        return text.strip()
    keep = max(200, max_chars // 2)
    return (
        text[:keep].rstrip()
        + "\n... worker patch truncated; increase max_chars for more detail ...\n"
        + text[-keep:].lstrip()
    ).strip()


def _read_snippet(path: Path, limit: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"[unreadable: {exc}]"
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit // 2].rstrip() + "\n... file snippet truncated ...\n" + text[-limit // 2 :].lstrip()


def _safe_relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _is_binaryish(path: Path) -> bool:
    binary_suffixes = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz", ".7z", ".exe", ".dll"}
    return path.suffix.lower() in binary_suffixes


def _reason_code(exc: Exception) -> str:
    if "escapes working directory" in str(exc):
        return "agent.path_escape"
    if isinstance(exc, FileNotFoundError):
        return "agent.path_not_found"
    return "agent.subtask_failed"
