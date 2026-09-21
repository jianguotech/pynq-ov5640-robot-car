"""
quick_relay_server.py

课程设计用云端 relay 服务。

它同时负责三件事：
1. 保持原有 PYNQ 车端 token 连接方式不变；
2. 给网页端增加注册、登录、驾驶申请、管理员审批、运行记录；
3. 在服务端做控制权限判断，避免多个网页同时给小车发运动命令。

说明：
- 车端仍然连接 /car/control、/car/video、/car/audio，并携带 CAR_RELAY_TOKEN。
- 网页端优先使用登录后的 HttpOnly Cookie。
- 为了不引入额外 Web 框架，HTTP API 使用 GET query 参数；密码不会明文入库，
  但生产环境应改成 HTTPS + POST。
"""

import argparse
import asyncio
from http import HTTPStatus
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from http.cookies import SimpleCookie
from typing import Any, Dict, Iterable, Optional, Set, Tuple
from urllib.parse import parse_qs, urlsplit

import websockets
try:
    from websockets.datastructures import Headers
    from websockets.http11 import Response
except Exception:  # websockets < 11
    Headers = None
    Response = None
from websockets.exceptions import ConnectionClosed
try:
    from websockets.server import WebSocketServerProtocol
except Exception:  # websockets >= 16 deprecates the old name, but typing is optional.
    WebSocketServerProtocol = Any


SESSION_COOKIE = "car_session"
SESSION_TTL_SECONDS = 24 * 60 * 60
DEFAULT_LEASE_MINUTES = 5
USERNAME_RE = re.compile(r"^[A-Za-z0-9_\u4e00-\u9fff-]{2,24}$")

MOVEMENT_ACTIONS = {
    "FORWARD",
    "BACKWARD",
    "LEFT",
    "RIGHT",
    "ROTATE_LEFT",
    "ROTATE_RIGHT",
    "STOP",
    "HOLD",
}
AUTO_ACTIONS = {
    "START_LINE_FOLLOW",
    "LINE_FOLLOW",
    "AUTO_LINE_FOLLOW",
    "FOLLOW_LINE",
    "START_AUTO",
    "AUTO",
    "STOP_AUTO",
    "STOP_LINE_FOLLOW",
}
CLEAR_ESTOP_ACTIONS = {
    "CLEAR_ESTOP",
    "RESET_ESTOP",
    "RELEASE_ESTOP",
}


def now() -> float:
    return time.time()


