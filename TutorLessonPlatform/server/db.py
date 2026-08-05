"""SQLite persistence for УрокLive."""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent / "data"
DB_PATH = DATA_DIR / "uroklive.db"

_local = threading.local()


def _conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        _local.conn = conn
    return conn


def init_db() -> None:
    conn = _conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            login TEXT NOT NULL UNIQUE COLLATE NOCASE,
            display_name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            created_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expires_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS friendships (
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            friend_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            status TEXT NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY (user_id, friend_id)
        );

        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            from_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            to_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            text TEXT NOT NULL,
            created_at REAL NOT NULL,
            read_at REAL
        );

        CREATE TABLE IF NOT EXISTS calls (
            id TEXT PRIMARY KEY,
            from_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            to_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            room_code TEXT NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_messages_pair ON messages(from_id, to_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
        """
    )
    conn.commit()


def user_public(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "id": row["id"],
        "login": row["login"],
        "name": row["display_name"],
    }


def create_user(login: str, display_name: str, password_hash: str, salt: str) -> dict[str, Any]:
    user_id = uuid.uuid4().hex
    now = time.time()
    conn = _conn()
    conn.execute(
        "INSERT INTO users (id, login, display_name, password_hash, salt, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, login, display_name, password_hash, salt, now),
    )
    conn.commit()
    return {"id": user_id, "login": login, "name": display_name}


def get_user_by_login(login: str) -> sqlite3.Row | None:
    return _conn().execute(
        "SELECT * FROM users WHERE login = ? COLLATE NOCASE",
        (login,),
    ).fetchone()


def get_user_by_id(user_id: str) -> sqlite3.Row | None:
    return _conn().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def create_session(user_id: str, days: int = 30) -> str:
    token = uuid.uuid4().hex + uuid.uuid4().hex
    expires = time.time() + days * 86400
    conn = _conn()
    conn.execute(
        "INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
        (token, user_id, expires),
    )
    conn.commit()
    return token


def delete_session(token: str) -> None:
    conn = _conn()
    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    conn.commit()


def user_from_token(token: str) -> dict[str, Any] | None:
    if not token:
        return None
    row = _conn().execute(
        """
        SELECT u.* FROM sessions s
        JOIN users u ON u.id = s.user_id
        WHERE s.token = ? AND s.expires_at > ?
        """,
        (token, time.time()),
    ).fetchone()
    return user_public(row)


def search_users(query: str, exclude_id: str, limit: int = 20) -> list[dict[str, Any]]:
    q = f"%{query.strip()}%"
    rows = _conn().execute(
        """
        SELECT id, login, display_name FROM users
        WHERE id != ? AND (login LIKE ? COLLATE NOCASE OR display_name LIKE ? COLLATE NOCASE)
        ORDER BY login LIMIT ?
        """,
        (exclude_id, q, q, limit),
    ).fetchall()
    return [{"id": r["id"], "login": r["login"], "name": r["display_name"]} for r in rows]


def friendship_status(a: str, b: str) -> str | None:
    row = _conn().execute(
        """
        SELECT status FROM friendships
        WHERE (user_id = ? AND friend_id = ?) OR (user_id = ? AND friend_id = ?)
        """,
        (a, b, b, a),
    ).fetchone()
    return row["status"] if row else None


def request_friend(from_id: str, to_id: str) -> tuple[bool, str]:
    if from_id == to_id:
        return False, "Нельзя добавить себя"
    if not get_user_by_id(to_id):
        return False, "Пользователь не найден"
    existing = friendship_status(from_id, to_id)
    if existing == "accepted":
        return False, "Уже в друзьях"
    if existing == "pending":
        return False, "Заявка уже отправлена"
    conn = _conn()
    conn.execute(
        "INSERT INTO friendships (user_id, friend_id, status, created_at) VALUES (?, ?, 'pending', ?)",
        (from_id, to_id, time.time()),
    )
    conn.commit()
    return True, "ok"


def respond_friend(user_id: str, from_id: str, accept: bool) -> tuple[bool, str]:
    conn = _conn()
    row = conn.execute(
        "SELECT * FROM friendships WHERE user_id = ? AND friend_id = ? AND status = 'pending'",
        (from_id, user_id),
    ).fetchone()
    if not row:
        return False, "Заявка не найдена"
    if accept:
        conn.execute(
            "UPDATE friendships SET status = 'accepted' WHERE user_id = ? AND friend_id = ?",
            (from_id, user_id),
        )
        # Ensure symmetric accepted edge for easier queries.
        conn.execute(
            """
            INSERT OR REPLACE INTO friendships (user_id, friend_id, status, created_at)
            VALUES (?, ?, 'accepted', ?)
            """,
            (user_id, from_id, time.time()),
        )
    else:
        conn.execute(
            "DELETE FROM friendships WHERE user_id = ? AND friend_id = ?",
            (from_id, user_id),
        )
    conn.commit()
    return True, "ok"


def list_friends(user_id: str) -> dict[str, list[dict[str, Any]]]:
    conn = _conn()
    accepted = conn.execute(
        """
        SELECT u.id, u.login, u.display_name FROM friendships f
        JOIN users u ON u.id = f.friend_id
        WHERE f.user_id = ? AND f.status = 'accepted'
        ORDER BY u.display_name
        """,
        (user_id,),
    ).fetchall()
    incoming = conn.execute(
        """
        SELECT u.id, u.login, u.display_name FROM friendships f
        JOIN users u ON u.id = f.user_id
        WHERE f.friend_id = ? AND f.status = 'pending'
        ORDER BY f.created_at DESC
        """,
        (user_id,),
    ).fetchall()
    outgoing = conn.execute(
        """
        SELECT u.id, u.login, u.display_name FROM friendships f
        JOIN users u ON u.id = f.friend_id
        WHERE f.user_id = ? AND f.status = 'pending'
        ORDER BY f.created_at DESC
        """,
        (user_id,),
    ).fetchall()

    def map_rows(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
        return [{"id": r["id"], "login": r["login"], "name": r["display_name"]} for r in rows]

    return {
        "friends": map_rows(accepted),
        "incoming": map_rows(incoming),
        "outgoing": map_rows(outgoing),
    }


def are_friends(a: str, b: str) -> bool:
    return friendship_status(a, b) == "accepted"


def save_message(from_id: str, to_id: str, text: str) -> dict[str, Any]:
    msg_id = uuid.uuid4().hex
    now = time.time()
    conn = _conn()
    conn.execute(
        "INSERT INTO messages (id, from_id, to_id, text, created_at, read_at) VALUES (?, ?, ?, ?, ?, NULL)",
        (msg_id, from_id, to_id, text, now),
    )
    conn.commit()
    return {
        "id": msg_id,
        "fromId": from_id,
        "toId": to_id,
        "text": text,
        "createdAt": int(now * 1000),
    }


def list_messages(user_id: str, friend_id: str, limit: int = 100) -> list[dict[str, Any]]:
    rows = _conn().execute(
        """
        SELECT * FROM messages
        WHERE (from_id = ? AND to_id = ?) OR (from_id = ? AND to_id = ?)
        ORDER BY created_at DESC LIMIT ?
        """,
        (user_id, friend_id, friend_id, user_id, limit),
    ).fetchall()
    messages = [
        {
            "id": r["id"],
            "fromId": r["from_id"],
            "toId": r["to_id"],
            "text": r["text"],
            "createdAt": int(r["created_at"] * 1000),
        }
        for r in reversed(rows)
    ]
    return messages


def create_call(
    from_id: str,
    to_id: str,
    kind: str,
    room_code: str,
    title: str,
) -> dict[str, Any]:
    call_id = uuid.uuid4().hex
    now = time.time()
    conn = _conn()
    conn.execute(
        """
        INSERT INTO calls (id, from_id, to_id, kind, room_code, title, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 'ringing', ?)
        """,
        (call_id, from_id, to_id, kind, room_code, title, now),
    )
    conn.commit()
    return {
        "id": call_id,
        "fromId": from_id,
        "toId": to_id,
        "kind": kind,
        "roomCode": room_code,
        "title": title,
        "status": "ringing",
        "createdAt": int(now * 1000),
    }


def update_call_status(call_id: str, status: str) -> dict[str, Any] | None:
    conn = _conn()
    conn.execute("UPDATE calls SET status = ? WHERE id = ?", (status, call_id))
    conn.commit()
    row = conn.execute("SELECT * FROM calls WHERE id = ?", (call_id,)).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "fromId": row["from_id"],
        "toId": row["to_id"],
        "kind": row["kind"],
        "roomCode": row["room_code"],
        "title": row["title"],
        "status": row["status"],
        "createdAt": int(row["created_at"] * 1000),
    }


def get_call(call_id: str) -> dict[str, Any] | None:
    row = _conn().execute("SELECT * FROM calls WHERE id = ?", (call_id,)).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "fromId": row["from_id"],
        "toId": row["to_id"],
        "kind": row["kind"],
        "roomCode": row["room_code"],
        "title": row["title"],
        "status": row["status"],
        "createdAt": int(row["created_at"] * 1000),
    }
