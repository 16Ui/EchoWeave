from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from collections.abc import Callable
import json
import shlex
import sys

import typer

from echoweave_harness.audit import read_audit_events
from echoweave_harness.evaluation import score_eval_case
from echoweave_harness.feedback import (
    suggest_eval_hardening,
    suggest_harness_improvements,
    write_eval_fixtures,
    write_feedback_backlog,
)
from echoweave_harness.metrics import compute_harness_metrics
from echoweave_runtime.app import build_registry, build_runtime
from echoweave_runtime.extensions.manager import ExtensionManager, build_extension_manager
from echoweave_runtime.sandbox import DockerSandboxProfile
from echoweave_runtime.tools.patch import PatchTool
from echoweave_runtime.config import (
    SUPPORTED_EXPORT_KINDS,
    SUPPORTED_PROVIDERS,
    SUPPORTED_TOOL_EXECUTION_MODES,
    get_sessions_dir,
    has_anthropic_credentials,
    has_openai_credentials,
    load_env,
    resolve_settings,
)
from echoweave_runtime.models.anthropic import AnthropicModelClient
from echoweave_runtime.models.openai import OpenAIModelClient
from echoweave_runtime.models.factory import (
    create_model_client as _create_model_client,
    get_provider_capabilities as _get_provider_capabilities,
)
from echoweave_runtime.models.demo import (
    build_code_demo_model,
    CompactionDemoModelClient,
    EchoTurnModelClient,
    SequenceModelClient,
    tool_response,
)
from echoweave_runtime.package_manager import PackageManager
from echoweave_runtime.session.store import SessionStore
from echoweave_runtime.types import AgentResponse, ToolExecutionMode
from echoweave_runtime.runtime.events import build_runtime_event
from echoweave_runtime.runtime.observer import summarize_runtime_events
from echoweave_runtime.runtime.session_runtime import (
    SessionRuntimeFacade,
    empty_tool_execution_stats,
    list_session_items,
    render_tree,
    summarize_tool_execution,
)

app = typer.Typer(add_completion=False)
package_app = typer.Typer(add_completion=False)
app.add_typer(package_app, name="package")


def resolve_rpc_runtime_context(request: dict[str, Any]) -> dict[str, str | None]:
    context: dict[str, str | None] = {
        "turn_id": None,
        "trace_id": None,
        "event_id": None,
        "parent_event_id": None,
    }
    for key in context:
        value = request.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            stripped = value.strip()
            context[key] = stripped or None
        else:
            context[key] = str(value)
    return context


def create_model_client(provider: str, model: str | None):
    try:
        return _create_model_client(provider, model)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def get_provider_capabilities(provider: str):
    try:
        return _get_provider_capabilities(provider)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def ensure_provider_credentials(provider: str) -> None:
    if provider == "anthropic" and not has_anthropic_credentials():
        raise typer.BadParameter("Anthropic credentials are not set")
    if provider == "openai" and not has_openai_credentials():
        raise typer.BadParameter("OpenAI credentials are not set")


def prepare_runtime_settings(
    working_dir: Path,
    provider: str | None,
    model: str | None,
    session_overrides: dict[str, Any] | None = None,
    validate_credentials: bool = True,
):
    if provider is not None and provider not in SUPPORTED_PROVIDERS:
        raise typer.BadParameter(f"Unsupported provider: {provider}")
    load_env(working_dir)
    settings = resolve_settings(
        working_dir,
        cli_provider=provider,
        cli_model=model,
        session_overrides=session_overrides,
    )
    if validate_credentials:
        ensure_provider_credentials(settings.provider)
    return settings


def create_agent_runtime(
    working_dir: Path,
    session_store: SessionStore,
    provider: str,
    model: str | None,
    compact_keep_tail: int = 8,
    tool_execution_mode: ToolExecutionMode = "sequential",
    event_sink: Callable[[str], None] | None = None,
    approval_callback=None,
    extensions: ExtensionManager | None = None,
    runtime_settings: Any | None = None,
):
    extension_manager = extensions or _build_extensions(working_dir, runtime_settings)
    registry = build_registry(working_dir, approval_callback=approval_callback, extensions=extension_manager)
    model_client = create_model_client(provider, model)
    provider_capabilities = get_provider_capabilities(provider)
    return build_runtime(
        model_client,
        registry,
        session_store,
        event_sink=event_sink,
        extensions=extension_manager,
        compact_keep_tail=compact_keep_tail,
        tool_execution_mode=tool_execution_mode,
        provider_capabilities=provider_capabilities,
    )


def create_chat_runtime(
    working_dir: Path,
    session_store: SessionStore,
    provider: str,
    model: str | None,
    json_stream: bool,
    compact_keep_tail: int = 8,
    tool_execution_mode: ToolExecutionMode = "sequential",
    approval_callback=None,
    extensions: ExtensionManager | None = None,
    runtime_settings: Any | None = None,
):
    return create_agent_runtime(
        working_dir,
        session_store,
        provider,
        model,
        compact_keep_tail=compact_keep_tail,
        tool_execution_mode=tool_execution_mode,
        event_sink=stream_sink(json_stream),
        approval_callback=approval_callback,
        extensions=extensions,
        runtime_settings=runtime_settings,
    )


def run_single_prompt(
    working_dir: Path,
    session_store: SessionStore,
    session_runtime: SessionRuntimeFacade,
    prompt: str,
    provider: str,
    model: str | None,
    json_stream: bool,
    compact_keep_tail: int = 8,
    tool_execution_mode: ToolExecutionMode = "sequential",
    runtime_settings: Any | None = None,
) -> tuple[str, list[dict[str, Any]], str | None]:
    runtime = create_chat_runtime(
        working_dir,
        session_store,
        provider,
        model,
        json_stream,
        compact_keep_tail=compact_keep_tail,
        tool_execution_mode=tool_execution_mode,
        runtime_settings=runtime_settings,
    )
    return session_runtime.run_prompt(runtime, prompt)



def _echo(text: str) -> None:
    """Write text to stdout with UTF-8 encoding, bypassing the terminal's default codec on Windows."""
    buf = getattr(sys.stdout, "buffer", None)
    if buf is not None:
        buf.write((text + "\n").encode("utf-8"))
        buf.flush()
    else:
        typer.echo(text)