def json_bytes(payload: Dict[str, Any], status: str = "ok") -> bytes:
    body = {"status": status}
    body.update(payload)
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def public_user(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return {
        "id": row["id"],
        "username": row["username"],
        "role": row["role"],
        "can_drive": bool(row["can_drive"]),
        "created_at": row["created_at"],
    }


def make_password_hash(password: str, salt_hex: Optional[str] = None) -> Tuple[str, str]:
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return salt.hex(), digest.hex()


def verify_password(password: str, salt_hex: str, expected_hash: str) -> bool:
    _, candidate = make_password_hash(password, salt_hex)
    return hmac.compare_digest(candidate, expected_hash)


def first_value(query: Dict[str, Iterable[str]], key: str, default: str = "") -> str:
    values = query.get(key)
    if not values:
        return default
    return str(list(values)[0])


def clean_username(username: str) -> str:
    return username.strip()


def parse_cookie(headers) -> Dict[str, str]:
    raw = ""
    try:
        raw = headers.get("Cookie", "") or headers.get("cookie", "")
    except Exception:
        raw = ""
    cookie = SimpleCookie()
    try:
        cookie.load(raw)
    except Exception:
        return {}
    return {key: morsel.value for key, morsel in cookie.items()}


class AuthDB:
    def __init__(self, db_file: str):
        self.db_file = db_file
        os.makedirs(os.path.dirname(os.path.abspath(db_file)), exist_ok=True)
        self.conn = sqlite3.connect(db_file, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.init_schema()

    def init_schema(self) -> None:
        cur = self.conn.cursor()
        cur.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'viewer',
                can_drive INTEGER NOT NULL DEFAULT 0,
                disabled INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS driver_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                requested_at REAL NOT NULL,
                decided_at REAL,
                decided_by INTEGER,
                note TEXT DEFAULT '',
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS control_leases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                granted_by INTEGER,
                starts_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                revoked_at REAL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS run_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                operator_user_id INTEGER,
                mode TEXT NOT NULL DEFAULT 'manual',
                started_at REAL NOT NULL,
                ended_at REAL,
                estop_count INTEGER NOT NULL DEFAULT 0,
                final_status TEXT DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY(operator_user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                user_id INTEGER,
                action TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            """
        )
        self.conn.commit()

    def user_count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])

    def audit(self, user_id: Optional[int], action: str, detail: Dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO audit_logs(ts,user_id,action,detail) VALUES(?,?,?,?)",
            (now(), user_id, action, json.dumps(detail, ensure_ascii=False, separators=(",", ":"))),
        )
        self.conn.commit()

    def create_user(self, username: str, password: str) -> sqlite3.Row:
        username = clean_username(username)
        if not USERNAME_RE.match(username):
            raise ValueError("用户名需要 2-24 位，可包含中文、英文、数字、下划线和短横线")
        if len(password) < 6:
            raise ValueError("密码至少 6 位")

        first_user = self.user_count() == 0
        role = "admin" if first_user else "viewer"
        can_drive = 1 if first_user else 0
        salt, digest = make_password_hash(password)
        try:
            cur = self.conn.execute(
                """
                INSERT INTO users(username,password_salt,password_hash,role,can_drive,created_at)
                VALUES(?,?,?,?,?,?)
                """,
                (username, salt, digest, role, can_drive, now()),
            )
            self.conn.commit()
        except sqlite3.IntegrityError:
            raise ValueError("用户名已存在")

        user = self.get_user(cur.lastrowid)
        self.audit(user["id"], "register", {"username": username, "role": role})
        return user

    def get_user(self, user_id: int) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM users WHERE id=? AND disabled=0",
            (user_id,),
        ).fetchone()

    def get_user_by_name(self, username: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM users WHERE username=? AND disabled=0",
            (clean_username(username),),
        ).fetchone()

    def login(self, username: str, password: str) -> sqlite3.Row:
        user = self.get_user_by_name(username)
        if user is None or not verify_password(password, user["password_salt"], user["password_hash"]):
            raise ValueError("用户名或密码错误")
        self.audit(user["id"], "login", {"username": user["username"]})
        return user

    def create_session(self, user_id: int) -> str:
        token = secrets.token_urlsafe(32)
        created = now()
        self.conn.execute(
            "INSERT INTO sessions(token,user_id,created_at,expires_at) VALUES(?,?,?,?)",
            (token, user_id, created, created + SESSION_TTL_SECONDS),
        )
        self.conn.commit()
        return token

    def delete_session(self, token: str) -> None:
        if token:
            self.conn.execute("DELETE FROM sessions WHERE token=?", (token,))
            self.conn.commit()

    def session_user(self, token: str) -> Optional[sqlite3.Row]:
        if not token:
            return None
        self.conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now(),))
        self.conn.commit()
        return self.conn.execute(
            """
            SELECT users.* FROM sessions
            JOIN users ON users.id=sessions.user_id
            WHERE sessions.token=? AND sessions.expires_at>=? AND users.disabled=0
            """,
            (token, now()),
        ).fetchone()

    def current_lease(self) -> Optional[sqlite3.Row]:
        self.conn.execute(
            "UPDATE control_leases SET active=0 WHERE active=1 AND expires_at<?",
            (now(),),
        )
        self.conn.commit()
        return self.conn.execute(
            """
            SELECT control_leases.*, users.username, users.role
            FROM control_leases
            JOIN users ON users.id=control_leases.user_id
            WHERE control_leases.active=1 AND control_leases.expires_at>=?
            ORDER BY control_leases.id DESC LIMIT 1
            """,
            (now(),),
        ).fetchone()

    def can_control(self, user: Optional[sqlite3.Row]) -> bool:
        if user is None:
            return False
        if user["role"] == "admin":
            return True
        lease = self.current_lease()
        return bool(lease and lease["user_id"] == user["id"])

    def can_clear_estop(self, user: Optional[sqlite3.Row]) -> bool:
        return self.can_control(user)

    def request_driver(self, user: sqlite3.Row) -> sqlite3.Row:
        pending = self.conn.execute(
            "SELECT * FROM driver_requests WHERE user_id=? AND status='pending' ORDER BY id DESC LIMIT 1",
            (user["id"],),
        ).fetchone()
        if pending is not None:
            return pending
        cur = self.conn.execute(
            "INSERT INTO driver_requests(user_id,status,requested_at) VALUES(?,'pending',?)",
            (user["id"], now()),
        )
        self.conn.commit()
        self.audit(user["id"], "request_driver", {"request_id": cur.lastrowid})
        return self.conn.execute("SELECT * FROM driver_requests WHERE id=?", (cur.lastrowid,)).fetchone()

    def approve_driver_request(self, request_id: int, admin: sqlite3.Row, minutes: int) -> sqlite3.Row:
        if admin["role"] != "admin":
            raise PermissionError("只有管理员可以审批驾驶申请")
        req = self.conn.execute(
            "SELECT * FROM driver_requests WHERE id=? AND status='pending'",
            (request_id,),
        ).fetchone()
        if req is None:
            raise ValueError("驾驶申请不存在或已处理")

        minutes = max(1, min(int(minutes or DEFAULT_LEASE_MINUTES), 120))
        start = now()
        expires = start + minutes * 60
        self.conn.execute("UPDATE control_leases SET active=0, revoked_at=? WHERE active=1", (start,))
        self.conn.execute(
            "UPDATE users SET role='driver', can_drive=1 WHERE id=?",
            (req["user_id"],),
        )
        self.conn.execute(
            "UPDATE driver_requests SET status='approved', decided_at=?, decided_by=? WHERE id=?",
            (start, admin["id"], request_id),
        )
        cur = self.conn.execute(
            "INSERT INTO control_leases(user_id,granted_by,starts_at,expires_at,active) VALUES(?,?,?,?,1)",
            (req["user_id"], admin["id"], start, expires),
        )
        self.conn.commit()
        self.audit(admin["id"], "approve_driver", {"request_id": request_id, "lease_id": cur.lastrowid, "minutes": minutes})
        return self.current_lease()

    def reject_driver_request(self, request_id: int, admin: sqlite3.Row) -> None:
        if admin["role"] != "admin":
            raise PermissionError("只有管理员可以拒绝驾驶申请")
        self.conn.execute(
            "UPDATE driver_requests SET status='rejected', decided_at=?, decided_by=? WHERE id=? AND status='pending'",
            (now(), admin["id"], request_id),
        )
        self.conn.commit()
        self.audit(admin["id"], "reject_driver", {"request_id": request_id})

    def revoke_current_lease(self, admin: sqlite3.Row) -> None:
        if admin["role"] != "admin":
            raise PermissionError("只有管理员可以收回控制权")
        self.conn.execute("UPDATE control_leases SET active=0, revoked_at=? WHERE active=1", (now(),))
        self.conn.commit()
        self.audit(admin["id"], "revoke_lease", {})

    def active_run(self) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT run_sessions.*, users.username AS operator_name
            FROM run_sessions
            LEFT JOIN users ON users.id=run_sessions.operator_user_id
            WHERE run_sessions.active=1
            ORDER BY run_sessions.id DESC LIMIT 1
            """
        ).fetchone()

    def start_run(self, user: sqlite3.Row, mode: str) -> sqlite3.Row:
        active = self.active_run()
        if active is not None:
            return active
        cur = self.conn.execute(
            "INSERT INTO run_sessions(operator_user_id,mode,started_at,active) VALUES(?,?,?,1)",
            (user["id"], mode[:32] or "manual", now()),
        )
        self.conn.commit()
        self.audit(user["id"], "run_start", {"mode": mode})
        return self.active_run()

    def end_run(self, user: sqlite3.Row, final_status: str = "") -> Optional[sqlite3.Row]:
        active = self.active_run()
        if active is None:
            return None
        self.conn.execute(
            "UPDATE run_sessions SET active=0, ended_at=?, final_status=? WHERE id=?",
            (now(), final_status[:200], active["id"]),
        )
        self.conn.commit()
        self.audit(user["id"], "run_end", {"run_id": active["id"], "final_status": final_status})
        return self.conn.execute("SELECT * FROM run_sessions WHERE id=?", (active["id"],)).fetchone()

    def increment_estop(self) -> None:
        active = self.active_run()
        if active is not None:
            self.conn.execute(
                "UPDATE run_sessions SET estop_count=estop_count+1 WHERE id=?",
                (active["id"],),
            )
            self.conn.commit()

    def list_users(self) -> list:
        rows = self.conn.execute(
            "SELECT id,username,role,can_drive,disabled,created_at FROM users ORDER BY id DESC LIMIT 100"
        ).fetchall()
        return [dict(row) for row in rows]

    def list_driver_requests(self) -> list:
        rows = self.conn.execute(
            """
            SELECT driver_requests.*, users.username
            FROM driver_requests
            JOIN users ON users.id=driver_requests.user_id
            ORDER BY driver_requests.id DESC LIMIT 100
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def list_runs(self) -> list:
        rows = self.conn.execute(
            """
            SELECT run_sessions.*, users.username AS operator_name
            FROM run_sessions
            LEFT JOIN users ON users.id=run_sessions.operator_user_id
            ORDER BY run_sessions.id DESC LIMIT 100
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def list_audit_logs(self) -> list:
        rows = self.conn.execute(
            """
            SELECT audit_logs.*, users.username
            FROM audit_logs
            LEFT JOIN users ON users.id=audit_logs.user_id
            ORDER BY audit_logs.id DESC LIMIT 120
            """
        ).fetchall()
        return [dict(row) for row in rows]


class RelayServer:
    def __init__(self, token: str, web_file: str, db_file: str, video_send_timeout: float = 0.2):
        self.token = token
        self.web_file = web_file
        self.video_send_timeout = video_send_timeout
        self.db = AuthDB(db_file)
        self.car_control = None  # type: Optional[WebSocketServerProtocol]
        self.web_controls = set()  # type: Set[WebSocketServerProtocol]
        self.web_control_users = {}  # type: Dict[WebSocketServerProtocol, Optional[sqlite3.Row]]
        self.web_videos = set()  # type: Set[WebSocketServerProtocol]
        self.car_audio = None  # type: Optional[WebSocketServerProtocol]
        self.web_audios = set()  # type: Set[WebSocketServerProtocol]
        self.web_audio_users = {}  # type: Dict[WebSocketServerProtocol, Optional[sqlite3.Row]]

    def token_ok(self, path: str) -> bool:
        query = parse_qs(urlsplit(path).query)
        return first_value(query, "token") == self.token

    def cookie_user(self, headers) -> Optional[sqlite3.Row]:
        cookies = parse_cookie(headers)
        return self.db.session_user(cookies.get(SESSION_COOKIE, ""))

    def auth_user(self, path: str, headers) -> Optional[sqlite3.Row]:
        if self.token_ok(path):
            # 兼容旧 token 页面：只要知道 relay token，就按管理员权限处理。
            return {
                "id": 0,
                "username": "token-admin",
                "role": "admin",
                "can_drive": 1,
                "created_at": now(),
            }
        return self.cookie_user(headers)

    def http_response(
        self,
        status: HTTPStatus,
        content_type: str,
        body: bytes,
        extra_headers: Optional[list] = None,
    ):
        headers = [
            ("Content-Type", content_type),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
            ("Access-Control-Allow-Credentials", "true"),
        ]
        if extra_headers:
            headers.extend(extra_headers)

        if Response is not None and Headers is not None:
            response_headers = Headers()
            for key, value in headers:
                response_headers[key] = value
            return Response(status.value, status.phrase, response_headers, body)

        return status, headers, body

    def json_response(self, payload: Dict[str, Any], status: HTTPStatus = HTTPStatus.OK, extra_headers: Optional[list] = None):
        return self.http_response(status, "application/json; charset=utf-8", json_bytes(payload), extra_headers)

    def error_response(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST):
        return self.http_response(status, "application/json; charset=utf-8", json_bytes({"message": message}, "error"))

    def session_cookie_header(self, token: str) -> Tuple[str, str]:
        return (
            "Set-Cookie",
            f"{SESSION_COOKIE}={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={SESSION_TTL_SECONDS}",
        )

    def clear_cookie_header(self) -> Tuple[str, str]:
        return ("Set-Cookie", f"{SESSION_COOKIE}=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0")

    def state_payload(self, user: Optional[sqlite3.Row]) -> Dict[str, Any]:
        lease = self.db.current_lease()
        active_run = self.db.active_run()
        current_driver = None
        if lease is not None:
            current_driver = {
                "lease_id": lease["id"],
                "user_id": lease["user_id"],
                "username": lease["username"],
                "expires_at": lease["expires_at"],
            }
        user_obj = public_user(user)
        return {
            "type": "campus_state",
            "user": user_obj,
            "permissions": {
                "authenticated": user is not None,
                "is_admin": bool(user and user["role"] == "admin"),
                "can_control": self.db.can_control(user),
                "can_clear_estop": self.db.can_clear_estop(user),
                "can_estop": user is not None,
            },
            "current_driver": current_driver,
            "active_run": dict(active_run) if active_run is not None else None,
            "car_online": self.car_control is not None,
            "web_control_clients": len(self.web_controls),
            "web_video_clients": len(self.web_videos),
            "first_user_will_be_admin": self.db.user_count() == 0,
        }

    async def send_state(self, ws: WebSocketServerProtocol, user: Optional[sqlite3.Row]) -> None:
        await self.safe_send(ws, json.dumps(self.state_payload(user), ensure_ascii=False, separators=(",", ":")))

    def fresh_connected_user(self, user: Optional[sqlite3.Row]) -> Optional[sqlite3.Row]:
        if user is None:
            return None
        try:
            user_id = int(user["id"])
        except Exception:
            return user
        if user_id <= 0:
            return user
        return self.db.get_user(user_id) or user

    async def broadcast_state(self) -> None:
        for ws in list(self.web_controls):
            user = self.fresh_connected_user(self.web_control_users.get(ws))
            self.web_control_users[ws] = user
            await self.send_state(ws, user)

    async def handle_api(self, route: str, query: Dict[str, Any], headers):
        cookies = parse_cookie(headers)
        session_token = cookies.get(SESSION_COOKIE, "")
        user = self.db.session_user(session_token)

        try:
            if route == "/api/me":
                return self.json_response(self.state_payload(user))

            if route == "/api/register":
                new_user = self.db.create_user(first_value(query, "username"), first_value(query, "password"))
                token = self.db.create_session(new_user["id"])
                return self.json_response(
                    self.state_payload(new_user),
                    extra_headers=[self.session_cookie_header(token)],
                )

            if route == "/api/login":
                login_user = self.db.login(first_value(query, "username"), first_value(query, "password"))
                token = self.db.create_session(login_user["id"])
                return self.json_response(
                    self.state_payload(login_user),
                    extra_headers=[self.session_cookie_header(token)],
                )

            if route == "/api/logout":
                self.db.delete_session(session_token)
                return self.json_response({"message": "logged out"}, extra_headers=[self.clear_cookie_header()])

            if user is None:
                return self.error_response("请先登录", HTTPStatus.UNAUTHORIZED)

            if route == "/api/request_driver":
                req = self.db.request_driver(user)
                await self.broadcast_state()
                return self.json_response({"request": dict(req), **self.state_payload(user)})

            if route == "/api/run/start":
                if not self.db.can_control(user):
                    return self.error_response("没有驾驶权限，不能开始校园跑", HTTPStatus.FORBIDDEN)
                run = self.db.start_run(user, first_value(query, "mode", "manual"))
                await self.broadcast_state()
                return self.json_response({"run": dict(run), **self.state_payload(user)})

            if route == "/api/run/end":
                if not self.db.can_control(user):
                    return self.error_response("没有驾驶权限，不能结束校园跑", HTTPStatus.FORBIDDEN)
                run = self.db.end_run(user, first_value(query, "final_status", ""))
                await self.broadcast_state()
                return self.json_response({"run": dict(run) if run else None, **self.state_payload(user)})

            if route == "/api/runs":
                return self.json_response({"runs": self.db.list_runs(), **self.state_payload(user)})

            if route.startswith("/api/admin/"):
                if user["role"] != "admin":
                    return self.error_response("需要管理员权限", HTTPStatus.FORBIDDEN)
                if route == "/api/admin/users":
                    return self.json_response({"users": self.db.list_users(), **self.state_payload(user)})
                if route == "/api/admin/driver_requests":
                    return self.json_response({"requests": self.db.list_driver_requests(), **self.state_payload(user)})
                if route == "/api/admin/approve_driver":
                    lease = self.db.approve_driver_request(
                        int(first_value(query, "id", "0")),
                        user,
                        int(first_value(query, "minutes", str(DEFAULT_LEASE_MINUTES))),
                    )
                    await self.broadcast_state()
                    return self.json_response({"lease": dict(lease) if lease else None, **self.state_payload(user)})
                if route == "/api/admin/reject_driver":
                    self.db.reject_driver_request(int(first_value(query, "id", "0")), user)
                    await self.broadcast_state()
                    return self.json_response(self.state_payload(user))
                if route == "/api/admin/revoke_lease":
                    self.db.revoke_current_lease(user)
                    await self.send_server_stop("admin_revoke_lease", user)
                    await self.broadcast_state()
                    return self.json_response(self.state_payload(user))
                if route == "/api/admin/audit_logs":
                    return self.json_response({"logs": self.db.list_audit_logs(), **self.state_payload(user)})

            return self.error_response("未知 API", HTTPStatus.NOT_FOUND)
        except PermissionError as exc:
            return self.error_response(str(exc), HTTPStatus.FORBIDDEN)
        except ValueError as exc:
            return self.error_response(str(exc), HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            return self.error_response(f"服务器异常：{exc}", HTTPStatus.INTERNAL_SERVER_ERROR)

    async def safe_send(self, ws: WebSocketServerProtocol, data, timeout: Optional[float] = None) -> bool:
        try:
            if timeout and timeout > 0:
                await asyncio.wait_for(ws.send(data), timeout=timeout)
            else:
                await ws.send(data)
            return True
        except Exception:
            return False

    async def broadcast(self, clients: Set[WebSocketServerProtocol], data, timeout: Optional[float] = None) -> int:
        snapshot = list(clients)
        if not snapshot:
            return 0

        results = await asyncio.gather(
            *(self.safe_send(client, data, timeout=timeout) for client in snapshot),
            return_exceptions=True,
        )

        dead = []
        for client, ok in zip(snapshot, results):
            if ok is not True:
                dead.append(client)

        for client in dead:
            clients.discard(client)
            self.web_control_users.pop(client, None)
            self.web_audio_users.pop(client, None)
            try:
                await client.close()
            except Exception:
                pass

        return len(dead)

    async def notify_car(self, event: str, **payload) -> None:
        if self.car_control is None:
            return

        msg = {
            "type": "server_event",
            "event": event,
            "web_control_clients": len(self.web_controls),
            "web_video_clients": len(self.web_videos),
        }
        msg.update(payload)
        await self.safe_send(self.car_control, json.dumps(msg, ensure_ascii=False, separators=(",", ":")))

    async def notify_video_demand(self) -> None:
        await self.notify_car("video_demand", enabled=len(self.web_videos) > 0)

    async def notify_control_demand(self) -> None:
        await self.notify_car("control_demand", enabled=len(self.web_controls) > 0)

    async def send_server_stop(self, reason: str, user: Optional[sqlite3.Row] = None) -> None:
        if self.car_control is None:
            return
        payload = {
            "type": "remote_cmd",
            "action": "STOP",
            "manual_enable": 1,
            "vx": 0.0,
            "vy": 0.0,
            "wz": 0.0,
            "speed": 0.0,
            "estop": 0,
            "source": "relay",
            "reason": reason,
            "timestamp_ms": int(now() * 1000),
        }
        if user is not None:
            payload["source_user"] = user["username"]
            payload["source_user_id"] = user["id"]
        await self.safe_send(self.car_control, json.dumps(payload, ensure_ascii=False, separators=(",", ":")))

    async def process_request(self, first, second=None):
        request = second if hasattr(second, "path") else None
        path = getattr(request, "path", None)
        headers = getattr(request, "headers", None)
        if path is None:
            path = first if isinstance(first, str) else getattr(getattr(first, "request", None), "path", "")
        if headers is None and second is not None and not hasattr(second, "path"):
            headers = second

        split = urlsplit(path)
        route = split.path
        query = parse_qs(split.query)
        if route.startswith(("/car/control", "/web/control", "/car/video", "/web/video", "/car/audio", "/web/audio")):
            return None

        if route == "/healthz":
            body = json_bytes({"ok": True, "car_online": self.car_control is not None})
            return self.http_response(HTTPStatus.OK, "application/json; charset=utf-8", body)

        if route.startswith("/api/"):
            return await self.handle_api(route, query, headers)

        if route not in {"/", "/index.html", "/test_web_client.html"}:
            body = b"not found\n"
            return self.http_response(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", body)

        try:
            with open(self.web_file, "rb") as f:
                body = f.read()
        except OSError:
            body = b"control page missing\n"
            return self.http_response(HTTPStatus.INTERNAL_SERVER_ERROR, "text/plain; charset=utf-8", body)

        return self.http_response(HTTPStatus.OK, "text/html; charset=utf-8", body)

    def connection_path_and_headers(self, ws: WebSocketServerProtocol, path: Optional[str]) -> Tuple[str, Any]:
        if path is None:
            request = getattr(ws, "request", None)
            path = getattr(request, "path", None) or getattr(ws, "path", "")
            headers = getattr(request, "headers", None) or getattr(ws, "request_headers", None)
        else:
            headers = getattr(ws, "request_headers", None)
        return path or "", headers

    async def handler(self, ws: WebSocketServerProtocol, path: Optional[str] = None):
        path, headers = self.connection_path_and_headers(ws, path)
        route = urlsplit(path).path

        if route.startswith("/car/"):
            if not self.token_ok(path):
                await ws.close(code=1008, reason="bad token")
                return
            user = None
        else:
            user = self.auth_user(path, headers)
            if user is None:
                await ws.close(code=1008, reason="login required")
                return

        if route.startswith("/car/control"):
            await self.handle_car_control(ws)
        elif route.startswith("/web/control"):
            await self.handle_web_control(ws, user)
        elif route.startswith("/car/video"):
            await self.handle_car_video(ws)
        elif route.startswith("/web/video"):
            await self.handle_web_video(ws)
        elif route.startswith("/car/audio"):
            await self.handle_car_audio(ws)
        elif route.startswith("/web/audio"):
            await self.handle_web_audio(ws, user)
        else:
            await ws.close(code=1008, reason="unknown path")

    async def handle_car_control(self, ws: WebSocketServerProtocol):
        self.car_control = ws
        print("[server] car control connected")
        await self.notify_video_demand()
        await self.notify_control_demand()
        await self.broadcast_state()
        try:
            async for msg in ws:
                await self.broadcast(self.web_controls, msg)
        except ConnectionClosed:
            pass
        finally:
            if self.car_control is ws:
                self.car_control = None
            await self.broadcast_state()
            print("[server] car control disconnected")

    def command_action(self, payload: Dict[str, Any]) -> str:
        action = str(payload.get("action", "")).upper()
        if not action and str(payload.get("cmd", "")).lower() in {"estop", "emergency_stop"}:
            action = "ESTOP"
        if not action and str(payload.get("cmd", "")).lower() in {"stop", "hold"}:
            action = "STOP"
        return action

    def command_allowed(self, user: sqlite3.Row, payload: Dict[str, Any]) -> Tuple[bool, str]:
        action = self.command_action(payload)
        estop = bool(payload.get("estop")) or action == "ESTOP"
        if estop:
            return True, "estop"
        if action in CLEAR_ESTOP_ACTIONS:
            return self.db.can_clear_estop(user), "clear_estop"
        if action in AUTO_ACTIONS or action in MOVEMENT_ACTIONS or payload.get("type") in {"remote_cmd", "cmd"}:
            return self.db.can_control(user), "drive"
        return False, "unknown_command"

    async def handle_web_control(self, ws: WebSocketServerProtocol, user: sqlite3.Row):
        self.web_controls.add(ws)
        self.web_control_users[ws] = user
        print(f"[server] web control connected user={user['username']}")
        await self.send_state(ws, user)
        await self.notify_control_demand()
        try:
            async for msg in ws:
                try:
                    payload = json.loads(msg)
                except json.JSONDecodeError:
                    await self.safe_send(ws, '{"type":"error","msg":"invalid json"}')
                    continue

                allowed, reason = self.command_allowed(user, payload)
                action = self.command_action(payload)
                if not allowed:
                    self.db.audit(user["id"], "control_denied", {"reason": reason, "action": action})
                    if reason == "drive":
                        await self.send_server_stop("drive_command_denied", user)
                    await self.safe_send(ws, json.dumps({
                        "type": "error",
                        "msg": "无驾驶权限，请先申请驾驶或等待管理员审批",
                        "reason": reason,
                    }, ensure_ascii=False))
                    await self.send_state(ws, user)
                    continue

                payload["source_user"] = user["username"]
                payload["source_user_id"] = user["id"]
                msg = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

                if self.car_control is not None:
                    await self.safe_send(self.car_control, msg)
                    if action == "ESTOP" or payload.get("estop"):
                        self.db.increment_estop()
                    if action in {"ESTOP", "CLEAR_ESTOP", "START_LINE_FOLLOW", "STOP_AUTO", "STOP_LINE_FOLLOW"}:
                        self.db.audit(user["id"], "control_command", {"action": action, "payload": payload})
                else:
                    await self.safe_send(ws, '{"type":"error","msg":"car offline"}')
                await self.broadcast_state()
        except ConnectionClosed:
            pass
        finally:
            self.web_controls.discard(ws)
            self.web_control_users.pop(ws, None)
            await self.notify_control_demand()
            await self.broadcast_state()
            print(f"[server] web control disconnected user={user['username']}")

    async def handle_car_video(self, ws: WebSocketServerProtocol):
        print("[server] car video connected")
        try:
            async for frame in ws:
                removed = await self.broadcast(self.web_videos, frame, timeout=self.video_send_timeout)
                if removed:
                    print(f"[server] dropped slow web video clients: {removed}")
                    await self.notify_video_demand()
        except ConnectionClosed:
            pass
        finally:
            print("[server] car video disconnected")

    async def handle_web_video(self, ws: WebSocketServerProtocol):
        self.web_videos.add(ws)
        print("[server] web video connected")
        await self.notify_video_demand()
        await self.broadcast_state()
        try:
            async for _ in ws:
                pass
        except ConnectionClosed:
            pass
        finally:
            self.web_videos.discard(ws)
            await self.notify_video_demand()
            await self.broadcast_state()
            print("[server] web video disconnected")

    async def handle_car_audio(self, ws: WebSocketServerProtocol):
        self.car_audio = ws
        print("[server] car audio connected")
        await self.broadcast(
            self.web_audios,
            json.dumps({"type": "audio_status", "text": "音频助手在线", "online": True}, ensure_ascii=False),
        )
        try:
            async for msg in ws:
                await self.broadcast(self.web_audios, msg)
        except ConnectionClosed:
            pass
        finally:
            if self.car_audio is ws:
                self.car_audio = None
            await self.broadcast(
                self.web_audios,
                json.dumps({"type": "audio_status", "text": "音频助手离线", "online": False}, ensure_ascii=False),
            )
            print("[server] car audio disconnected")

    async def handle_web_audio(self, ws: WebSocketServerProtocol, user: sqlite3.Row):
        self.web_audios.add(ws)
        self.web_audio_users[ws] = user
        print(f"[server] web audio connected user={user['username']}")
        await self.safe_send(
            ws,
            json.dumps(
                {
                    "type": "audio_status",
                    "text": "音频助手在线" if self.car_audio is not None else "音频助手离线",
                    "online": self.car_audio is not None,
                },
                ensure_ascii=False,
            ),
        )
        try:
            async for msg in ws:
                if self.car_audio is not None:
                    await self.safe_send(self.car_audio, msg)
                else:
                    await self.safe_send(ws, '{"type":"audio_status","text":"音频助手离线","online":false}')
        except ConnectionClosed:
            pass
        finally:
            self.web_audios.discard(ws)
            self.web_audio_users.pop(ws, None)
            print(f"[server] web audio disconnected user={user['username']}")


async def main():
    parser = argparse.ArgumentParser(description="校园跑小车 WebSocket/HTTP 中转服务器")
    parser.add_argument("--host", default=os.environ.get("CAR_RELAY_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("CAR_RELAY_PORT", "8000")))
    parser.add_argument("--token", default=os.environ.get("CAR_RELAY_TOKEN", "change_this_token"))
    parser.add_argument(
        "--web-file",
        default=os.environ.get(
            "CAR_RELAY_WEB_FILE",
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_web_client.html"),
        ),
        help="同端口 HTTP 访问时返回的控制网页",
    )
    parser.add_argument(
        "--db-file",
        default=os.environ.get(
            "CAR_RELAY_DB_FILE",
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "car_relay.sqlite3"),
        ),
        help="SQLite 数据库文件",
    )
    parser.add_argument("--ping-interval", type=float, default=30.0, help="WebSocket ping 间隔")
    parser.add_argument("--ping-timeout", type=float, default=15.0, help="WebSocket ping 超时时间")
    parser.add_argument("--video-send-timeout", type=float, default=10.0, help="单个网页视频连接发送超时")
    args = parser.parse_args()

    relay = RelayServer(
        token=args.token,
        web_file=args.web_file,
        db_file=args.db_file,
        video_send_timeout=args.video_send_timeout,
    )

    print(f"[server] ws://{args.host}:{args.port}")
    print(f"[server] web_file={args.web_file}")
    print(f"[server] db_file={args.db_file}")
    print("[server] car token is accepted only on /car/*; web users should login with cookie")

    async with websockets.serve(
        relay.handler,
        args.host,
        args.port,
        max_size=None,
        ping_interval=args.ping_interval,
        ping_timeout=args.ping_timeout,
        process_request=relay.process_request,
    ):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
