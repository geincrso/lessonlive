#!/usr/bin/env python3
"""УрокLive — HTTPS + WebSocket сервер с аккаунтами, друзьями и звонками."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import mimetypes
import os
import random
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

# Allow `py -3 server/app.py` and `py -3 -m server.app`
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    from server import auth, db, tlsutil
except ImportError:
    import auth  # type: ignore
    import db  # type: ignore
    import tlsutil  # type: ignore

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "3443"))
HTTP_REDIRECT_PORT = int(os.environ.get("HTTP_PORT", "3000"))
ROOT = ROOT_DIR / "public"
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
READ_TIMEOUT = 60.0
MAX_HEADER_BYTES = 64 * 1024
MAX_BODY_BYTES = 1 * 1024 * 1024

mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("text/html", ".html")


@dataclass
class Participant:
    id: str
    name: str
    role: str
    writer: asyncio.StreamWriter
    user_id: str | None = None


@dataclass
class Room:
    id: str
    code: str
    title: str
    mode: str = "lesson"
    host_id: str | None = None
    participants: dict[str, Participant] = field(default_factory=dict)
    strokes: list[dict[str, Any]] = field(default_factory=list)


rooms: dict[str, Room] = {}
code_index: dict[str, str] = {}
static_cache: dict[str, tuple[bytes, str, float]] = {}
# user_id -> set of websocket writers (cabinet connections)
online_users: dict[str, set[asyncio.StreamWriter]] = {}


def log(message: str) -> None:
    print(message, flush=True)


def generate_code() -> str:
    while True:
        code = "".join(random.choice(ALPHABET) for _ in range(6))
        if code not in code_index:
            return code


def public_participants(room: Room) -> list[dict[str, str]]:
    return [
        {"id": p.id, "name": p.name, "role": p.role}
        for p in room.participants.values()
    ]


def http_message(
    status: int,
    body: bytes,
    *,
    content_type: str,
    keep_alive: bool,
    extra_headers: list[str] | None = None,
) -> bytes:
    reason = {
        200: "OK",
        201: "Created",
        204: "No Content",
        400: "Bad Request",
        401: "Unauthorized",
        403: "Forbidden",
        404: "Not Found",
        405: "Method Not Allowed",
        301: "Moved Permanently",
        302: "Found",
    }.get(status, "OK")
    connection = "keep-alive" if keep_alive else "close"
    headers = [
        f"HTTP/1.1 {status} {reason}",
        f"Content-Type: {content_type}",
        f"Content-Length: {len(body)}",
        f"Connection: {connection}",
        "Access-Control-Allow-Origin: *",
        "Access-Control-Allow-Headers: Content-Type, Authorization",
        "Access-Control-Allow-Methods: GET, POST, OPTIONS",
    ]
    if keep_alive:
        headers.append("Keep-Alive: timeout=15, max=100")
    if extra_headers:
        headers.extend(extra_headers)
    return ("\r\n".join(headers) + "\r\n\r\n").encode("utf-8") + body


def json_message(
    status: int,
    payload: dict[str, Any],
    keep_alive: bool,
    extra_headers: list[str] | None = None,
) -> bytes:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return http_message(
        status,
        body,
        content_type="application/json; charset=utf-8",
        keep_alive=keep_alive,
        extra_headers=extra_headers,
    )


def load_static(rel_path: str) -> tuple[bytes, str] | None:
    candidate = (ROOT / rel_path).resolve()
    root_resolved = ROOT.resolve()
    if not str(candidate).startswith(str(root_resolved)) or not candidate.is_file():
        return None
    mtime = candidate.stat().st_mtime
    cached = static_cache.get(rel_path)
    if cached and cached[2] == mtime:
        return cached[0], cached[1]
    data = candidate.read_bytes()
    mime, _ = mimetypes.guess_type(str(candidate))
    content_type = mime or "application/octet-stream"
    if content_type.startswith("text/") or content_type in {
        "application/javascript",
        "application/json",
    }:
        content_type += "; charset=utf-8"
    static_cache[rel_path] = (data, content_type, mtime)
    return data, content_type


async def read_http_request(
    reader: asyncio.StreamReader,
) -> tuple[str, str, dict[str, str], bytes]:
    header_bytes = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=READ_TIMEOUT)
    if len(header_bytes) > MAX_HEADER_BYTES:
        raise ValueError("headers too large")
    header_text = header_bytes.decode("iso-8859-1")
    lines = header_text.split("\r\n")
    parts = lines[0].split(" ")
    if len(parts) < 2:
        raise ValueError("bad request line")
    method, target = parts[0].upper(), parts[1]
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    length = int(headers.get("content-length", "0") or "0")
    if length < 0 or length > MAX_BODY_BYTES:
        raise ValueError("bad content length")
    body = await asyncio.wait_for(reader.readexactly(length), timeout=READ_TIMEOUT) if length else b""
    return method, target, headers, body


def ws_accept_key(key: str) -> str:
    guid = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
    digest = hashlib.sha1((key + guid).encode("utf-8")).digest()
    return base64.b64encode(digest).decode("ascii")


async def send_ws(writer: asyncio.StreamWriter, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    header = bytearray([0x81])
    length = len(data)
    if length < 126:
        header.append(length)
    elif length < (1 << 16):
        header.append(126)
        header.extend(length.to_bytes(2, "big"))
    else:
        header.append(127)
        header.extend(length.to_bytes(8, "big"))
    writer.write(header + data)
    await writer.drain()


async def send_ws_pong(writer: asyncio.StreamWriter, payload: bytes = b"") -> None:
    header = bytearray([0x8A])
    length = len(payload)
    if length < 126:
        header.append(length)
    else:
        header.append(126)
        header.extend(length.to_bytes(2, "big"))
    writer.write(header + payload)
    await writer.drain()


async def recv_ws(reader: asyncio.StreamReader) -> dict[str, Any] | None:
    header = await reader.readexactly(2)
    opcode = header[0] & 0x0F
    masked = (header[1] & 0x80) != 0
    length = header[1] & 0x7F
    if length == 126:
        length = int.from_bytes(await reader.readexactly(2), "big")
    elif length == 127:
        length = int.from_bytes(await reader.readexactly(8), "big")
    mask = await reader.readexactly(4) if masked else b""
    data = bytearray(await asyncio.wait_for(reader.readexactly(length), timeout=READ_TIMEOUT))
    if masked:
        for i in range(length):
            data[i] ^= mask[i % 4]
    if opcode == 0x8:
        return None
    if opcode == 0x9:
        return {"type": "__ping__", "payload": bytes(data)}
    if opcode == 0xA:
        return {"type": "__pong__"}
    if opcode != 0x1:
        return {"type": "__ignore__"}
    return json.loads(data.decode("utf-8"))


async def notify_user(user_id: str, payload: dict[str, Any]) -> None:
    writers = list(online_users.get(user_id, set()))
    dead: list[asyncio.StreamWriter] = []
    for writer in writers:
        try:
            await send_ws(writer, payload)
        except Exception:
            dead.append(writer)
    for writer in dead:
        online_users.get(user_id, set()).discard(writer)


def create_room(title: str, mode: str = "lesson") -> Room:
    room_id = str(uuid.uuid4())
    code = generate_code()
    room = Room(id=room_id, code=code, title=title, mode=mode)
    rooms[room_id] = room
    code_index[code] = room_id
    return room


async def broadcast(room: Room, payload: dict[str, Any], skip: str | None = None) -> None:
    dead: list[str] = []
    for pid, participant in list(room.participants.items()):
        if skip and pid == skip:
            continue
        try:
            await send_ws(participant.writer, payload)
        except Exception:
            dead.append(pid)
    for pid in dead:
        await remove_participant(room, pid)


async def remove_participant(room: Room, participant_id: str) -> None:
    left = room.participants.pop(participant_id, None)
    if not left:
        return
    if room.host_id == participant_id:
        room.host_id = None
    if room.participants:
        await broadcast(
            room,
            {
                "type": "participant-left",
                "id": participant_id,
                "name": left.name,
                "participants": public_participants(room),
            },
        )
    else:
        rooms.pop(room.id, None)
        code_index.pop(room.code, None)


def require_user(headers: dict[str, str], body: dict[str, Any] | None = None) -> dict[str, Any] | None:
    token = auth.extract_token(headers, (body or {}).get("token"))
    return db.user_from_token(token)


def parse_json(body: bytes) -> dict[str, Any]:
    if not body:
        return {}
    data = json.loads(body.decode("utf-8"))
    return data if isinstance(data, dict) else {}


async def handle_websocket(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    headers: dict[str, str],
) -> None:
    key = headers.get("sec-websocket-key")
    if not key:
        writer.write(json_message(400, {"error": "Bad websocket key"}, False))
        await writer.drain()
        return

    accept = ws_accept_key(key)
    writer.write(
        (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
        ).encode("utf-8")
    )
    await writer.drain()

    participant_id = uuid.uuid4().hex
    room: Room | None = None
    user: dict[str, Any] | None = None

    try:
        while True:
            message = await recv_ws(reader)
            if message is None:
                break
            if message.get("type") == "__ping__":
                await send_ws_pong(writer, message.get("payload") or b"")
                continue
            if message.get("type") in {"__pong__", "__ignore__"}:
                continue

            msg_type = message.get("type")
            if msg_type == "ping":
                await send_ws(writer, {"type": "pong", "t": message.get("t")})
                continue

            if msg_type == "auth":
                token = str(message.get("token") or "")
                user = db.user_from_token(token)
                if not user:
                    await send_ws(writer, {"type": "auth-result", "ok": False, "error": "Нужен вход"})
                    continue
                online_users.setdefault(user["id"], set()).add(writer)
                await send_ws(writer, {"type": "auth-result", "ok": True, "user": user})
                continue

            if msg_type == "dm-send" and user:
                to_id = str(message.get("toId") or "")
                text = str(message.get("text") or "").strip()[:2000]
                if not text or not db.are_friends(user["id"], to_id):
                    await send_ws(writer, {"type": "error", "error": "Нельзя отправить сообщение"})
                    continue
                msg = db.save_message(user["id"], to_id, text)
                payload = {"type": "dm-message", "message": msg}
                await send_ws(writer, payload)
                await notify_user(to_id, payload)
                continue

            if msg_type == "call-invite" and user:
                to_id = str(message.get("toId") or "")
                kind = "audio" if message.get("kind") == "audio" else "lesson"
                title = str(message.get("title") or ("Аудиозвонок" if kind == "audio" else "Урок")).strip()[:80]
                if not db.are_friends(user["id"], to_id):
                    await send_ws(writer, {"type": "error", "error": "Только для друзей"})
                    continue
                room_obj = create_room(title, mode=kind)
                call = db.create_call(user["id"], to_id, kind, room_obj.code, title)
                payload = {
                    "type": "call-invite",
                    "call": call,
                    "from": user,
                }
                await send_ws(writer, {"type": "call-created", "call": call})
                await notify_user(to_id, payload)
                continue

            if msg_type == "call-respond" and user:
                call_id = str(message.get("callId") or "")
                accept = bool(message.get("accept"))
                call = db.get_call(call_id)
                if not call or call["toId"] != user["id"]:
                    await send_ws(writer, {"type": "error", "error": "Звонок не найден"})
                    continue
                status = "accepted" if accept else "declined"
                updated = db.update_call_status(call_id, status)
                payload = {"type": "call-respond", "call": updated, "accept": accept, "by": user}
                await notify_user(call["fromId"], payload)
                await send_ws(writer, payload)
                continue

            if msg_type == "join-room":
                code = str(message.get("code", "")).strip().upper()
                name = str(message.get("name", "Гость")).strip()[:40] or "Гость"
                role = "tutor" if message.get("role") == "tutor" else "student"
                room_id = code_index.get(code)
                room = rooms.get(room_id) if room_id else None
                if not room:
                    await send_ws(
                        writer,
                        {"type": "join-result", "ok": False, "error": "Комната не найдена"},
                    )
                    continue
                if role == "tutor" and room.host_id and room.host_id in room.participants:
                    await send_ws(
                        writer,
                        {"type": "join-result", "ok": False, "error": "В комнате уже есть репетитор"},
                    )
                    continue
                participant = Participant(
                    id=participant_id,
                    name=name,
                    role=role,
                    writer=writer,
                    user_id=user["id"] if user else None,
                )
                room.participants[participant_id] = participant
                if role == "tutor":
                    room.host_id = participant_id
                await send_ws(
                    writer,
                    {
                        "type": "join-result",
                        "ok": True,
                        "roomId": room.id,
                        "code": room.code,
                        "title": room.title,
                        "mode": room.mode,
                        "self": {
                            "id": participant.id,
                            "name": participant.name,
                            "role": participant.role,
                        },
                        "participants": public_participants(room),
                        "strokes": room.strokes,
                    },
                )
                await broadcast(
                    room,
                    {
                        "type": "participant-joined",
                        "participant": {
                            "id": participant.id,
                            "name": participant.name,
                            "role": participant.role,
                        },
                        "participants": public_participants(room),
                    },
                    skip=participant_id,
                )
                continue

            if room and participant_id in room.participants:
                if msg_type == "webrtc-offer":
                    target = room.participants.get(str(message.get("to", "")))
                    if target:
                        await send_ws(
                            target.writer,
                            {"type": "webrtc-offer", "from": participant_id, "sdp": message.get("sdp")},
                        )
                elif msg_type == "webrtc-answer":
                    target = room.participants.get(str(message.get("to", "")))
                    if target:
                        await send_ws(
                            target.writer,
                            {"type": "webrtc-answer", "from": participant_id, "sdp": message.get("sdp")},
                        )
                elif msg_type == "webrtc-ice":
                    target = room.participants.get(str(message.get("to", "")))
                    if target:
                        await send_ws(
                            target.writer,
                            {
                                "type": "webrtc-ice",
                                "from": participant_id,
                                "candidate": message.get("candidate"),
                            },
                        )
                elif msg_type == "whiteboard-draw":
                    points = message.get("points")
                    if not isinstance(points, list):
                        continue
                    phase = message.get("phase")
                    if phase not in {"start", "move", "end"}:
                        continue
                    max_points = 5000 if phase == "end" else 32
                    payload = {
                        "type": "whiteboard-draw",
                        "phase": phase,
                        "strokeId": str(message.get("strokeId") or uuid.uuid4())[:64],
                        "tool": "eraser" if message.get("tool") == "eraser" else "pen",
                        "color": str(message.get("color") or "#1a1a1a")[:20],
                        "width": max(1.0, min(48.0, float(message.get("width") or 3))),
                        "points": points[:max_points],
                        "from": participant_id,
                    }
                    if phase == "end" and len(payload["points"]) >= 2:
                        room.strokes.append(
                            {
                                "id": payload["strokeId"],
                                "tool": payload["tool"],
                                "color": payload["color"],
                                "width": payload["width"],
                                "points": payload["points"],
                                "from": participant_id,
                            }
                        )
                        if len(room.strokes) > 2000:
                            del room.strokes[:-2000]
                    await broadcast(room, payload, skip=participant_id)
                elif msg_type == "whiteboard-clear":
                    room.strokes = []
                    await broadcast(
                        room,
                        {"type": "whiteboard-clear", "from": participant_id},
                        skip=participant_id,
                    )
                elif msg_type == "chat-message":
                    text = str(message.get("text") or "").strip()[:500]
                    me = room.participants.get(participant_id)
                    if text and me:
                        await broadcast(
                            room,
                            {
                                "type": "chat-message",
                                "id": uuid.uuid4().hex,
                                "from": me.name,
                                "role": me.role,
                                "text": text,
                                "at": int(time.time() * 1000),
                            },
                        )
    except (
        asyncio.TimeoutError,
        asyncio.IncompleteReadError,
        ConnectionResetError,
        BrokenPipeError,
        json.JSONDecodeError,
        ValueError,
    ):
        pass
    finally:
        if user:
            bucket = online_users.get(user["id"])
            if bucket is not None:
                bucket.discard(writer)
                if not bucket:
                    online_users.pop(user["id"], None)
        if room:
            await remove_participant(room, participant_id)


def build_http_response(
    method: str,
    target: str,
    headers: dict[str, str],
    body: bytes,
    keep_alive: bool,
) -> bytes:
    parsed = urlparse(target)
    path = parsed.path or "/"
    query = parse_qs(parsed.query)

    if method == "OPTIONS":
        return http_message(204, b"", content_type="text/plain", keep_alive=keep_alive)

    if path == "/api/health" and method == "GET":
        return json_message(200, {"ok": True, "service": "uroklive", "tls": True}, keep_alive)

    # ---- Auth ----
    if path == "/api/auth/register" and method == "POST":
        try:
            data = parse_json(body)
        except json.JSONDecodeError:
            return json_message(400, {"error": "Некорректный JSON"}, keep_alive)
        result, error = auth.register_user(data.get("login", ""), data.get("password", ""), data.get("name", ""))
        if error:
            return json_message(400, {"error": error}, keep_alive)
        return json_message(201, result, keep_alive)

    if path == "/api/auth/login" and method == "POST":
        try:
            data = parse_json(body)
        except json.JSONDecodeError:
            return json_message(400, {"error": "Некорректный JSON"}, keep_alive)
        result, error = auth.login_user(data.get("login", ""), data.get("password", ""))
        if error:
            return json_message(401, {"error": error}, keep_alive)
        return json_message(200, result, keep_alive)

    if path == "/api/auth/logout" and method == "POST":
        token = auth.extract_token(headers)
        if token:
            db.delete_session(token)
        return json_message(200, {"ok": True}, keep_alive)

    if path == "/api/me" and method == "GET":
        user = require_user(headers)
        if not user:
            return json_message(401, {"error": "Нужен вход"}, keep_alive)
        return json_message(200, {"user": user}, keep_alive)

    # ---- Users / friends / messages / calls ----
    if path == "/api/users/search" and method == "GET":
        user = require_user(headers)
        if not user:
            return json_message(401, {"error": "Нужен вход"}, keep_alive)
        q = (query.get("q") or [""])[0]
        if len(q.strip()) < 1:
            return json_message(200, {"users": []}, keep_alive)
        return json_message(200, {"users": db.search_users(q, user["id"])}, keep_alive)

    if path == "/api/friends" and method == "GET":
        user = require_user(headers)
        if not user:
            return json_message(401, {"error": "Нужен вход"}, keep_alive)
        return json_message(200, db.list_friends(user["id"]), keep_alive)

    if path == "/api/friends/request" and method == "POST":
        user = require_user(headers)
        if not user:
            return json_message(401, {"error": "Нужен вход"}, keep_alive)
        try:
            data = parse_json(body)
        except json.JSONDecodeError:
            return json_message(400, {"error": "Некорректный JSON"}, keep_alive)
        ok, err = db.request_friend(user["id"], str(data.get("userId") or ""))
        if not ok:
            return json_message(400, {"error": err}, keep_alive)
        try:
            asyncio.get_running_loop().create_task(
                notify_user(
                    str(data.get("userId")),
                    {"type": "friend-event", "action": "request", "from": user},
                )
            )
        except RuntimeError:
            pass
        return json_message(200, {"ok": True}, keep_alive)

    if path == "/api/friends/respond" and method == "POST":
        user = require_user(headers)
        if not user:
            return json_message(401, {"error": "Нужен вход"}, keep_alive)
        try:
            data = parse_json(body)
        except json.JSONDecodeError:
            return json_message(400, {"error": "Некорректный JSON"}, keep_alive)
        from_id = str(data.get("userId") or "")
        accept = bool(data.get("accept"))
        ok, err = db.respond_friend(user["id"], from_id, accept)
        if not ok:
            return json_message(400, {"error": err}, keep_alive)
        try:
            asyncio.get_running_loop().create_task(
                notify_user(
                    from_id,
                    {
                        "type": "friend-event",
                        "action": "accepted" if accept else "declined",
                        "from": user,
                    },
                )
            )
        except RuntimeError:
            pass
        return json_message(200, {"ok": True}, keep_alive)

    match_msg = re.fullmatch(r"/api/messages/([a-f0-9]+)", path)
    if match_msg and method == "GET":
        user = require_user(headers)
        if not user:
            return json_message(401, {"error": "Нужен вход"}, keep_alive)
        friend_id = match_msg.group(1)
        if not db.are_friends(user["id"], friend_id):
            return json_message(403, {"error": "Только для друзей"}, keep_alive)
        return json_message(200, {"messages": db.list_messages(user["id"], friend_id)}, keep_alive)

    if path == "/api/calls/invite" and method == "POST":
        user = require_user(headers)
        if not user:
            return json_message(401, {"error": "Нужен вход"}, keep_alive)
        try:
            data = parse_json(body)
        except json.JSONDecodeError:
            return json_message(400, {"error": "Некорректный JSON"}, keep_alive)
        friend_id = str(data.get("friendId") or "")
        kind = "audio" if data.get("kind") == "audio" else "lesson"
        title = str(data.get("title") or ("Аудиозвонок" if kind == "audio" else "Урок")).strip()[:80]
        if not db.are_friends(user["id"], friend_id):
            return json_message(403, {"error": "Только для друзей"}, keep_alive)
        room = create_room(title, mode=kind)
        call = db.create_call(user["id"], friend_id, kind, room.code, title)
        try:
            asyncio.get_running_loop().create_task(
                notify_user(friend_id, {"type": "call-invite", "call": call, "from": user})
            )
        except RuntimeError:
            pass
        return json_message(201, {"call": call}, keep_alive)

    # ---- Guest rooms ----
    if path == "/api/rooms" and method == "POST":
        try:
            data = parse_json(body)
        except json.JSONDecodeError:
            return json_message(400, {"error": "Некорректный JSON"}, keep_alive)
        title = str(data.get("title") or "Урок").strip()[:80] or "Урок"
        mode = "audio" if data.get("mode") == "audio" else "lesson"
        room = create_room(title, mode=mode)
        return json_message(
            201,
            {"roomId": room.id, "code": room.code, "title": room.title, "mode": room.mode},
            keep_alive,
        )

    match = re.fullmatch(r"/api/rooms/([A-Za-z0-9]+)", path)
    if match and method == "GET":
        code = match.group(1).upper()
        room_id = code_index.get(code)
        room = rooms.get(room_id) if room_id else None
        if not room:
            return json_message(404, {"error": "Комната не найдена"}, keep_alive)
        return json_message(
            200,
            {
                "roomId": room.id,
                "code": room.code,
                "title": room.title,
                "mode": room.mode,
                "participants": public_participants(room),
            },
            keep_alive,
        )

    if path.startswith("/api/"):
        return json_message(404, {"error": "Не найдено"}, keep_alive)

    if method not in {"GET", "HEAD"}:
        return json_message(405, {"error": "Метод не поддерживается"}, keep_alive)

    rel = "index.html" if path == "/" else path.lstrip("/")
    loaded = load_static(rel)
    if not loaded:
        return http_message(
            404,
            b"Not Found",
            content_type="text/plain; charset=utf-8",
            keep_alive=keep_alive,
        )
    data, content_type = loaded
    return http_message(
        200,
        b"" if method == "HEAD" else data,
        content_type=content_type,
        keep_alive=keep_alive,
        extra_headers=["Cache-Control: no-cache"],
    )


async def client_connected(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    peer = writer.get_extra_info("peername")
    try:
        requests_handled = 0
        while True:
            try:
                method, target, headers, body = await read_http_request(reader)
            except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionResetError, ValueError):
                break

            upgrade = headers.get("upgrade", "").lower()
            path = urlparse(target).path or "/"
            if upgrade == "websocket" and path == "/ws":
                await handle_websocket(reader, writer, headers)
                return

            connection_hdr = headers.get("connection", "").lower()
            keep_alive = "close" not in connection_hdr and requests_handled < 50
            response = build_http_response(method, target, headers, body, keep_alive)
            writer.write(response)
            await writer.drain()
            requests_handled += 1
            if not keep_alive:
                break
    except Exception as exc:
        log(f"connection error from {peer}: {exc}")
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def http_redirect_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        method, target, headers, _body = await read_http_request(reader)
        host = headers.get("host", f"localhost:{HTTP_REDIRECT_PORT}").split(":")[0]
        location = f"https://{host}:{PORT}{urlparse(target).path or '/'}"
        if urlparse(target).query:
            location += "?" + urlparse(target).query
        body = b""
        writer.write(
            http_message(
                302,
                body,
                content_type="text/plain",
                keep_alive=False,
                extra_headers=[f"Location: {location}"],
            )
        )
        await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def main() -> None:
    if not ROOT.exists():
        raise SystemExit(f"Папка frontend не найдена: {ROOT}")

    db.init_db()
    ssl_ctx = tlsutil.make_ssl_context()

    for rel in (
        "index.html",
        "lesson.html",
        "auth.html",
        "app.html",
        "css/styles.css",
        "js/home.js",
        "js/lesson.js",
        "js/auth.js",
        "js/cabinet.js",
    ):
        load_static(rel)

    tls_server = await asyncio.start_server(client_connected, HOST, PORT, ssl=ssl_ctx)
    redirect_server = await asyncio.start_server(http_redirect_client, HOST, HTTP_REDIRECT_PORT)

    log(f"УрокLive HTTPS: https://127.0.0.1:{PORT}")
    log(f"HTTP redirect: http://127.0.0.1:{HTTP_REDIRECT_PORT} -> https")
    log("Примите самоподписанный сертификат в браузере при первом заходе.")

    async with tls_server, redirect_server:
        await asyncio.gather(
            tls_server.serve_forever(),
            redirect_server.serve_forever(),
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("Сервер остановлен")