def emit_output(payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        _echo(json.dumps(payload, ensure_ascii=False))
        return
    for key, value in payload.items():
        if isinstance(value, list):
            _echo(f"{key}:")
            for item in value:
                _echo(f"- {item}")
        elif isinstance(value, dict):
            _echo(f"{key}: {json.dumps(value, ensure_ascii=False)}")
        else:
            _echo(f"{key}: {value}")




def stream_sink(enabled: bool) -> callable:
    def sink(line: str) -> None:
        if enabled:
            _echo(line)

    return sink


def emit_runtime_event(enabled: bool, session_id: str, event: str, payload: dict[str, Any]) -> None:
    if not enabled:
        return
    _echo(build_runtime_event(event, session_id, payload).to_json())


def get_demo_sessions_dir(working_dir: Path) -> Path:
    return working_dir / ".echoweave-demo-sessions"


def bootstrap_code_demo_workspace(working_dir: Path) -> tuple[Path, Path, Path]:
    workspace_dir = working_dir / ".echoweave-code-demo"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    source_file = workspace_dir / "calculator.py"
    test_file = workspace_dir / "test_calculator.py"
    source_file.write_text(
        "def add(a: int, b: int) -> int:\n"
        "    return a + b + 1\n",
        encoding="utf-8",
    )
    test_file.write_text(
        "from calculator import add\n\n\n"
        "def test_add() -> None:\n"
        "    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    return workspace_dir, source_file, test_file


def collect_tool_results(session_store: SessionStore, session_path: Path, tool_name: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    tool_calls: dict[str, dict[str, Any]] = {}
    for event in session_store.read_events(session_path):
        if event.type == "tool_call" and event.payload.get("name") == tool_name:
            tool_calls[event.payload["id"]] = event.payload
        elif event.type == "tool_result":
            tool_call = tool_calls.get(event.payload.get("id"))
            if tool_call and tool_call.get("name") == tool_name:
                results.append(
                    {
                        "id": event.payload.get("id"),
                        "tool": tool_name,
                        "input": tool_call.get("input"),
                        "content": event.payload.get("content", ""),
                        "status": "ok",
                    }
                )
        elif event.type == "tool_error":
            tool_call = tool_calls.get(event.payload.get("id"))
            if tool_call and tool_call.get("name") == tool_name:
                results.append(
                    {
                        "id": event.payload.get("id"),
                        "tool": tool_name,
                        "input": tool_call.get("input"),
                        "content": event.payload.get("error", ""),
                        "status": "error",
                    }
                )
    return results


def get_session_store(working_dir: Path, use_global: bool = True) -> SessionStore:
    if use_global:
        return SessionStore(get_sessions_dir())
    return SessionStore(get_demo_sessions_dir(working_dir))




def get_latest_summary(session_store: SessionStore, session_runtime: SessionRuntimeFacade | None = None) -> str | None:
    runtime = session_runtime or build_session_browser_runtime(session_store)
    if runtime is None:
        return None
    _, summary = runtime.load_history()
    return summary


def build_session_browser_runtime(session_store: SessionStore) -> SessionRuntimeFacade | None:
    if session_store.latest() is None:
        return None
    return SessionRuntimeFacade.from_resume(session_store, True)


def resolve_session_browser_stream_id(session_runtime: SessionRuntimeFacade | None) -> str:
    if session_runtime is None:
        return "session-browser"
    return session_runtime.session_id()


def load_eval_cases(cases_file: str | None) -> list[dict[str, Any]]:
    if cases_file is None:
        return [
            {
                "id": "default-1",
                "prompt": "hello from eval",
                "expected_contains": None,
            }
        ]

    path = Path(cases_file)
    if not path.exists() or not path.is_file():
        raise typer.BadParameter(f"cases file not found: {cases_file}")

    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".jsonl":
        raw_cases: list[dict[str, Any]] = []
        for line in text.splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            if not isinstance(item, dict):
                raise typer.BadParameter("each JSONL line must be an object")
            raw_cases.append(item)
    else:
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get("cases")
        if not isinstance(data, list):
            raise typer.BadParameter("cases file must be a list or object with 'cases' list")
        raw_cases = []
        for item in data:
            if not isinstance(item, dict):
                raise typer.BadParameter("each case must be an object")
            raw_cases.append(item)

    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(raw_cases, start=1):
        prompt = item.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise typer.BadParameter(f"case #{index} prompt is required")
        case_id = item.get("id")
        expected = item.get("expected_contains")
        normalized.append(
            {
                "id": str(case_id).strip() if case_id is not None and str(case_id).strip() else f"case-{index}",
                "prompt": prompt,
                "expected_contains": str(expected) if expected is not None else None,
                "expected_tools": item.get("expected_tools"),
                "forbidden_tools": item.get("forbidden_tools"),
                "expected_rag_sources": item.get("expected_rag_sources"),
                "expected_policy_blocks": item.get("expected_policy_blocks"),
                "expect_sandbox_escape_blocked": item.get("expect_sandbox_escape_blocked"),
            }
        )

    if not normalized:
        raise typer.BadParameter("at least one eval case is required")
    return normalized


def parse_runtime_event_lines(lines: list[str]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in lines:
        text = line.strip()
        if not text or not text.startswith("{"):
            continue
        try:
            item = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            events.append(item)
    return events


def get_runtime_settings_payload(settings) -> dict[str, Any]:
    return {
        "provider": settings.provider,
        "model": settings.model,
        "compact_keep_tail": settings.compact_keep_tail,
        "tool_execution_mode": settings.tool_execution_mode,
        "export_default_kind": settings.export_default_kind,
        "manifest_path": str(settings.manifest_path),
        "memory_exact_match_weight": settings.memory_exact_match_weight,
        "memory_token_overlap_weight": settings.memory_token_overlap_weight,
        "memory_recency_weight": settings.memory_recency_weight,
    }


def resolve_export_payload(session_store: SessionStore, session_path: Path, kind: str) -> dict[str, Any]:
    if kind == "events":
        return {"events": {"items": [{"type": event.type, "payload": event.payload} for event in session_store.read_events(session_path)]}}
    if kind == "snapshot":
        snapshot = session_store.load_snapshot(session_path)
        return {
            "snapshot": {
                "session_id": snapshot.header.id,
                "parent_id": snapshot.header.parent_id,
                "branch_label": snapshot.header.branch_label,
                "summary": snapshot.summary,
                "history": snapshot.history,
                "history_size": len(snapshot.history),
                "compaction": snapshot.compaction,
                "state": snapshot.state,
            }
        }
    if kind == "tree":
        tree = session_store.build_tree()
        return {"tree": {"nodes": [line for root in tree.roots for line in render_tree(root)]}}
    if kind == "task_graph":
        graph = session_store.build_task_graph(session_store.read_events(session_path))
        return {"task_graph": graph}
    raise typer.BadParameter(f"Unsupported export kind: {kind}")


def default_export_output_path(working_dir: Path, session_store: SessionStore, session_path: Path, kind: str) -> Path:
    session_id = session_store.read_header(session_path).id
    return working_dir / ".echoweave-exports" / f"{session_id}-{kind}.json"


def write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def coerce_session_setting(key: str, value: str) -> Any:
    if key == "compact_keep_tail":
        try:
            parsed = int(value)
        except ValueError as exc:
            raise typer.BadParameter("compact_keep_tail must be a positive integer") from exc
        if parsed <= 0:
            raise typer.BadParameter("compact_keep_tail must be a positive integer")
        return parsed
    if key == "tool_execution_mode":
        normalized = value.strip().lower()
        if normalized not in SUPPORTED_TOOL_EXECUTION_MODES:
            raise typer.BadParameter(f"Unsupported tool execution mode: {normalized}")
        return normalized
    if key == "export_default_kind":
        normalized = value.strip().lower()
        if normalized not in SUPPORTED_EXPORT_KINDS:
            raise typer.BadParameter(f"Unsupported export kind: {normalized}")
        return normalized
    if key == "manifest_path":
        raw = value.strip()
        if not raw:
            raise typer.BadParameter("manifest_path is required")
        return raw
    if key == "model":
        normalized = value.strip()
        if not normalized:
            raise typer.BadParameter("model is required")
        return normalized
    if key in {"memory_exact_match_weight", "memory_token_overlap_weight", "memory_recency_weight"}:
        try:
            parsed = float(value)
        except ValueError as exc:
            raise typer.BadParameter(f"{key} must be a non-negative number") from exc
        if parsed < 0.0:
            raise typer.BadParameter(f"{key} must be a non-negative number")
        return parsed
    raise typer.BadParameter(f"Unsupported setting key: {key}")


@dataclass
class ChatCommandHandler:
    name: str
    matcher: Callable[[str], bool]
    executor: Callable[[str], bool]


class ChatCommandRegistry:
    def __init__(self) -> None:
        self._handlers: list[ChatCommandHandler] = []

    def register(self, handler: ChatCommandHandler) -> None:
        self._handlers.append(handler)

    def dispatch(self, command: str) -> bool:
        for handler in self._handlers:
            if handler.matcher(command):
                return bool(handler.executor(command))
        return False


def parse_extension_command(command: str) -> tuple[str, str] | None:
    if not command.startswith("/"):
        return None
    body = command[1:].strip()
    if not body:
        return None
    parts = body.split(maxsplit=1)
    name = parts[0].strip()
    if not name:
        return None
    argument_text = parts[1].strip() if len(parts) > 1 else ""
    return name, argument_text


def build_extension_command_payload(argument_text: str) -> dict[str, Any]:
    if not argument_text:
        return {}
    try:
        parsed = json.loads(argument_text)
    except json.JSONDecodeError:
        argv = shlex.split(argument_text)
        payload: dict[str, Any] = {"input": argument_text, "argv": argv}
        if len(argv) == 1:
            payload["target"] = argv[0]
        elif len(argv) > 1:
            payload["args"] = argv
        return payload

    if isinstance(parsed, dict):
        return parsed
    return {"input": parsed}


def parse_extension_command_result(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text or text[0] not in "[{":
        return value
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return value


@app.command()
def chat(
    cwd: str = typer.Option(".", help="Working directory"),
    resume: bool = typer.Option(False, help="Resume latest session"),
    provider: str = typer.Option("anthropic", help="Model provider"),
    model: str | None = typer.Option(None, help="Model id for the selected provider"),
    json_stream: bool = typer.Option(False, "--json-stream", help="Stream runtime events as JSONL"),
) -> None:
    working_dir = Path(cwd).resolve()
    session_overrides: dict[str, Any] = {}
    extensions: ExtensionManager | None = None

    session_store = SessionStore(get_sessions_dir())
    session_runtime = SessionRuntimeFacade.from_resume(session_store, resume)
    history, summary = session_runtime.load_history()

    def _approval_cb(command: str, reason: str) -> bool:
        _echo(f"\n[approval required] {reason}")
        _echo(f"  command: {command}")
        return typer.confirm("Allow this command?", default=False)

    def _rebuild_runtime() -> tuple[Any, Any]:
        nonlocal extensions
        current_settings = prepare_runtime_settings(
            working_dir,
            provider,
            model,
            session_overrides=session_overrides,
        )
        extensions = _build_extensions(working_dir, current_settings)
        runtime = create_chat_runtime(
            working_dir,
            session_store,
            current_settings.provider,
            current_settings.model,
            json_stream,
            compact_keep_tail=current_settings.compact_keep_tail,
            tool_execution_mode=current_settings.tool_execution_mode,
            approval_callback=_approval_cb,
            extensions=extensions,
            runtime_settings=current_settings,
        )
        return current_settings, runtime

    settings, agent_loop = _rebuild_runtime()

    _echo(f"session: {session_runtime.session_path}")
    _echo("Type /quit to exit")
    _echo("Built-in commands: /branch <label>, /switch <session>, /import <path>, /summary, /tree, /sessions, /inspect, /model [id], /settings [set|unset], /compact, /export [kind] [path], /quit")

    command_registry = ChatCommandRegistry()

    def _handle_summary(_: str) -> bool:
        session_id = session_runtime.session_id()
        latest_graph = session_runtime.task_graph()
        payload = {
            "inspect": {
                "session": str(session_runtime.session_path),
                "session_id": session_id,
                "summary": summary,
                "state": None,
                "tool_execution": summarize_tool_execution(latest_graph),
            }
        }
        emit_runtime_event(json_stream, session_id, "inspect_summary", payload)
        if not json_stream:
            emit_output(payload["inspect"], False)
        return True

    def _handle_inspect(_: str) -> bool:
        snapshot, graph = session_runtime.inspect()
        payload = {
            "inspect": {
                "session": str(session_runtime.session_path),
                "session_id": snapshot.header.id,
                "parent_id": snapshot.header.parent_id,
                "branch_label": snapshot.header.branch_label,
                "summary": snapshot.summary,
                "history_size": len(snapshot.history),
                "compaction": snapshot.compaction,
                "state": snapshot.state,
                "tool_execution": summarize_tool_execution(graph),
            }
        }
        emit_runtime_event(json_stream, snapshot.header.id, "inspect_session", payload)
        if not json_stream:
            emit_output(payload["inspect"], False)
        return True

    def _handle_tree(_: str) -> bool:
        payload = resolve_export_payload(session_store, session_runtime.session_path, "tree")
        session_id = session_runtime.session_id()
        emit_runtime_event(json_stream, session_id, "session_tree", payload)
        if not json_stream:
            emit_output(payload["tree"], False)
        return True

    def _handle_sessions(_: str) -> bool:
        items = session_runtime.list_sessions()
        session_id = session_runtime.session_id()
        payload = {"sessions": {"items": items, "session_id": session_id}}
        emit_runtime_event(json_stream, session_id, "session_list", payload)
        if not json_stream:
            emit_output({"sessions": items, "session_id": session_id}, False)
        return True

    def _handle_model_show(_: str) -> bool:
        if not json_stream:
            emit_output({"provider": settings.provider, "model": settings.model}, False)
        return True

    def _handle_model_set(command: str) -> bool:
        nonlocal settings, agent_loop
        next_model = command.removeprefix("/model ").strip()
        if not next_model:
            raise typer.BadParameter("model is required")
        session_overrides["model"] = next_model
        settings, agent_loop = _rebuild_runtime()
        if not json_stream:
            emit_output({"provider": settings.provider, "model": settings.model}, False)
        return True

    def _handle_settings_show(_: str) -> bool:
        if not json_stream:
            emit_output({"settings": get_runtime_settings_payload(settings)}, False)
        return True

    def _handle_settings_update(command: str) -> bool:
        nonlocal settings, agent_loop
        args = shlex.split(command.removeprefix("/settings "))
        if not args:
            raise typer.BadParameter("settings command is required")
        action = args[0]
        if action == "set":
            if len(args) < 3:
                raise typer.BadParameter("usage: /settings set <key> <value>")
            key = args[1]
            value = " ".join(args[2:])
            session_overrides[key] = coerce_session_setting(key, value)
            settings, agent_loop = _rebuild_runtime()
            if not json_stream:
                emit_output({"settings": get_runtime_settings_payload(settings)}, False)
            return True
        if action == "unset":
            if len(args) != 2:
                raise typer.BadParameter("usage: /settings unset <key>")
            key = args[1]
            if key not in {
                "compact_keep_tail",
                "tool_execution_mode",
                "export_default_kind",
                "manifest_path",
                "model",
                "memory_exact_match_weight",
                "memory_token_overlap_weight",
                "memory_recency_weight",
            }:
                raise typer.BadParameter(f"Unsupported setting key: {key}")
            session_overrides.pop(key, None)
            settings, agent_loop = _rebuild_runtime()
            if not json_stream:
                emit_output({"settings": get_runtime_settings_payload(settings)}, False)
            return True
        raise typer.BadParameter("settings command must be set or unset")

    def _handle_compact(_: str) -> bool:
        nonlocal history, summary
        history, summary, details = agent_loop.compact_now(
            session_runtime.session_path,
            history,
            summary,
            session_id=session_runtime.session_id(),
        )
        if not json_stream:
            emit_output({"compact": {"summary": summary, "history_size": len(history), **details}}, False)
        return True

    def _handle_export(command: str) -> bool:
        args = shlex.split(command.removeprefix("/export").strip()) if command != "/export" else []
        export_kind = args[0] if args else settings.export_default_kind
        if export_kind not in SUPPORTED_EXPORT_KINDS:
            raise typer.BadParameter(f"Unsupported export kind: {export_kind}")
        output_path = Path(args[1]).expanduser() if len(args) > 1 else default_export_output_path(working_dir, session_store, session_runtime.session_path, export_kind)
        if not output_path.is_absolute():
            output_path = (working_dir / output_path).resolve()
        payload = resolve_export_payload(session_store, session_runtime.session_path, export_kind)
        write_json_file(output_path, payload)
        if not json_stream:
            emit_output({"exported": str(output_path), "kind": export_kind}, False)
        return True

    def _handle_branch(command: str) -> bool:
        nonlocal history, summary
        branch_label = command.removeprefix("/branch ").strip()
        if not branch_label:
            raise typer.BadParameter("branch label is required")
        session_runtime.branch(branch_label)
        history, summary = session_runtime.load_history()
        payload = {"branch": {"session": str(session_runtime.session_path), "session_id": session_runtime.session_id(), "label": branch_label}}
        emit_runtime_event(json_stream, session_runtime.session_id(), "branch_created", payload)
        if not json_stream:
            emit_output({"branched": str(session_runtime.session_path), "session_id": session_runtime.session_id(), "branch_label": branch_label}, False)
        return True

    def _handle_switch(command: str) -> bool:
        nonlocal history, summary
        session_ref = command.removeprefix("/switch ").strip()
        if not session_ref:
            raise typer.BadParameter("session is required")
        switched_path = session_runtime.switch_session(session_ref)
        history, summary = session_runtime.load_history()
        payload = {"switch": {"session": str(switched_path), "session_id": session_runtime.session_id()}}
        emit_runtime_event(json_stream, session_runtime.session_id(), "session_switched", payload)
        if not json_stream:
            emit_output({"switched": str(switched_path), "session_id": session_runtime.session_id()}, False)
        return True

    def _handle_import(command: str) -> bool:
        nonlocal history, summary
        source_ref = command.removeprefix("/import ").strip()
        if not source_ref:
            raise typer.BadParameter("source session path is required")
        imported_path = session_runtime.import_session(source_ref)
        history, summary = session_runtime.load_history()
        payload = {"import": {"session": str(imported_path), "session_id": session_runtime.session_id(), "source": source_ref}}
        emit_runtime_event(json_stream, session_runtime.session_id(), "session_imported", payload)
        if not json_stream:
            emit_output({"imported": str(imported_path), "session_id": session_runtime.session_id(), "source": source_ref}, False)
        return True

    command_registry.register(ChatCommandHandler(name="summary", matcher=lambda c: c == "/summary", executor=_handle_summary))
    command_registry.register(ChatCommandHandler(name="inspect", matcher=lambda c: c == "/inspect", executor=_handle_inspect))
    command_registry.register(ChatCommandHandler(name="tree", matcher=lambda c: c == "/tree", executor=_handle_tree))
    command_registry.register(ChatCommandHandler(name="sessions", matcher=lambda c: c == "/sessions", executor=_handle_sessions))
    command_registry.register(ChatCommandHandler(name="model.show", matcher=lambda c: c == "/model", executor=_handle_model_show))
    command_registry.register(ChatCommandHandler(name="model.set", matcher=lambda c: c.startswith("/model "), executor=_handle_model_set))
    command_registry.register(ChatCommandHandler(name="settings.show", matcher=lambda c: c == "/settings", executor=_handle_settings_show))
    command_registry.register(ChatCommandHandler(name="settings.update", matcher=lambda c: c.startswith("/settings "), executor=_handle_settings_update))
    command_registry.register(ChatCommandHandler(name="compact", matcher=lambda c: c == "/compact", executor=_handle_compact))
    command_registry.register(ChatCommandHandler(name="export", matcher=lambda c: c.startswith("/export"), executor=_handle_export))
    command_registry.register(ChatCommandHandler(name="branch", matcher=lambda c: c.startswith("/branch "), executor=_handle_branch))
    command_registry.register(ChatCommandHandler(name="switch", matcher=lambda c: c.startswith("/switch "), executor=_handle_switch))
    command_registry.register(ChatCommandHandler(name="import", matcher=lambda c: c.startswith("/import "), executor=_handle_import))

    while True:
        user_input = typer.prompt("you")
        stripped = user_input.strip()
        if stripped == "/quit":
            break

        handled = False
        if stripped.startswith("/"):
            parsed_command = parse_extension_command(stripped)
            if parsed_command is not None:
                extension_name, argument_text = parsed_command
                try:
                    extension_payload = build_extension_command_payload(argument_text)
                    extension_result = extensions.get_provider("skill").execute(extension_name, extension_payload)
                except ValueError:
                    handled = False
                except Exception as exc:
                    session_id = session_runtime.session_id()
                    payload = {
                        "extension": {
                            "name": extension_name,
                            "arguments": extension_payload,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    }
                    emit_runtime_event(json_stream, session_id, "extension_error", payload)
                    handled = False
                else:
                    handled = True
                    parsed_result = parse_extension_command_result(extension_result)
                    session_id = session_runtime.session_id()
                    payload = {
                        "extension": {
                            "name": extension_name,
                            "arguments": extension_payload,
                            "result": parsed_result,
                        }
                    }
                    emit_runtime_event(json_stream, session_id, "extension_command", payload)
                    if not json_stream:
                        if isinstance(parsed_result, dict):
                            emit_output(parsed_result, False)
                        elif isinstance(parsed_result, list):
                            emit_output({"result": parsed_result}, False)
                        elif parsed_result is not None:
                            _echo(str(parsed_result))
            if handled:
                continue

            if command_registry.dispatch(stripped):
                continue

        reply, history, summary = agent_loop.run_turn(session_runtime.session_path, history, user_input, summary)
        if reply and not json_stream:
            _echo(f"assistant> {reply}")


@app.command()
def run(
    prompt: str = typer.Option(..., help="Single prompt to execute"),
    cwd: str = typer.Option(".", help="Working directory"),
    resume: bool = typer.Option(False, help="Resume latest session"),
    provider: str = typer.Option("anthropic", help="Model provider"),
    model: str | None = typer.Option(None, help="Model id for the selected provider"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON object"),
    json_stream: bool = typer.Option(False, "--json-stream", help="Stream runtime events as JSONL"),
) -> None:
    working_dir = Path(cwd).resolve()
    settings = prepare_runtime_settings(working_dir, provider, model)

    session_store = SessionStore(get_sessions_dir())
    session_runtime = SessionRuntimeFacade.from_resume(session_store, resume)
    reply, history, summary = run_single_prompt(
        working_dir,
        session_store,
        session_runtime,
        prompt,
        settings.provider,
        settings.model,
        json_stream,
        compact_keep_tail=settings.compact_keep_tail,
        tool_execution_mode=settings.tool_execution_mode,
        runtime_settings=settings,
    )
    graph = session_runtime.task_graph()
    snapshot = session_store.load_snapshot(session_runtime.session_path)
    payload = {
        "inspect": {
            "session": str(session_runtime.session_path),
            "session_id": snapshot.header.id,
            "summary": summary,
            "reply": reply,
            "history_size": len(history),
            "state": snapshot.state,
            "tool_execution": summarize_tool_execution(graph),
        }
    }
    if json_output:
        emit_output(payload, True)
    elif not json_stream and reply:
        _echo(reply)


@app.command()
def code_demo(
    cwd: str = typer.Option(".", help="Working directory"),
    json_output: bool = typer.Option(False, "--json", help="Print final JSON object"),
    json_stream: bool = typer.Option(False, "--json-stream", help="Stream runtime events as JSONL"),
) -> None:
    working_dir = Path(cwd).resolve()
    session_store = get_session_store(working_dir, use_global=False)
    workspace_dir, source_file, test_file = bootstrap_code_demo_workspace(working_dir)
    pytest_command = f'"{sys.executable}" -m pytest test_calculator.py'
    registry = build_registry(workspace_dir)
    runtime = build_runtime(
        build_code_demo_model(source_file, test_file, pytest_command),
        registry,
        session_store,
        event_sink=stream_sink(json_stream),
    )
    session_path = session_store.create()
    history: list[dict[str, object]] = []
    summary: str | None = None
    reply = ""

    for prompt in [
        "inspect the buggy source file",
        "inspect the failing pytest file",
        "run pytest to capture the failure",
        "fix the buggy add function",
        "run pytest again to verify the fix",
    ]:
        reply, history, summary = runtime.run_turn(session_path, history, prompt, summary)

    bash_runs = collect_tool_results(session_store, session_path, "bash")
    payload = {
        "inspect": {
            "session": str(session_path),
            "session_id": session_store.read_header(session_path).id,
            "source_file": str(source_file),
            "test_file": str(test_file),
            "workspace": str(workspace_dir),
            "reply": reply,
            "summary": summary,
        },
        "result": {
            "pytest_passed": any("1 passed" in run["content"] for run in bash_runs if run["status"] == "ok"),
            "bash_runs": bash_runs,
        },
        "events": {"items": [{"type": event.type, "payload": event.payload} for event in session_store.read_events(session_path)]},
    }
    emit_output(payload, json_output)


@app.command()
def demo(
    cwd: str = typer.Option(".", help="Working directory"),
    json_output: bool = typer.Option(False, "--json", help="Print final JSON object"),
    json_stream: bool = typer.Option(False, "--json-stream", help="Stream runtime events as JSONL"),
) -> None:
    working_dir = Path(cwd).resolve()
    session_store = get_session_store(working_dir, use_global=False)
    registry = build_registry(working_dir)
    runtime = build_runtime(
        SequenceModelClient(
            responses=[
                tool_response("demo-1", "write", {"path": "demo-notes.txt", "content": "hello from EchoWeave\n"}),
                AgentResponse(text="created demo-notes.txt"),
                tool_response("demo-2", "read", {"path": "demo-notes.txt"}),
                AgentResponse(text="read demo-notes.txt"),
                tool_response("demo-3", "edit", {"path": "demo-notes.txt", "old": "hello", "new": "updated"}),
                AgentResponse(text="updated demo-notes.txt"),
                tool_response("demo-4", "read", {"path": "demo-notes.txt"}),
                AgentResponse(text="confirmed updated content"),
                tool_response("demo-5", "grep", {"path": ".", "pattern": "updated", "glob": "demo-notes.txt"}),
                AgentResponse(text="found updated line"),
                tool_response("demo-6", "read", {"path": "missing.txt"}),
                AgentResponse(text="captured missing file error"),
            ]
        ),
        registry,
        session_store,
        event_sink=stream_sink(json_stream),
    )
    session_path = session_store.create()
    history: list[dict[str, object]] = []
    summary: str | None = None

    for prompt in [
        "create the demo file",
        "read back the demo file",
        "update the demo file",
        "confirm the updated file content",
        "search for the updated line",
        "try reading a missing file so error handling is visible",
    ]:
        _, history, summary = runtime.run_turn(session_path, history, prompt, summary)

    payload = {
        "inspect": {
            "session": str(session_path),
            "session_id": session_store.read_header(session_path).id,
            "file": str(working_dir / "demo-notes.txt"),
        },
        "sessions": {"items": [{"path": str(session_path)}]},
        "events": {"items": [{"type": event.type, "payload": event.payload} for event in session_store.read_events(session_path)]},
    }
    emit_output(payload, json_output)


def _build_inspect_demo_sessions(
    working_dir: Path,
    session_store: SessionStore,
    json_stream: bool,
) -> tuple[SessionRuntimeFacade, SessionRuntimeFacade, list[dict[str, Any]], str | None]:
    base_runtime = SessionRuntimeFacade(session_store, session_store.create())
    registry = build_registry(working_dir)
    runtime = build_runtime(
        CompactionDemoModelClient(),
        registry,
        session_store,
        event_sink=stream_sink(json_stream),
    )
    history: list[dict[str, Any]] = []
    summary: str | None = None

    for index in range(7):
        _, history, summary = runtime.run_turn(base_runtime.session_path, history, f"base-{index}", summary)

    branch_path = base_runtime.branch("experiment")
    branch_runtime = SessionRuntimeFacade(session_store, branch_path)
    branch_history, branch_summary = branch_runtime.load_history()
    echo_runtime = build_runtime(
        EchoTurnModelClient(),
        registry,
        session_store,
        event_sink=stream_sink(json_stream),
    )
    _, branch_history, branch_summary = echo_runtime.run_turn(
        branch_runtime.session_path,
        branch_history,
        "branch-follow-up",
        branch_summary,
    )
    return base_runtime, branch_runtime, branch_history, branch_summary


@app.command()
def inspect_session(
    cwd: str = typer.Option(".", help="Working directory"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON object"),
    json_stream: bool = typer.Option(False, "--json-stream", help="Stream runtime events as JSONL"),
) -> None:
    working_dir = Path(cwd).resolve()
    session_store = get_session_store(working_dir, use_global=False)

    base_runtime, branch_runtime, resumed_history, resumed_summary = _build_inspect_demo_sessions(
        working_dir,
        session_store,
        json_stream,
    )
    tree_runtime = build_session_browser_runtime(session_store)
    tree_nodes = tree_runtime.build_tree() if tree_runtime is not None else []

    base_snapshot, base_graph = base_runtime.inspect()
    base_events = session_store.read_events(base_runtime.session_path)
    branch_events = session_store.read_events(branch_runtime.session_path)
    branch_snapshot, branch_graph = branch_runtime.inspect()

    payload = {
        "inspect": {
            "base_session": str(base_runtime.session_path),
            "base_session_id": base_snapshot.header.id,
            "branch_session": str(branch_runtime.session_path),
            "branch_session_id": branch_snapshot.header.id,
            "base_event_count": len(base_events),
            "branch_event_count": len(branch_events),
            "compaction_seen": any(event.type == "compaction" for event in base_events),
            "branch_event_seen": any(event.type == "branch" for event in branch_events),
            "base_state": base_snapshot.state,
            "branch_state": branch_snapshot.state,
            "branch_summary": resumed_summary,
            "resumed_history_size": len(resumed_history),
            "base_graph_stats": base_graph["stats"],
            "branch_graph_stats": branch_graph["stats"],
            "tool_execution": {
                "base": summarize_tool_execution(base_graph),
                "branch": summarize_tool_execution(branch_graph),
            },
        },
        "tree": {"nodes": tree_nodes, "session_id": base_snapshot.header.id},
    }
    emit_runtime_event(json_stream, base_snapshot.header.id, "inspect_session", payload)
    if not json_stream:
        emit_output(payload, json_output)


@app.command()
def rpc(
    cwd: str = typer.Option(".", help="Working directory"),
    resume: bool = typer.Option(False, help="Resume latest session"),
    provider: str = typer.Option("anthropic", help="Model provider"),
    model: str | None = typer.Option(None, help="Model id for the selected provider"),
) -> None:
    working_dir = Path(cwd).resolve()
    settings = prepare_runtime_settings(working_dir, provider, model)

    session_store = SessionStore(get_sessions_dir())
    session_runtime = SessionRuntimeFacade.from_resume(session_store, resume)

    while True:
        try:
            raw = input()
        except EOFError:
            break
        if not raw.strip():
            continue
        request = json.loads(raw)
        request_id = request.get("id")
        command = request.get("type")
        runtime_context = resolve_rpc_runtime_context(request)
        try:
            if command == "prompt":
                prompt = str(request.get("prompt", ""))
                reply, history, summary = run_single_prompt(
                    working_dir,
                    session_store,
                    session_runtime,
                    prompt,
                    settings.provider,
                    settings.model,
                    True,
                    compact_keep_tail=settings.compact_keep_tail,
                    tool_execution_mode=settings.tool_execution_mode,
                    runtime_settings=settings,
                )
                graph = session_runtime.task_graph()
                payload = {
                    "request_id": request_id,
                    "command": command,
                    "result": {
                        "reply": reply,
                        "summary": summary,
                        "history_size": len(history),
                        "tool_execution": summarize_tool_execution(graph),
                        "session_id": session_runtime.session_id(),
                    },
                }
                _echo(build_runtime_event("rpc_response", session_runtime.session_id(), payload).to_json())
            elif command == "summary":
                _, summary = session_runtime.load_history()
                payload = {
                    "request_id": request_id,
                    "command": command,
                    "result": {"summary": summary, "session_id": session_runtime.session_id()},
                }
                _echo(build_runtime_event("rpc_response", session_runtime.session_id(), payload).to_json())
            elif command == "inspect":
                snapshot, graph = session_runtime.inspect()
                payload = {
                    "request_id": request_id,
                    "command": command,
                    "result": {
                        "session_id": snapshot.header.id,
                        "parent_id": snapshot.header.parent_id,
                        "branch_label": snapshot.header.branch_label,
                        "summary": snapshot.summary,
                        "history_size": len(snapshot.history),
                        "compaction": snapshot.compaction,
                        "state": snapshot.state,
                        "tool_execution": summarize_tool_execution(graph),
                    },
                }
                _echo(build_runtime_event("rpc_response", snapshot.header.id, payload).to_json())
            elif command == "sessions":
                items = session_runtime.list_sessions()
                payload = {
                    "request_id": request_id,
                    "command": command,
                    "result": {"sessions": items, "session_id": session_runtime.session_id()},
                }
                _echo(build_runtime_event("rpc_response", session_runtime.session_id(), payload).to_json())
            elif command == "tree":
                tree_nodes = session_runtime.build_tree()
                payload = {
                    "request_id": request_id,
                    "command": command,
                    "result": {"tree": tree_nodes, "session_id": session_runtime.session_id()},
                }
                _echo(build_runtime_event("rpc_response", session_runtime.session_id(), payload).to_json())
            elif command == "task_graph":
                by = str(request.get("by", "turn"))
                detail = bool(request.get("detail", False))
                graph = session_runtime.task_graph(by=by)
                result: dict[str, Any] = {
                    "stats": graph["stats"],
                    "node_count": len(graph["nodes"]),
                    "edge_count": len(graph["edges"]),
                    "session_id": session_runtime.session_id(),
                }
                if detail:
                    result["nodes"] = graph["nodes"]
                    result["edges"] = graph["edges"]
                payload = {"request_id": request_id, "command": command, "result": result}
                _echo(build_runtime_event("rpc_response", session_runtime.session_id(), payload).to_json())
            elif command == "checkpoint_create":
                label_value = request.get("label")
                label = str(label_value) if label_value is not None else None
                at_event_index = request.get("at_event_index")
                checkpoint = session_runtime.create_checkpoint(
                    label=label,
                    at_event_index=at_event_index,
                    runtime_context=runtime_context,
                )
                payload = {"request_id": request_id, "command": command, "result": {"checkpoint": checkpoint, "session_id": session_runtime.session_id()}}
                _echo(build_runtime_event("rpc_response", session_runtime.session_id(), payload).to_json())
            elif command == "checkpoint_list":
                checkpoints = session_runtime.list_checkpoints()
                payload = {
                    "request_id": request_id,
                    "command": command,
                    "result": {"checkpoints": checkpoints, "count": len(checkpoints), "session_id": session_runtime.session_id()},
                }
                _echo(build_runtime_event("rpc_response", session_runtime.session_id(), payload).to_json())
            elif command == "replay":
                checkpoint_id = str(request.get("checkpoint_id", "")).strip()
                if not checkpoint_id:
                    raise ValueError("checkpoint_id is required")
                until_event_index = request.get("until_event_index")
                fork = bool(request.get("fork", False))
                replay_result = session_runtime.replay_from_checkpoint(
                    checkpoint_id=checkpoint_id,
                    until_event_index=until_event_index,
                    fork=fork,
                    runtime_context=runtime_context,
                )
                replay_result["session_id"] = session_runtime.session_id()
                payload = {"request_id": request_id, "command": command, "result": replay_result}
                _echo(build_runtime_event("rpc_response", session_runtime.session_id(), payload).to_json())
            elif command == "branch":
                label = str(request.get("label", "")).strip()
                if not label:
                    raise ValueError("branch label is required")
                branch_path = session_runtime.branch(label)
                payload = {
                    "request_id": request_id,
                    "command": command,
                    "result": {
                        "session": str(branch_path),
                        "session_id": session_runtime.session_id(),
                        "label": label,
                    },
                }
                _echo(build_runtime_event("rpc_response", session_runtime.session_id(), payload).to_json())
            elif command == "new_session":
                new_session_path = session_runtime.new_session()
                payload = {
                    "request_id": request_id,
                    "command": command,
                    "result": {"session": str(new_session_path), "session_id": session_runtime.session_id()},
                }
                _echo(build_runtime_event("rpc_response", session_runtime.session_id(), payload).to_json())
            elif command == "switch":
                session_ref = str(request.get("session", "")).strip()
                if not session_ref:
                    raise ValueError("session is required")
                switched_path = session_runtime.switch_session(session_ref)
                payload = {
                    "request_id": request_id,
                    "command": command,
                    "result": {"session": str(switched_path), "session_id": session_runtime.session_id()},
                }
                _echo(build_runtime_event("rpc_response", session_runtime.session_id(), payload).to_json())
            elif command == "import":
                source_ref = str(request.get("source", "")).strip()
                if not source_ref:
                    raise ValueError("source is required")
                imported_path = session_runtime.import_session(source_ref)
                payload = {
                    "request_id": request_id,
                    "command": command,
                    "result": {
                        "session": str(imported_path),
                        "session_id": session_runtime.session_id(),
                        "source": source_ref,
                    },
                }
                _echo(build_runtime_event("rpc_response", session_runtime.session_id(), payload).to_json())
            else:
                raise ValueError(f"unknown rpc command: {command}")
        except Exception as exc:
            payload = {
                "request_id": request_id,
                "command": command,
                "error": f"{type(exc).__name__}: {exc}",
                "session_id": session_runtime.session_id(),
            }
            _echo(build_runtime_event("rpc_error", session_runtime.session_id(), payload).to_json())


@app.command("sessions-list")
def sessions_list(
    cwd: str = typer.Option(".", help="Working directory"),
    demo: bool = typer.Option(False, help="Browse demo sessions instead of chat sessions"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON object"),
    json_stream: bool = typer.Option(False, "--json-stream", help="Stream runtime events as JSONL"),
) -> None:
    working_dir = Path(cwd).resolve()
    session_store = get_session_store(working_dir, use_global=not demo)
    session_runtime = build_session_browser_runtime(session_store)
    items = session_runtime.list_sessions() if session_runtime is not None else list_session_items(session_store)
    stream_session_id = resolve_session_browser_stream_id(session_runtime)
    payload = {"sessions": {"items": items, "session_id": stream_session_id}}
    emit_runtime_event(json_stream, stream_session_id, "session_list", payload)
    if not json_stream:
        emit_output(payload, json_output)


@app.command("sessions-summary")
def sessions_summary(
    cwd: str = typer.Option(".", help="Working directory"),
    demo: bool = typer.Option(False, help="Browse demo sessions instead of chat sessions"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON object"),
    json_stream: bool = typer.Option(False, "--json-stream", help="Stream runtime events as JSONL"),
) -> None:
    working_dir = Path(cwd).resolve()
    session_store = get_session_store(working_dir, use_global=not demo)
    session_runtime = build_session_browser_runtime(session_store)
    payload = {
        "inspect": {
            "latest_session": str(session_runtime.session_path) if session_runtime is not None else None,
            "latest_session_id": session_runtime.session_id() if session_runtime is not None else None,
            "latest_summary": get_latest_summary(session_store),
            "state": None,
            "tool_execution": empty_tool_execution_stats(),
        }
    }
    if session_runtime is not None:
        latest_graph = session_runtime.task_graph()
        latest_snapshot = session_store.load_snapshot(session_runtime.session_path)
        payload["inspect"]["state"] = latest_snapshot.state
        payload["inspect"]["tool_execution"] = summarize_tool_execution(latest_graph)
    stream_session_id = resolve_session_browser_stream_id(session_runtime)
    emit_runtime_event(json_stream, stream_session_id, "inspect_summary", payload)
    if not json_stream:
        emit_output(payload, json_output)


@app.command("sessions-tree")
def sessions_tree(
    cwd: str = typer.Option(".", help="Working directory"),
    demo: bool = typer.Option(False, help="Browse demo sessions instead of chat sessions"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON object"),
    json_stream: bool = typer.Option(False, "--json-stream", help="Stream runtime events as JSONL"),
) -> None:
    working_dir = Path(cwd).resolve()
    session_store = get_session_store(working_dir, use_global=not demo)
    session_runtime = build_session_browser_runtime(session_store)
    nodes = session_runtime.build_tree() if session_runtime is not None else []
    stream_session_id = resolve_session_browser_stream_id(session_runtime)
    payload = {"tree": {"nodes": nodes, "session_id": stream_session_id}}
    emit_runtime_event(json_stream, stream_session_id, "session_tree", payload)
    if not json_stream:
        emit_output(payload, json_output)


def _build_extensions(working_dir: Path, settings: Any | None = None):
    kwargs: dict[str, Any] = {}
    if settings is not None:
        kwargs = {
            "memory_exact_match_weight": settings.memory_exact_match_weight,
            "memory_token_overlap_weight": settings.memory_token_overlap_weight,
            "memory_recency_weight": settings.memory_recency_weight,
        }
    return build_extension_manager(working_dir, **kwargs)


@app.command("tools-list")
def tools_list(
    cwd: str = typer.Option(".", help="Working directory"),
    query: str = typer.Option("", "--query", "-q", help="Filter tools by keyword"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON object"),
) -> None:
    working_dir = Path(cwd).resolve()
    registry = build_registry(working_dir)
    items = registry.list_with_sources()
    if query.strip():
        needle = query.strip().lower()
        items = [
            item
            for item in items
            if needle in str(item.get("name", "")).lower()
            or needle in str(item.get("description", "")).lower()
            or needle in str(item.get("source", "")).lower()
        ]
    payload = {"tools": {"items": items, "count": len(items), "query": query}}
    if json_output:
        emit_output(payload, True)
        return
    if not items:
        _echo("No matching tools.")
        return
    _echo(f"tools ({len(items)}):")
    for item in items:
        _echo(f"- {item['name']} [{item.get('source', 'unknown')}]: {item.get('description', '')}")


@app.command("patch-review")
def patch_review(
    cwd: str = typer.Option(".", help="Working directory"),
    action: str = typer.Argument("list", help="list/show/apply/discard/rollback"),
    patch_id: str | None = typer.Option(None, "--id", help="Patch id for show/apply/discard/rollback"),
    confirm: bool = typer.Option(False, "--confirm", help="Required for apply"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON object"),
) -> None:
    working_dir = Path(cwd).resolve()
    tool = PatchTool(working_dir)
    payload_args: dict[str, Any] = {"action": action}
    if patch_id:
        payload_args["id"] = patch_id
    if action == "apply":
        payload_args["confirm"] = confirm
    try:
        result = tool.execute(payload_args)
    except Exception as exc:
        payload = {"patch": {"ok": False, "action": action, "id": patch_id, "error": f"{type(exc).__name__}: {exc}"}}
        if json_output:
            emit_output(payload, True)
        else:
            _echo(payload["patch"]["error"])
        raise typer.Exit(code=1)
    payload = {"patch": {"ok": True, "action": action, "id": patch_id, "result": result}}
    if json_output:
        emit_output(payload, True)
    else:
        _echo(result)


@app.command("audit-inspect")
def audit_inspect(
    audit_log: str = typer.Option("logs/audit.jsonl", "--audit-log", help="Path to audit JSONL"),
    feedback_log: str | None = typer.Option(None, "--feedback-log", help="Optional backlog JSONL to append suggestions"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON object"),
) -> None:
    audit_path = Path(audit_log).resolve()
    events = read_audit_events(audit_path)
    metrics = compute_harness_metrics(events)
    suggestions = suggest_harness_improvements(events)
    written = write_feedback_backlog(feedback_log, suggestions, source_audit_log=str(audit_path)) if feedback_log else 0
    payload = {
        "audit": {
            "path": str(audit_path),
            "event_count": len(events),
            "metrics": metrics.to_dict(),
            "suggestions": [item.to_dict() for item in suggestions],
            "feedback_written": written,
        }
    }
    if json_output:
        emit_output(payload, True)
        return
    _echo(f"audit: {audit_path}")
    _echo(f"events: {len(events)}")
    _echo(f"metrics: {json.dumps(metrics.to_dict(), ensure_ascii=False)}")
    if suggestions:
        _echo("suggestions:")
        for item in suggestions:
            _echo(f"- P{item.priority} {item.kind}: {item.title}")
    if written:
        _echo(f"feedback_written: {written}")


@app.command("hardening-plan")
def hardening_plan(
    audit_log: str = typer.Option("logs/audit.jsonl", "--audit-log", help="Path to audit JSONL"),
    feedback_log: str | None = typer.Option(None, "--feedback-log", help="Backlog JSONL to append suggestions"),
    eval_out: str | None = typer.Option(None, "--eval-out", help="Write generated eval fixture JSON"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON object"),
) -> None:
    audit_path = Path(audit_log).resolve()
    events = read_audit_events(audit_path)
    suggestions = suggest_harness_improvements(events)
    feedback_written = write_feedback_backlog(feedback_log, suggestions, source_audit_log=str(audit_path)) if feedback_log else 0
    eval_written = write_eval_fixtures(eval_out, suggestions) if eval_out else 0
    payload = {
        "hardening": {
            "audit_log": str(audit_path),
            "suggestions": [item.to_dict() for item in suggestions],
            "feedback_written": feedback_written,
            "eval_fixture_written": eval_written,
            "eval_out": str(Path(eval_out).resolve()) if eval_out else None,
        }
    }
    if json_output:
        emit_output(payload, True)
        return
    _echo(f"hardening suggestions: {len(suggestions)}")
    _echo(f"feedback_written: {feedback_written}")
    _echo(f"eval_fixture_written: {eval_written}")


@app.command("sandbox-plan")
def sandbox_plan(
    cwd: str = typer.Option(".", help="Working directory"),
    command: str = typer.Argument("python -m pytest -q", help="Command to wrap"),
    image: str = typer.Option("python:3.12-slim", "--image", help="Docker image"),
    network: str = typer.Option("none", "--network", help="Docker network mode"),
    memory: str = typer.Option("512m", "--memory", help="Container memory limit"),
    cpus: str = typer.Option("1.0", "--cpus", help="Container CPU limit"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON object"),
) -> None:
    working_dir = Path(cwd).resolve()
    profile = DockerSandboxProfile(enabled=True, image=image, network=network, memory=memory, cpus=cpus)
    wrapped = profile.wrap_command(command, workspace=working_dir, cwd=working_dir)
    payload = {"sandbox": {"workspace": str(working_dir), "profile": profile.diagnostics(), "command": wrapped}}
    if json_output:
        emit_output(payload, True)
    else:
        _echo(" ".join(shlex.quote(part) for part in wrapped))


@app.command("complex-repo-verify")
def complex_repo_verify(
    target: str = typer.Option(".", "--target", help="Repository or workspace to inspect"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON object"),
) -> None:
    root = Path(target).resolve()
    if not root.exists() or not root.is_dir():
        raise typer.BadParameter(f"target must be an existing directory: {root}")
    files = _collect_verification_files(root)
    signals = _verification_signals(root, files)
    risks = _verification_risks(signals)
    payload = {
        "complex_repo_verify": {
            "target": str(root),
            "file_count": len(files),
            "signals": signals,
            "risks": risks,
            "recommended_eval": _recommended_complex_eval(signals, risks),
        }
    }
    if json_output:
        emit_output(payload, True)
        return
    _echo(f"target: {root}")
    _echo(f"files: {len(files)}")
    _echo("signals:")
    for key, value in signals.items():
        _echo(f"- {key}: {value}")
    if risks:
        _echo("risks:")
        for risk in risks:
            _echo(f"- {risk}")
    else:
        _echo("risks: none")


def _collect_verification_files(root: Path) -> list[Path]:
    ignored = {".git", ".venv", "venv", "__pycache__", "node_modules", ".pytest_cache", "dist", "build"}
    files: list[Path] = []
    for current_raw, dirnames, filenames in __import__("os").walk(root):
        dirnames[:] = [name for name in dirnames if name not in ignored]
        current = Path(current_raw)
        for filename in filenames:
            path = current / filename
            if path.suffix.lower() in {".pyc", ".png", ".jpg", ".jpeg", ".gif", ".zip", ".pdf", ".exe", ".dll"}:
                continue
            files.append(path)
            if len(files) >= 2000:
                return files
    return files


def _verification_signals(root: Path, files: list[Path]) -> dict[str, Any]:
    names = {path.name.lower() for path in files}
    suffixes: dict[str, int] = {}
    for path in files:
        suffix = path.suffix.lower() or "<none>"
        suffixes[suffix] = suffixes.get(suffix, 0) + 1
    return {
        "has_git": (root / ".git").exists(),
        "has_tests": any("test" in path.name.lower() or "tests" in path.parts for path in files),
        "has_python": ".py" in suffixes or "pyproject.toml" in names,
        "has_node": "package.json" in names,
        "has_java": ".java" in suffixes or "pom.xml" in names or "build.gradle" in names,
        "has_docker": "dockerfile" in names or "docker-compose.yml" in names,
        "has_ci": any(part in {".github", ".gitlab"} for path in files for part in path.parts),
        "top_suffixes": sorted(suffixes.items(), key=lambda item: (-item[1], item[0]))[:8],
    }


def _verification_risks(signals: dict[str, Any]) -> list[str]:
    risks: list[str] = []
    if not signals["has_tests"]:
        risks.append("missing obvious tests; require manual or generated regression checks before applying patches")
    if not signals["has_git"]:
        risks.append("not a git checkout; patch rollback backups are more important")
    if not signals["has_docker"]:
        risks.append("no Docker files detected; container sandbox verification may need a generic image")
    if not signals["has_ci"]:
        risks.append("no CI directory detected; local verification commands should be documented")
    return risks


def _recommended_complex_eval(signals: dict[str, Any], risks: list[str]) -> dict[str, Any]:
    commands: list[str] = []
    if signals["has_python"]:
        commands.append("python -m pytest -q")
    if signals["has_node"]:
        commands.append("npm test")
    if signals["has_java"]:
        commands.append("mvn test or ./gradlew test")
    if not commands:
        commands.append("run project-specific smoke tests")
    return {
        "commands": commands,
        "use_worker_patch": True,
        "require_patch_review": True,
        "require_docker_sandbox_for_untrusted_commands": True,
        "risk_count": len(risks),
    }


@app.command("corecoder-status")
def corecoder_status(
    cwd: str = typer.Option(".", help="Working directory"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON object"),
) -> None:
    working_dir = Path(cwd).resolve()
    registry = build_registry(working_dir)
    tool_names = {item["name"] for item in registry.list_with_sources()}
    features = [
        {"name": "unique_search_replace_edit", "status": "implemented", "evidence": "edit old_string/new_string requires exactly one match and returns diff"},
        {"name": "governed_write_with_diff", "status": "implemented", "evidence": "write returns diff for overwrite and supports overwrite=false"},
        {"name": "read_output_budget", "status": "implemented", "evidence": "read supports line ranges and max_chars"},
        {"name": "bash_policy_timeout_truncation_cd", "status": "implemented", "evidence": "bash classifies commands, blocks interactive commands, tracks cd, truncates output"},
        {"name": "read_only_sub_agent", "status": "implemented", "evidence": "agent tool supports explore/plan/verify/summarize and optional isolated model summary"},
        {"name": "isolated_worker_agent_patch", "status": "implemented", "evidence": "agent worker role applies deterministic edits in a temporary workspace and returns a patch without mutating the real workspace"},
        {"name": "confirmed_patch_apply_rollback", "status": "implemented", "evidence": "patch tool stages diffs, requires confirm=true to apply, and keeps rollback backups"},
        {"name": "tool_discovery", "status": "implemented", "evidence": "tool_search and tools-list expose registered capabilities"},
        {"name": "todo_task_tracking", "status": "implemented", "evidence": "todo tool persists task status with at most one in_progress item"},
        {"name": "dynamic_system_prompt", "status": "implemented", "evidence": "provider calls include runtime prompt context"},
        {"name": "project_instruction_loading", "status": "implemented", "evidence": "ECHOWEAVE.md/AGENTS.md/.echoweave/instructions.md/CLAUDE.md are loaded as bounded project guidance"},
        {"name": "untrusted_rag_context", "status": "implemented", "evidence": "RAG/memory blocks mark prompt-injection-like content as untrusted"},
        {"name": "compaction_checkpoint_summary", "status": "implemented", "evidence": "compaction summary preserves user goals, tool state, errors, recent tail"},
        {"name": "history_tool_output_snip", "status": "implemented", "evidence": "large historical tool_result content is head/tail snipped before provider calls"},
        {"name": "parallel_conflict_governance", "status": "implemented", "evidence": "parallel mode downgrades unsafe write/command tool batches to sequential"},
        {"name": "streaming_eager_safe_tools", "status": "implemented", "evidence": "streaming mode executes completed safe read-only tool calls before message_done and defers side-effect tools"},
        {"name": "behavior_scorecard_eval", "status": "implemented", "evidence": "eval cases can score answer quality, tool correctness, RAG hits, policy blocks, and sandbox blocks"},
        {"name": "eval_hardening_backlog", "status": "implemented", "evidence": "failed eval scorecard criteria can be converted into feedback backlog suggestions"},
        {"name": "session_capability_policy", "status": "implemented", "evidence": "harness policy evaluates session model allowlist, skill allowlist, and RAG enablement"},
        {"name": "policy_risk_classification", "status": "implemented", "evidence": "shell policy decisions include command category and risk_level for UI/audit presentation"},
        {"name": "productized_patch_audit_cli", "status": "implemented", "evidence": "patch-review, audit-inspect, hardening-plan, and sandbox-plan expose review/governance workflows"},
        {"name": "docker_sandbox_profile", "status": "implemented", "evidence": "bash can wrap commands in a restricted docker run when ECHOWEAVE_SANDBOX_MODE=docker"},
        {"name": "multi_worker_orchestration", "status": "implemented", "evidence": "workers tool plans/runs isolated subtasks and reports write conflicts before merge"},
        {"name": "complex_repo_verification", "status": "implemented", "evidence": "complex-repo-verify inspects larger repositories and emits risk-focused verification fixtures"},
    ]
    payload = {
        "corecoder": {
            "workspace": str(working_dir),
            "tools_present": sorted(tool_names),
            "features": features,
            "implemented_count": sum(1 for item in features if item["status"] == "implemented"),
            "count": len(features),
        }
    }
    if json_output:
        emit_output(payload, True)
        return
    _echo("CoreCoder-style runtime status:")
    for item in features:
        _echo(f"- [{item['status']}] {item['name']}: {item['evidence']}")


@app.command("skills-list")
def skills_list(
    cwd: str = typer.Option(".", help="Working directory"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON object"),
) -> None:
    working_dir = Path(cwd).resolve()
    extensions = _build_extensions(working_dir)
    skills = [
        {
            "name": spec.name,
            "description": spec.description,
            "input_schema": spec.input_schema,
            "source": getattr(spec, "source", "builtin"),
        }
        for spec in extensions.get_provider("skill").list_skills()
    ]
    payload = {
        "skills": {
            "items": skills,
            "count": len(skills),
        }
    }
    if json_output:
        emit_output(payload, True)
        return
    if not skills:
        _echo("No skills configured.")
        return
    _echo(f"skills ({len(skills)}):")
    for item in skills:
        _echo(f"- {item['name']} [{item['source']}]: {item['description']}")


@app.command("mcp-list")
def mcp_list(
    cwd: str = typer.Option(".", help="Working directory"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON object"),
) -> None:
    working_dir = Path(cwd).resolve()
    extensions = _build_extensions(working_dir)
    servers = [
        {
            "name": server.name,
            "command": server.command,
            "args": server.args,
            "env": server.env,
            "timeout_seconds": server.timeout_seconds,
            "status": "configured",
        }
        for server in extensions.get_provider("mcp").list_servers()
    ]
    diagnostics: dict[str, Any] = {"status": "unknown"}
    diagnostics_fn = getattr(extensions.get_provider("mcp"), "diagnostics", None)
    if callable(diagnostics_fn):
        diagnostics = diagnostics_fn()
    payload = {
        "mcp": {
            "servers": servers,
            "count": len(servers),
            "diagnostics": diagnostics,
        }
    }
    if json_output:
        emit_output(payload, True)
        return
    if not servers:
        _echo("No MCP servers configured.")
        if diagnostics:
            _echo(f"diagnostics: {json.dumps(diagnostics, ensure_ascii=False)}")
        return
    _echo(f"mcp servers ({len(servers)}):")
    for server in servers:
        _echo(
            f"- {server['name']} [{server['status']}]: {server['command']} {' '.join(server['args'])}"
            .rstrip()
        )
    if diagnostics:
        _echo(f"diagnostics: {json.dumps(diagnostics, ensure_ascii=False)}")


@app.command("mcp-ping")
def mcp_ping(
    server: str = typer.Option(..., "--server", help="MCP server name"),
    cwd: str = typer.Option(".", help="Working directory"),
    method: str = typer.Option("ping", "--method", help="Method used for ping request"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON object"),
) -> None:
    import time

    working_dir = Path(cwd).resolve()
    extensions = _build_extensions(working_dir)
    started = time.perf_counter()
    try:
        result = extensions.get_provider("mcp").call(server=server, method=method, params={})
    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        payload = {
            "mcp": {
                "server": server,
                "method": method,
                "status": "error",
                "latency_ms": elapsed_ms,
                "error": f"{type(exc).__name__}: {exc}",
            }
        }
        if json_output:
            emit_output(payload, True)
        else:
            _echo(f"mcp ping failed: {server}/{method}")
            _echo(payload["mcp"]["error"])
            _echo(f"latency_ms: {elapsed_ms}")
        raise typer.Exit(code=1)

    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    payload = {
        "mcp": {
            "server": server,
            "method": method,
            "status": "ok",
            "latency_ms": elapsed_ms,
            "result": result,
        }
    }
    if json_output:
        emit_output(payload, True)
        return
    _echo(f"mcp ping ok: {server}/{method}")
    _echo(f"latency_ms: {elapsed_ms}")
    _echo(str(result))


@app.command("eval")
def eval_command(
    cwd: str = typer.Option(".", help="Working directory"),
    cases_file: str | None = typer.Option(None, "--cases", help="Path to eval cases JSON/JSONL"),
    provider: str = typer.Option("anthropic", help="Model provider"),
    model: str | None = typer.Option(None, help="Model id for the selected provider"),
    feedback_log: str | None = typer.Option(None, "--feedback-log", help="Optional JSONL path for eval hardening suggestions"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON object"),
) -> None:
    working_dir = Path(cwd).resolve()
    settings = prepare_runtime_settings(working_dir, provider, model)

    cases = load_eval_cases(cases_file)
    session_store = SessionStore(get_sessions_dir())

    case_results: list[dict[str, Any]] = []
    success_count = 0
    total_steps = 0
    total_retries = 0
    total_policy_blocks = 0
    scored_case_count = 0
    total_behavior_score = 0.0
    merged_error_types: dict[str, int] = {}
    hardening_suggestions = []

    for case in cases:
        session_path = session_store.create()
        history: list[dict[str, Any]] = []
        summary: str | None = None
        event_lines: list[str] = []

        runtime = create_agent_runtime(
            working_dir,
            session_store,
            settings.provider,
            settings.model,
            compact_keep_tail=settings.compact_keep_tail,
            tool_execution_mode=settings.tool_execution_mode,
            event_sink=event_lines.append,
            runtime_settings=settings,
        )

        session_id = session_store.read_header(session_path).id
        started_payload = {
            "case": {
                "id": case["id"],
                "prompt": case["prompt"],
                "session": str(session_path),
                "session_id": session_id,
            }
        }
        session_store.append(session_path, "eval.case_started", started_payload["case"])
        _echo(build_runtime_event("eval.case_started", session_id, started_payload).to_json())

        reply, history, summary = runtime.run_turn(
            session_path,
            history,
            case["prompt"],
            summary,
        )

        runtime_events = parse_runtime_event_lines(event_lines)
        metrics = summarize_runtime_events(runtime_events)

        expected_contains = case.get("expected_contains")
        expectation_ok = True
        if isinstance(expected_contains, str) and expected_contains:
            expectation_ok = expected_contains in reply

        scorecard = score_eval_case(case, reply=reply, runtime_events=runtime_events)
        if scorecard.overall_score is not None:
            scored_case_count += 1
            total_behavior_score += scorecard.overall_score
        if not scorecard.passed:
            hardening_suggestions.extend(suggest_eval_hardening(case["id"], scorecard))

        case_success = bool(metrics["success"] and expectation_ok and scorecard.passed)
        success_count += 1 if case_success else 0
        total_steps += int(metrics["step_count"])
        total_retries += int(metrics["retry_count"])
        total_policy_blocks += int(metrics["policy_block_count"])
        for item in metrics["error_types"]:
            name = str(item.get("type", "unknown"))
            count = int(item.get("count", 0))
            merged_error_types[name] = merged_error_types.get(name, 0) + count

        case_result = {
            "id": case["id"],
            "prompt": case["prompt"],
            "session": str(session_path),
            "session_id": session_id,
            "reply": reply,
            "summary": summary,
            "success": case_success,
            "expectation_ok": expectation_ok,
            "expected_contains": expected_contains,
            "scorecard": scorecard.to_dict(),
            "metrics": metrics,
            "history_size": len(history),
        }
        case_results.append(case_result)

        finished_payload = {
            "case": {
                "id": case["id"],
                "session_id": session_id,
                "success": case_success,
                "metrics": metrics,
            }
        }
        session_store.append(
            session_path,
            "eval.case_finished",
            {
                "id": case["id"],
                "success": case_success,
                "metrics": metrics,
            },
        )
        _echo(build_runtime_event("eval.case_finished", session_id, finished_payload).to_json())

    case_count = len(cases)
    sorted_errors = sorted(merged_error_types.items(), key=lambda pair: (-pair[1], pair[0]))
    run_summary = {
        "session_id": "eval-run",
        "case_count": case_count,
        "success_count": success_count,
        "success_rate": (success_count / case_count) if case_count else 0.0,
        "avg_step_count": (total_steps / case_count) if case_count else 0.0,
        "avg_retry_count": (total_retries / case_count) if case_count else 0.0,
        "policy_block_rate": (total_policy_blocks / case_count) if case_count else 0.0,
        "avg_behavior_score": (total_behavior_score / scored_case_count) if scored_case_count else None,
        "scored_case_count": scored_case_count,
        "error_types_top": [{"type": name, "count": count} for name, count in sorted_errors[:5]],
        "total_error_type_kinds": len(merged_error_types),
        "hardening_suggestion_count": len(hardening_suggestions),
    }
    if feedback_log:
        run_summary["hardening_backlog_written"] = write_feedback_backlog(
            feedback_log,
            hardening_suggestions,
            source_audit_log=None,
        )

    run_event_payload = {"eval": run_summary}
    _echo(build_runtime_event("eval.run_finished", "eval-run", run_event_payload).to_json())

    payload = {
        "eval": run_summary,
        "cases": case_results,
    }
    emit_output(payload, json_output)


@app.command("tui")
def tui(
    cwd: str = typer.Option(".", help="Working directory"),
    resume: bool = typer.Option(False, help="Resume latest session"),
    provider: str = typer.Option("anthropic", help="Model provider"),
    model: str | None = typer.Option(None, help="Model id for the selected provider"),
) -> None:
    working_dir = Path(cwd).resolve()
    settings = prepare_runtime_settings(working_dir, provider, model)

    session_store = SessionStore(get_sessions_dir())
    session_runtime = SessionRuntimeFacade.from_resume(session_store, resume)
    history, summary = session_runtime.load_history()

    event_lines: list[str] = []

    def event_sink(line: str) -> None:
        event_lines.append(line)

    def _approval_cb(command: str, reason: str) -> bool:
        _echo(f"\n[approval required] {reason}")
        _echo(f"  command: {command}")
        return typer.confirm("Allow this command?", default=False)

    runtime = create_agent_runtime(
        working_dir,
        session_store,
        settings.provider,
        settings.model,
        compact_keep_tail=settings.compact_keep_tail,
        tool_execution_mode=settings.tool_execution_mode,
        event_sink=event_sink,
        approval_callback=_approval_cb,
        runtime_settings=settings,
    )

    _echo(f"tui session: {session_runtime.session_path}")
    _echo("TUI commands: /summary, /sessions, /quit")

    while True:
        user_input = typer.prompt("tui> you")
        if user_input.strip() == "/quit":
            break
        if user_input.strip() == "/summary":
            emit_output(
                {
                    "latest_summary": summary,
                    "session": str(session_runtime.session_path),
                    "session_id": session_runtime.session_id(),
                },
                False,
            )
            continue
        if user_input.strip() == "/sessions":
            items = session_runtime.list_sessions()
            emit_output({"sessions": items, "session_id": session_runtime.session_id()}, False)
            continue

        reply, history, summary = runtime.run_turn(session_runtime.session_path, history, user_input, summary)

        if reply:
            _echo(f"assistant> {reply}")

        if event_lines:
            _echo("events>")
            for line in event_lines:
                _echo(line)
            event_lines.clear()


@package_app.command("install")
def package_install(
    name: str = typer.Argument(..., help="Package name"),
    version: str | None = typer.Option(None, "--version", help="Package version"),
    source: str = typer.Option("local", "--source", help="Package source label"),
    cwd: str = typer.Option(".", help="Working directory"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON object"),
) -> None:
    working_dir = Path(cwd).resolve()
    settings = prepare_runtime_settings(working_dir, None, None, validate_credentials=False)
    manager = PackageManager(settings.manifest_path)
    package = manager.install(name, version=version, source=source)
    emit_output({"package": package, "manifest_path": str(settings.manifest_path)}, json_output)


@package_app.command("list")
def package_list(
    cwd: str = typer.Option(".", help="Working directory"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON object"),
) -> None:
    working_dir = Path(cwd).resolve()
    settings = prepare_runtime_settings(working_dir, None, None, validate_credentials=False)
    manager = PackageManager(settings.manifest_path)
    packages = manager.list()
    emit_output(
        {"packages": {"items": packages, "count": len(packages)}, "manifest_path": str(settings.manifest_path)},
        json_output,
    )


@package_app.command("remove")
def package_remove(
    name: str = typer.Argument(..., help="Package name"),
    cwd: str = typer.Option(".", help="Working directory"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON object"),
) -> None:
    working_dir = Path(cwd).resolve()
    settings = prepare_runtime_settings(working_dir, None, None, validate_credentials=False)
    manager = PackageManager(settings.manifest_path)
    removed = manager.remove(name)
    if removed is None:
        raise typer.BadParameter(f"package not installed: {name}")
    emit_output({"removed": removed, "manifest_path": str(settings.manifest_path)}, json_output)


if __name__ == "__main__":
    app()
