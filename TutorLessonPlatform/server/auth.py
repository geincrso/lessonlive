"""Password hashing and session helpers."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from typing import Any

try:
    from server import db
except ImportError:
    import db  # type: ignore

LOGIN_RE = re.compile(r"^[a-zA-Z0-9_]{3,32}$")


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        120_000,
    )
    return digest.hex(), salt


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    check, _ = hash_password(password, salt)
    return hmac.compare_digest(check, password_hash)


def register_user(login: str, password: str, name: str) -> tuple[dict[str, Any] | None, str | None]:
    login = (login or "").strip()
    name = (name or "").strip()[:40] or login
    password = password or ""

    if not LOGIN_RE.match(login):
        return None, "Логин: 3–32 символа (латиница, цифры, _)"
    if len(password) < 6:
        return None, "Пароль не короче 6 символов"
    if db.get_user_by_login(login):
        return None, "Такой логин уже занят"

    password_hash, salt = hash_password(password)
    user = db.create_user(login, name, password_hash, salt)
    token = db.create_session(user["id"])
    return {"user": user, "token": token}, None


def login_user(login: str, password: str) -> tuple[dict[str, Any] | None, str | None]:
    row = db.get_user_by_login((login or "").strip())
    if not row or not verify_password(password or "", row["password_hash"], row["salt"]):
        return None, "Неверный логин или пароль"
    user = db.user_public(row)
    token = db.create_session(row["id"])
    return {"user": user, "token": token}, None


def extract_token(headers: dict[str, str], body_token: str | None = None) -> str:
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    cookie = headers.get("cookie", "")
    for part in cookie.split(";"):
        part = part.strip()
        if part.startswith("token="):
            return part[6:].strip()
    return (body_token or "").strip()
