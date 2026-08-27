from __future__ import annotations

import json
import secrets
from pathlib import Path

import typer

from echoweave_social.composition import build_backend, create_adapter
from echoweave_social.config import EchoWeaveConfig


app = typer.Typer(add_completion=False)


@app.command()
def init(
    output: str = typer.Option("config.local.json", "--output", help="Config file to create"),
    workspace: str = typer.Option(
        "D:\\develop\\agent\\EchoWeave",
        "--workspace",
        help="Default coding workspace for EchoWeave",
    ),
    adapter: str = typer.Option("onebot-v11", help="generic/astrbot/onebot-v11/feishu/wechat-official"),
    provider: str = typer.Option("demo", help="EchoWeave provider: demo/anthropic/openai"),
    onebot_api_url: str = typer.Option(
        "",
        help="OneBot HTTP API base URL",
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite existing config file"),
) -> None:
    output_path = Path(output).resolve()
    if output_path.exists() and not force:
        raise typer.BadParameter(f"Config already exists: {output_path}. Use --force to overwrite.")
    workspace_path = Path(workspace).resolve()
    base_dir = output_path.parent
    config = {
        "adapter": adapter,
        "workspace": str(workspace_path),
        "provider": provider,
        "model": None,
        "ai_providers": {},
        "model_profiles": {
            "demo-echo": {"provider": "demo", "model": None, "label": "Demo / 本地 Echo"},
            "deepseek-chat": {"provider": "deepseek", "model": "deepseek-chat", "label": "DeepSeek Chat"},
            "openai-gpt-4.1-mini": {"provider": "openai", "model": "gpt-4.1-mini", "label": "OpenAI GPT-4.1 mini"},
            "ollama-qwen-coder": {"provider": "ollama", "model": "qwen2.5-coder:7b", "label": "Ollama 本地 Qwen Coder"}
        },
        "default_model_profile": "deepseek-chat" if provider == "deepseek" else "demo-echo",
        "rag_enabled": False,
        "rag_backend": "pgvector_hybrid_bgem3",
        "rag_pgvector_dsn": None,
        "rag_pgvector_table": "echoweave_rag_chunks",
        "rag_embedding_model": "BAAI/bge-m3",
        "rag_auto_index": False,
        "rag_vector_weight": 0.65,
        "rag_bm25_weight": 0.35,
        "rag_query_rewrite_enabled": False,
        "rag_query_rewrite_strategy": "local_multi_query",
        "rag_query_rewrite_max_queries": 3,
        "rag_rerank_enabled": False,
        "rag_rerank_strategy": "bm25",
        "rag_rerank_candidate_multiplier": 4,
        "rag_rerank_original_score_weight": 0.65,
        "rag_rerank_bm25_weight": 0.35,
        "global_enabled_skills": ["search_workspace"],
        "session_enabled_skills": [],
        "state_path": str(base_dir / "echoweave-state.json"),
        "sandbox_root": str(base_dir / "sandboxes"),
        "host": "127.0.0.1",
        "port": 8787,
        "webhook_token": secrets.token_urlsafe(32),
        "web_allow_url_token": False,
        "web_session_ttl_seconds": 28800,
        "onebot_api_url": onebot_api_url or None,
        "onebot_access_token": None,
        "sse_enabled": True,
        "log_file": str(base_dir / "logs" / "echoweave.log"),
        "admins": [],
        "allowed_users": [],
        "allowed_groups": [],
        "blocked_users": [],
        "require_mention_in_group": False,
        "bot_ids": [],
        "admin_only_commands": ["approve", "approvals", "bind", "deny", "rag:index", "retry", "revoke", "skill:global"],
        "approval_timeout_seconds": 3600,
        "orphan_recovery_enabled": False,
        "orphan_recovery_scan_interval_seconds": 30.0,
        "orphan_recovery_max_per_scan": 4,
        "orphan_recovery_max_attempts_per_turn": 3,
        "harness_audit_enabled": True,
        "harness_audit_path": str(base_dir / "logs" / "audit.jsonl"),
        "harness_policy": {
            "allowed_tools": [],
            "denied_tools": [],
            "allowed_paths": [],
            "denied_paths": [],
            "command_allow_patterns": [],
            "command_approval_patterns": [],
            "command_deny_patterns": [],
            "session_model_allowlist": [],
            "session_skill_allowlist": [],
            "session_rag_enabled": None
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    (base_dir / "logs").mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    typer.echo(f"EchoWeave config created: {output_path}")
    typer.echo("Webhook URL for NapCat:")
    typer.echo("http://127.0.0.1:8787/")
    typer.echo("Use Authorization: Bearer <webhook_token> when the platform supports custom headers.")


@app.command()
def once(
    text: str = typer.Option(..., "--text", help="Incoming message text"),
    adapter: str = typer.Option("generic", help="generic/astrbot/onebot-v11/feishu/wechat-official"),
    cwd: str = typer.Option(".", help="Default coding workspace"),
    provider: str = typer.Option("demo", help="EchoWeave provider: demo/anthropic/openai"),
    model: str | None = typer.Option(None, help="Optional model id"),
    session_id: str = typer.Option("local", help="Generic conversation id"),
    sender_id: str = typer.Option("user", help="Generic sender id"),
    state_path: str | None = typer.Option(None, help="State file path"),
    sandbox_root: str | None = typer.Option(None, help="Conversation sandbox root"),
    json_output: bool = typer.Option(False, "--json", help="Print adapter payload as JSON"),
) -> None:
    platform = create_adapter(adapter)
    payload = {
        "platform": platform.name,
        "session_id": session_id,
        "sender_id": sender_id,
        "text": text,
    }
    event = platform.event_from_payload(payload)
    reply = build_backend(cwd, provider, model, state_path, None, sandbox_root, rag_enabled=False).handle(event)
    outbound = platform.payload_from_reply(reply)
    if json_output:
        typer.echo(json.dumps(outbound, ensure_ascii=False))
    else:
        typer.echo(reply.text)


if __name__ == "__main__":
    app()
