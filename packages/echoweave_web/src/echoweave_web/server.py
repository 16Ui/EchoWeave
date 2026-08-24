from __future__ import annotations

import json
import logging
import secrets
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
import xml.etree.ElementTree as ET

from echoweave_social.adapters.base import PlatformAdapter
from echoweave_social.backend import AgentBackend
from echoweave_social.event_bus import EventBus
from echoweave_social.onebot_client import OneBotHttpClient
from echoweave_social.schema import EchoWeaveEvent
from echoweave_web.auth import AuthUser, JwtUserStore
from echoweave_web.templates import admin_html, login_html, user_html


__all__ = ["HubWebhookServer", "_read_chunked_body", "re_match_approval_action"]


class HubWebhookServer:
    def __init__(
        self,
        adapter: PlatformAdapter,
        backend: AgentBackend,
        *,
        webhook_token: str | None = None,
        onebot_client: OneBotHttpClient | None = None,
        event_bus: EventBus | None = None,
        sse_enabled: bool = True,
        allow_url_token: bool = False,
        session_ttl_seconds: int = 28800,
        user_store_path: str | Path | None = None,
    ) -> None:
        self.adapter = adapter
        self.backend = backend
        self.webhook_token = webhook_token
        self.onebot_client = onebot_client
        self.event_bus = event_bus or EventBus()
        self.sse_enabled = sse_enabled
        self.allow_url_token = allow_url_token
        self.session_ttl_seconds = max(60, int(session_ttl_seconds or 28800))
        self.user_store_path = Path(user_store_path).expanduser().resolve() if user_store_path else _default_user_store_path(backend)
        self.user_store = JwtUserStore(self.user_store_path, fallback_secret=webhook_token)
        self.logger = logging.getLogger("echoweave.web")

    def build_server(self, host: str = "127.0.0.1", port: int = 8787) -> ThreadingHTTPServer:
        adapter = self.adapter
        backend = self.backend
        webhook_token = self.webhook_token
        onebot_client = self.onebot_client
        event_bus = self.event_bus
        sse_enabled = self.sse_enabled
        allow_url_token = self.allow_url_token
        session_ttl_seconds = self.session_ttl_seconds
        user_store = self.user_store
        logger = self.logger

        class Handler(BaseHTTPRequestHandler):
            session_cookie_name = "echoweave_session"

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path == "/healthz":
                    self._write_json(
                        200,
                        {
                            "ok": True,
                            "service": "EchoWeave",
                            "adapter": adapter.name,
                            "sse": sse_enabled,
                            "web_package": "echoweave_web",
                        },
                    )
                    return
                verification = _verification_response_from_query(adapter, parsed)
                if verification is not None:
                    self._write_raw(200, verification, "text/plain; charset=utf-8")
                    return
                if parsed.path == "/login":
                    if self._cookie_session_valid():
                        self._write_redirect("/")
                        return
                    self._write_html(200, login_html())
                    return
                if parsed.path in {"/", "/user"}:
                    if not self._authorized(webhook_token, allow_url_token=allow_url_token):
                        self._write_login_redirect(parsed.path)
                        return
                    self._write_html(200, user_html())
                    return
                if parsed.path == "/admin":
                    if not self._admin_authorized(webhook_token, allow_url_token=allow_url_token):
                        self._write_login_redirect(parsed.path)
                        return
                    self._write_html(200, admin_html())
                    return
                if parsed.path == "/api/status":
                    if not self._authorized(webhook_token, allow_url_token=allow_url_token):
                        self._write_json(401, {"ok": False, "error": "Unauthorized"})
                        return
                    status = _call_backend(backend, "admin_status") or {
                        "ok": True,
                        "service": "EchoWeave",
                        "adapter": adapter.name,
                    }
                    self._write_json(200, dict(status))
                    return
                if parsed.path == "/api/me":
                    user = self._current_user()
                    self._write_json(
                        200,
                        {
                            "ok": True,
                            "authenticated": user is not None,
                            "user": {"username": user.username, "role": user.role} if user else None,
                            "registration_open": not user_store.has_users(),
                        },
                    )
                    return
                if parsed.path == "/api/approvals":
                    if not self._admin_authorized(webhook_token, allow_url_token=allow_url_token):
                        self._write_json(401, {"ok": False, "error": "Unauthorized"})
                        return
                    approvals = _call_backend(backend, "list_approvals", 100) or []
                    self._write_json(200, {"ok": True, "approvals": approvals})
                    return
                if parsed.path == "/api/audit":
                    if not self._admin_authorized(webhook_token, allow_url_token=allow_url_token):
                        self._write_json(401, {"ok": False, "error": "Unauthorized"})
                        return
                    summary = _call_backend(backend, "audit_summary") or {"ok": True, "event_count": 0}
                    self._write_json(200, dict(summary))
                    return
                if parsed.path == "/api/config":
                    if not self._admin_authorized(webhook_token, allow_url_token=allow_url_token):
                        self._write_json(401, {"ok": False, "error": "Unauthorized"})
                        return
                    config = _call_backend(backend, "admin_config") or {}
                    self._write_json(200, {"ok": True, "config": config})
                    return
                if parsed.path == "/api/capabilities":
                    if not self._authorized(webhook_token, allow_url_token=allow_url_token):
                        self._write_json(401, {"ok": False, "error": "Unauthorized"})
                        return
                    query = parse_qs(parsed.query)
                    capabilities = _call_backend(
                        backend,
                        "web_capabilities",
                        platform=query.get("platform", ["web-user"])[0],
                        conversation_id=query.get("conversation_id", ["web-coding"])[0],
                        sender_id=query.get("sender_id", ["web-admin"])[0],
                    ) or {"ok": True}
                    self._write_json(200, dict(capabilities))
                    return
                if parsed.path == "/events":
                    if not sse_enabled:
                        self._write_json(404, {"ok": False, "error": "SSE disabled"})
                        return
                    if not self._authorized(webhook_token, allow_url_token=allow_url_token):
                        self._write_json(401, {"ok": False, "error": "Unauthorized"})
                        return
                    self._write_sse(event_bus)
                    return
                self._write_json(404, {"ok": False, "error": "Not found"})

            def do_POST(self) -> None:  # noqa: N802
                try:
                    parsed = urlparse(self.path)
                    if parsed.path == "/api/login":
                        self._handle_login(webhook_token)
                        return
                    if parsed.path == "/api/register":
                        self._handle_register()
                        return
                    if parsed.path == "/api/logout":
                        self._handle_logout()
                        return
                    if not self._authorized(webhook_token, allow_url_token=allow_url_token):
                        self._write_json(401, {"ok": False, "error": "Unauthorized"})
                        return
                    if parsed.path == "/api/config":
                        if not self._admin_authorized(webhook_token, allow_url_token=allow_url_token):
                            self._write_json(401, {"ok": False, "error": "Unauthorized"})
                            return
                        payload = self._read_json()
                        config = _call_backend(backend, "update_admin_config", payload) or {}
                        self._write_json(200, {"ok": True, "config": config})
                        return
                    if parsed.path == "/api/hardening":
                        if not self._admin_authorized(webhook_token, allow_url_token=allow_url_token):
                            self._write_json(401, {"ok": False, "error": "Unauthorized"})
                            return
                        payload = self._read_json()
                        result = _call_backend(
                            backend,
                            "generate_hardening_plan",
                            feedback_log=payload.get("feedback_log"),
                            eval_out=payload.get("eval_out"),
                        ) or {"ok": True}
                        self._write_json(200, dict(result))
                        return
                    if parsed.path == "/api/command":
                        payload = self._read_json()
                        reply = _handle_web_command(backend, event_bus, payload)
                        self._write_json(
                            200,
                            {
                                "ok": True,
                                "reply": {
                                    "text": reply.text,
                                    "platform": reply.platform,
                                    "conversation_id": reply.conversation_id,
                                    "target_id": reply.target_id,
                                    "metadata": reply.metadata,
                                },
                            },
                        )
                        return
                    action_match = re_match_approval_action(parsed.path)
                    if action_match is not None:
                        if not self._admin_authorized(webhook_token, allow_url_token=allow_url_token):
                            self._write_json(401, {"ok": False, "error": "Unauthorized"})
                            return
                        approval_id, action = action_match
                        method_name = {
                            "approve": "approve_approval",
                            "deny": "deny_approval",
                            "revoke": "revoke_approval",
                            "retry": "retry_approval",
                        }.get(action)
                        if method_name is None:
                            self._write_json(404, {"ok": False, "error": "Unknown approval action"})
                            return
                        result = _call_backend(backend, method_name, approval_id, actor_id="web-admin")
                        self._write_json(200, {"ok": True, "result": result})
                        return
                    payload = self._read_payload()
                    verification_response = _call_adapter(adapter, "verification_response", payload)
                    if isinstance(verification_response, dict):
                        self._write_json(200, verification_response)
                        return
                    logger.info("payload summary=%s", _payload_summary(payload))
                    event = adapter.event_from_payload(payload)
                    event_bus.publish(
                        "message.inbound",
                        {
                            "platform": event.platform,
                            "conversation_id": event.conversation_id,
                            "sender_id": event.sender_id,
                            "message_id": event.message_id,
                            "text": event.text,
                        },
                    )
                    logger.info(
                        "inbound platform=%s conversation=%s sender=%s message_id=%s",
                        event.platform,
                        event.conversation_id,
                        event.sender_id,
                        event.message_id,
                    )
                    reply = backend.handle(event)
                    outbound = adapter.payload_from_reply(reply)
                    event_bus.publish(
                        "message.reply",
                        {
                            "platform": reply.platform,
                            "conversation_id": reply.conversation_id,
                            "target_id": reply.target_id,
                            "text": reply.text,
                            "metadata": reply.metadata,
                        },
                    )
                    if onebot_client and adapter.name == "onebot-v11":
                        send_result = onebot_client.send_reply(reply)
                        outbound["active_send"] = send_result.to_dict()
                        if not send_result.ok:
                            logger.warning("onebot active send failed: %s", send_result.error)
                    raw_body = outbound.get("_raw_body")
                    if isinstance(raw_body, str):
                        self._write_raw(
                            200,
                            raw_body,
                            str(outbound.get("_raw_content_type") or "text/plain; charset=utf-8"),
                        )
                        return
                    self._write_json(200, outbound)
                except json.JSONDecodeError:
                    self._write_json(400, {"ok": False, "error": "Invalid JSON payload"})
                except Exception as exc:  # pragma: no cover - defensive HTTP edge
                    logger.exception("web request failed")
                    event_bus.publish(
                        "message.error",
                        {"error": str(exc), "type": type(exc).__name__},
                    )
                    self._write_json(500, {"ok": False, "error": str(exc)})

            def log_message(self, format: str, *args: Any) -> None:
                return

            def _read_json(self) -> dict[str, Any]:
                raw = self._read_body()
                data = json.loads(raw.decode("utf-8") or "{}")
                if not isinstance(data, dict):
                    raise ValueError("Webhook payload must be a JSON object.")
                return data

            def _read_payload(self) -> dict[str, Any]:
                raw = self._read_body()
                stripped = raw.lstrip()
                content_type = (self.headers.get("Content-Type") or "").lower()
                if stripped.startswith(b"<") or "xml" in content_type:
                    return _parse_xml_payload(raw)
                data = json.loads(raw.decode("utf-8") or "{}")
                if not isinstance(data, dict):
                    raise ValueError("Webhook payload must be a JSON object.")
                return data

            def _read_body(self) -> bytes:
                transfer_encoding = (self.headers.get("Transfer-Encoding") or "").lower()
                if "chunked" in transfer_encoding:
                    return _read_chunked_body(self.rfile)
                length = int(self.headers.get("Content-Length") or "0")
                return self.rfile.read(length)

            def _handle_login(self, token: str | None) -> None:
                payload = self._read_json()
                username = str(payload.get("username") or "").strip()
                password = str(payload.get("password") or "")
                if username:
                    user = user_store.verify_login(username, password)
                    if user is None:
                        self._write_json(401, {"ok": False, "error": "Unauthorized"})
                        return
                    self._write_auth_cookie(user)
                    return

                submitted = str(payload.get("token") or payload.get("password") or "")
                if token and not secrets.compare_digest(submitted, token):
                    self._write_json(401, {"ok": False, "error": "Unauthorized"})
                    return
                if not token and user_store.has_users():
                    self._write_json(401, {"ok": False, "error": "Unauthorized"})
                    return
                self._write_auth_cookie(AuthUser("token-admin", "admin"))

            def _handle_register(self) -> None:
                payload = self._read_json()
                if user_store.has_users() and not self._admin_authorized(webhook_token, allow_url_token=allow_url_token):
                    self._write_json(401, {"ok": False, "error": "Unauthorized"})
                    return
                try:
                    user = user_store.register(str(payload.get("username") or ""), str(payload.get("password") or ""))
                except ValueError as exc:
                    status = 409 if "already exists" in str(exc) else 400
                    self._write_json(status, {"ok": False, "error": str(exc)})
                    return
                self._write_auth_cookie(user)

            def _write_auth_cookie(self, user: AuthUser) -> None:
                token_value = user_store.issue_token(user, session_ttl_seconds)
                self._write_json(
                    200,
                    {
                        "ok": True,
                        "expires_in": session_ttl_seconds,
                        "user": {"username": user.username, "role": user.role},
                    },
                    extra_headers=[
                        (
                            "Set-Cookie",
                            f"{self.session_cookie_name}={token_value}; Path=/; HttpOnly; SameSite=Lax; Max-Age={session_ttl_seconds}",
                        )
                    ],
                )

            def _handle_logout(self) -> None:
                self._write_json(
                    200,
                    {"ok": True},
                    extra_headers=[
                        (
                            "Set-Cookie",
                            f"{self.session_cookie_name}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0",
                        )
                    ],
                )

            def _authorized(self, token: str | None, *, allow_url_token: bool) -> bool:
                if self._cookie_session_valid():
                    return True
                if not token:
                    return not user_store.has_users()
                authorization = self.headers.get("Authorization") or ""
                if authorization == f"Bearer {token}":
                    return True
                if self.headers.get("X-EchoWeave-Token") == token:
                    return True
                if not allow_url_token:
                    return False
                query = parse_qs(urlparse(self.path).query)
                return query.get("token", [None])[0] == token

            def _admin_authorized(self, token: str | None, *, allow_url_token: bool) -> bool:
                user = self._current_user()
                if user is not None:
                    return user.role == "admin"
                if not token:
                    return not user_store.has_users()
                authorization = self.headers.get("Authorization") or ""
                if authorization == f"Bearer {token}":
                    return True
                if self.headers.get("X-EchoWeave-Token") == token:
                    return True
                if not allow_url_token:
                    return False
                query = parse_qs(urlparse(self.path).query)
                return query.get("token", [None])[0] == token

            def _current_user(self) -> AuthUser | None:
                return user_store.verify_token(self._session_cookie_value())

            def _session_cookie_value(self) -> str | None:
                raw = self.headers.get("Cookie")
                if not raw:
                    return None
                cookie = SimpleCookie()
                cookie.load(raw)
                morsel = cookie.get(self.session_cookie_name)
                return morsel.value if morsel else None

            def _cookie_session_valid(self) -> bool:
                return self._current_user() is not None

            def _write_json(
                self,
                status: int,
                payload: dict[str, Any],
                *,
                extra_headers: list[tuple[str, str]] | None = None,
            ) -> None:
                encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.send_header("Cache-Control", "no-store")
                for name, value in extra_headers or []:
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(encoded)

            def _write_html(self, status: int, html: str) -> None:
                encoded = html.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(encoded)

            def _write_redirect(self, location: str) -> None:
                self.send_response(302)
                self.send_header("Location", location)
                self.send_header("Content-Length", "0")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()

            def _write_login_redirect(self, next_path: str) -> None:
                self._write_redirect(f"/login?next={next_path}")

            def _write_raw(self, status: int, body: str | bytes, content_type: str) -> None:
                encoded = body if isinstance(body, bytes) else body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def _write_sse(self, bus: EventBus) -> None:
                query = parse_qs(urlparse(self.path).query)
                last_id = _parse_event_id(
                    self.headers.get("Last-Event-ID")
                    or query.get("last_id", [None])[0]
                    or query.get("since", [None])[0]
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                self.wfile.write(b": connected\n\n")
                self.wfile.flush()
                while True:
                    events = bus.wait_after(last_id, timeout=15.0)
                    if events:
                        for envelope in events:
                            self.wfile.write(envelope.to_sse())
                            last_id = envelope.id
                    else:
                        heartbeat = (
                            f"event: heartbeat\ndata: {{\"ts\": {time.time():.3f}}}\n\n"
                        ).encode("utf-8")
                        self.wfile.write(heartbeat)
                    self.wfile.flush()

        return ThreadingHTTPServer((host, port), Handler)

    def serve(self, host: str = "127.0.0.1", port: int = 8787) -> None:
        server = self.build_server(host, port)
        print(f"EchoWeave web gateway listening on http://{host}:{port}")
        print(f"adapter: {self.adapter.name}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("Shutting down EchoWeave web gateway.")
        finally:
            server.server_close()


def _handle_web_command(backend: AgentBackend, event_bus: EventBus, payload: dict[str, Any]) -> Any:
    text = str(payload.get("text") or "").strip()
    if not text:
        raise ValueError("Command text is required.")
    platform = str(payload.get("platform") or "web-admin")
    conversation_id = str(payload.get("conversation_id") or "web-admin")
    sender_id = str(payload.get("sender_id") or "").strip()
    if not sender_id or sender_id == "web-admin":
        sender_id = _first_configured_admin(backend) or "web-admin"
    event = EchoWeaveEvent(
        platform=platform,
        conversation_id=conversation_id,
        sender_id=sender_id,
        text=text,
        message_id=str(payload.get("message_id") or f"web-{int(time.time() * 1000)}"),
        raw={"source": "echoweave_web", "payload": payload},
    )
    event_bus.publish(
        "message.inbound",
        {
            "platform": event.platform,
            "conversation_id": event.conversation_id,
            "sender_id": event.sender_id,
            "message_id": event.message_id,
            "text": event.text,
            "source": "web-command",
        },
    )
    reply = backend.handle(event)
    event_bus.publish(
        "message.reply",
        {
            "platform": reply.platform,
            "conversation_id": reply.conversation_id,
            "target_id": reply.target_id,
            "text": reply.text,
            "metadata": reply.metadata,
            "source": "web-command",
        },
    )
    return reply


def _first_configured_admin(backend: AgentBackend) -> str | None:
    config = _call_backend(backend, "admin_config")
    if not isinstance(config, dict):
        return None
    admins = config.get("admins")
    if not isinstance(admins, list | tuple):
        return None
    for admin in admins:
        text = str(admin).strip()
        if text:
            return text
    return None


def _default_user_store_path(backend: AgentBackend) -> Path:
    config = _call_backend(backend, "admin_config")
    if isinstance(config, dict):
        for key in ("state_path", "harness_audit_path"):
            raw_path = config.get(key)
            if raw_path:
                return Path(str(raw_path)).expanduser().resolve().parent / "echoweave-users.json"
        raw_sandbox = config.get("sandbox_root")
        if raw_sandbox:
            return Path(str(raw_sandbox)).expanduser().resolve().parent / "echoweave-users.json"
    return Path.cwd().resolve() / "echoweave-data" / "echoweave-users.json"


def _payload_summary(payload: dict[str, Any]) -> str:
    try:
        encoded = json.dumps(payload, ensure_ascii=False, default=str)
    except TypeError:
        encoded = str(payload)
    if len(encoded) > 1000:
        return encoded[:1000] + "...(truncated)"
    return encoded


def _parse_event_id(value: str | None) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def re_match_approval_action(path: str) -> tuple[str, str] | None:
    parts = [part for part in path.split("/") if part]
    if len(parts) != 4:
        return None
    if parts[0] != "api" or parts[1] != "approvals":
        return None
    return parts[2], parts[3]


def _call_backend(backend: AgentBackend, method_name: str, *args: Any, **kwargs: Any) -> Any:
    method = getattr(backend, method_name, None)
    if not callable(method):
        return None
    return method(*args, **kwargs)


def _call_adapter(adapter: PlatformAdapter, method_name: str, *args: Any, **kwargs: Any) -> Any:
    method = getattr(adapter, method_name, None)
    if not callable(method):
        return None
    return method(*args, **kwargs)


def _verification_response_from_query(adapter: PlatformAdapter, parsed: Any) -> str | None:
    query = parse_qs(parsed.query)
    echostr = query.get("echostr", [None])[0]
    if adapter.name == "wechat-official" and echostr:
        return str(echostr)
    return None


def _parse_xml_payload(raw: bytes) -> dict[str, Any]:
    root = ET.fromstring(raw.decode("utf-8"))
    return {"xml": {child.tag: child.text or "" for child in root}}


def _read_chunked_body(stream: Any) -> bytes:
    chunks: list[bytes] = []
    while True:
        line = stream.readline()
        if not line:
            break
        size_text = line.split(b";", 1)[0].strip()
        if not size_text:
            continue
        size = int(size_text, 16)
        if size == 0:
            while True:
                trailer = stream.readline()
                if trailer in {b"\r\n", b"\n", b""}:
                    break
            break
        chunks.append(stream.read(size))
        stream.read(2)
    return b"".join(chunks)
