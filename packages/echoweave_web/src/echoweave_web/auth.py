from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AuthUser:
    username: str
    role: str


class JwtUserStore:
    def __init__(self, path: str | Path, *, fallback_secret: str | None = None) -> None:
        self.path = Path(path).expanduser().resolve()
        self.fallback_secret = fallback_secret or ""

    def has_users(self) -> bool:
        return bool(self._load().get("users"))

    def register(self, username: str, password: str, *, role: str | None = None) -> AuthUser:
        clean_username = _clean_username(username)
        if len(password) < 8:
            raise ValueError("Password must contain at least 8 characters.")
        data = self._load()
        users = data.setdefault("users", {})
        if clean_username in users:
            raise ValueError("User already exists.")
        assigned_role = role or ("admin" if not users else "user")
        users[clean_username] = {
            "password": _hash_password(password),
            "role": assigned_role,
            "created_at": int(time.time()),
        }
        self._save(data)
        return AuthUser(clean_username, assigned_role)

    def verify_login(self, username: str, password: str) -> AuthUser | None:
        clean_username = _clean_username(username, allow_empty=True)
        if not clean_username:
            return None
        data = self._load()
        raw_user = data.get("users", {}).get(clean_username)
        if not isinstance(raw_user, dict):
            return None
        stored_password = str(raw_user.get("password") or "")
        if not _verify_password(password, stored_password):
            return None
        return AuthUser(clean_username, str(raw_user.get("role") or "user"))

    def issue_token(self, user: AuthUser, ttl_seconds: int) -> str:
        now = int(time.time())
        payload = {
            "sub": user.username,
            "role": user.role,
            "iat": now,
            "exp": now + max(60, int(ttl_seconds or 0)),
        }
        return _encode_jwt(payload, self._secret())

    def verify_token(self, token: str | None) -> AuthUser | None:
        if not token:
            return None
        payload = _decode_jwt(token, self._secret())
        if payload is None:
            return None
        username = str(payload.get("sub") or "").strip()
        role = str(payload.get("role") or "user").strip() or "user"
        if not username:
            return None
        if username == "token-admin":
            return AuthUser(username, role)
        users = self._load().get("users", {})
        if username not in users:
            return None
        return AuthUser(username, role)

    def _secret(self) -> str:
        data = self._load()
        secret = str(data.get("jwt_secret") or "")
        if secret:
            return secret
        secret = secrets.token_urlsafe(48)
        data["jwt_secret"] = secret
        self._save(data)
        return secret

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "jwt_secret": "", "users": {}}
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return {"version": 1, "jwt_secret": "", "users": {}}
        if not isinstance(data, dict):
            return {"version": 1, "jwt_secret": "", "users": {}}
        if not isinstance(data.get("users"), dict):
            data["users"] = {}
        return data

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)


def _clean_username(username: str, *, allow_empty: bool = False) -> str:
    clean = str(username or "").strip()
    if not clean and allow_empty:
        return ""
    if not clean:
        raise ValueError("Username is required.")
    if len(clean) > 64:
        raise ValueError("Username is too long.")
    if any(ch.isspace() for ch in clean):
        raise ValueError("Username must not contain whitespace.")
    return clean


def _hash_password(password: str) -> str:
    iterations = 150_000
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${_b64e(salt)}${_b64e(digest)}"


def _verify_password(password: str, stored: str) -> bool:
    parts = stored.split("$")
    if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
        return False
    try:
        iterations = int(parts[1])
        salt = _b64d(parts[2])
        expected = _b64d(parts[3])
    except (TypeError, ValueError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def _encode_jwt(payload: dict[str, Any], secret: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = f"{_b64json(header)}.{_b64json(payload)}"
    signature = hmac.new(secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
    return f"{signing_input}.{_b64e(signature)}"


def _decode_jwt(token: str, secret: str) -> dict[str, Any] | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    signing_input = f"{parts[0]}.{parts[1]}"
    expected = hmac.new(secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
    try:
        actual = _b64d(parts[2])
        payload = json.loads(_b64d(parts[1]).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return None
    if not hmac.compare_digest(actual, expected):
        return None
    if not isinstance(payload, dict):
        return None
    exp = payload.get("exp")
    if not isinstance(exp, int | float) or exp <= time.time():
        return None
    return payload


def _b64json(value: dict[str, Any]) -> str:
    return _b64e(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * ((4 - len(text) % 4) % 4))
