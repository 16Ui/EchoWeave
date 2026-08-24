from __future__ import annotations

from pathlib import Path

import typer

from echoweave_social.cli import init, once
from echoweave_social.composition import build_backend, create_adapter, setup_logging
from echoweave_social.config import EchoWeaveConfig
from echoweave_social.onebot_client import OneBotHttpClient
from echoweave_web.server import HubWebhookServer


app = typer.Typer(add_completion=False)
app.command()(init)
app.command()(once)


@app.command()
def webhook(
    config: str | None = typer.Option(None, "--config", help="JSON config file path"),
    adapter: str = typer.Option("generic", help="generic/astrbot/onebot-v11/feishu/wechat-official"),
    cwd: str = typer.Option(".", help="Default coding workspace"),
    provider: str = typer.Option("demo", help="EchoWeave provider: demo/anthropic/openai"),
    model: str | None = typer.Option(None, help="Optional model id"),
    host: str = typer.Option("127.0.0.1", help="Host to bind"),
    port: int = typer.Option(8787, help="Port to bind"),
    state_path: str | None = typer.Option(None, help="State file path"),
    sandbox_root: str | None = typer.Option(None, help="Conversation sandbox root"),
    webhook_token: str | None = typer.Option(None, help="Require this bearer/header token and web login password"),
    onebot_api_url: str | None = typer.Option(None, help="OneBot HTTP API base URL"),
    onebot_access_token: str | None = typer.Option(None, help="OneBot HTTP API access token"),
    log_file: str | None = typer.Option(None, help="Optional log file path"),
) -> None:
    cfg = EchoWeaveConfig.load(config)
    final_adapter = adapter if adapter != "generic" or cfg.adapter == "generic" else cfg.adapter
    final_workspace = Path(cwd).resolve() if cwd != "." else cfg.workspace.resolve()
    final_provider = provider if provider != "demo" or cfg.provider == "demo" else cfg.provider
    final_model = model if model is not None else cfg.model
    final_host = host if host != "127.0.0.1" or cfg.host == "127.0.0.1" else cfg.host
    final_port = port if port != 8787 or cfg.port == 8787 else cfg.port
    final_state_path = Path(state_path).resolve() if state_path else cfg.state_path
    final_sandbox_root = Path(sandbox_root).resolve() if sandbox_root else cfg.sandbox_root
    final_token = webhook_token if webhook_token is not None else cfg.webhook_token
    final_onebot_url = onebot_api_url if onebot_api_url is not None else cfg.onebot_api_url
    final_onebot_token = onebot_access_token if onebot_access_token is not None else cfg.onebot_access_token
    final_log_file = Path(log_file).resolve() if log_file else cfg.log_file
    final_user_store_path = (
        (final_state_path.parent / "echoweave-users.json")
        if final_state_path
        else (final_workspace / "echoweave-data" / "echoweave-users.json")
    )
    setup_logging(final_log_file)
    onebot_client = OneBotHttpClient(final_onebot_url, final_onebot_token) if final_onebot_url else None
    server = HubWebhookServer(
        create_adapter(final_adapter),
        build_backend(
            str(final_workspace),
            final_provider,
            final_model,
            str(final_state_path) if final_state_path else None,
            str(Path(config).resolve()) if config else None,
            str(final_sandbox_root) if final_sandbox_root else None,
            cfg.ai_providers,
            cfg.model_profiles,
            cfg.default_model_profile,
            cfg.rag_enabled,
            cfg.rag_backend,
            cfg.rag_pgvector_dsn,
            cfg.rag_pgvector_table,
            cfg.rag_embedding_model,
            cfg.rag_auto_index,
            cfg.rag_vector_weight,
            cfg.rag_bm25_weight,
            cfg.rag_query_rewrite_enabled,
            cfg.rag_query_rewrite_strategy,
            cfg.rag_query_rewrite_max_queries,
            cfg.rag_rerank_enabled,
            cfg.rag_rerank_strategy,
            cfg.rag_rerank_candidate_multiplier,
            cfg.rag_rerank_original_score_weight,
            cfg.rag_rerank_bm25_weight,
            cfg.global_enabled_skills,
            cfg.session_enabled_skills,
            cfg.admins,
            cfg.allowed_users,
            cfg.allowed_groups,
            cfg.blocked_users,
            cfg.require_mention_in_group,
            cfg.bot_ids,
            cfg.admin_only_commands,
            cfg.approval_timeout_seconds,
            cfg.harness_audit_enabled,
            str(cfg.harness_audit_path) if cfg.harness_audit_path else None,
            cfg.harness_policy,
        ),
        webhook_token=final_token,
        onebot_client=onebot_client,
        sse_enabled=cfg.sse_enabled,
        allow_url_token=cfg.web_allow_url_token,
        session_ttl_seconds=cfg.web_session_ttl_seconds,
        user_store_path=final_user_store_path,
    )
    server.serve(host=final_host, port=final_port)


if __name__ == "__main__":
    app()
