"""Local-only chat UI and persistent sessions; credentials never enter browser payloads."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
from urllib.parse import urlsplit
import uuid

import research_assistant as research
from .llm import LLMError

STATIC = Path(__file__).resolve().parent / "web_static"
ID = re.compile(r"^[0-9a-f]{32}$")


class WebError(Exception):
    def __init__(self, message: str, status: int = 400):
        self.status = status
        super().__init__(message)


def now() -> str:
    return datetime.now(research.TZ).isoformat()


class SessionStore:
    def __init__(self, state: Path):
        research.private_dir(state / "web")
        self.path = state / "web" / "sessions.sqlite"
        self.lock = threading.RLock()
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        with self.connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS turns (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                    question TEXT NOT NULL, status TEXT NOT NULL, result TEXT, error TEXT,
                    created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS turns_session ON turns(session_id, created_at);
            """)
            # A process restart cannot resume an in-flight model request.
            connection.execute("UPDATE turns SET status='error', error=? WHERE status='running'",
                               ("上次服务关闭时分析尚未完成，请重新提问。",))

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def create(self) -> dict:
        session = {"id": uuid.uuid4().hex, "title": "新的研究", "created_at": now(), "updated_at": now()}
        with self.lock, self.connect() as connection:
            connection.execute("INSERT INTO sessions VALUES (:id,:title,:created_at,:updated_at)", session)
        return {**session, "turns": []}

    def list(self) -> list[dict]:
        with self.lock, self.connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT 100")]

    def get(self, session_id: str) -> dict:
        if not ID.fullmatch(session_id):
            raise WebError("未找到该会话。", 404)
        with self.lock, self.connect() as connection:
            row = connection.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            if row is None:
                raise WebError("未找到该会话。", 404)
            turns = []
            for turn in connection.execute("SELECT * FROM turns WHERE session_id=? ORDER BY rowid", (session_id,)):
                item = dict(turn)
                item.pop("session_id")
                item["result"] = json.loads(item["result"]) if item["result"] else None
                turns.append(item)
        return {**dict(row), "turns": turns}

    def start(self, session_id: str, question: str, turn_id: str):
        session = self.get(session_id)
        if len(session["turns"]) >= 200:
            raise WebError("这个会话已达到200次提问，请新建研究。")
        with self.lock, self.connect() as connection:
            connection.execute("INSERT INTO turns VALUES (?,?,?,'running',NULL,NULL,?)",
                               (turn_id, session_id, question, now()))
            connection.execute("UPDATE sessions SET title=?,updated_at=? WHERE id=?",
                               (question[:28] if not session["turns"] else session["title"], now(), session_id))

    def finish(self, session_id: str, turn_id: str, result=None, error=None):
        with self.lock, self.connect() as connection:
            connection.execute("UPDATE turns SET status=?,result=?,error=? WHERE id=? AND session_id=?",
                               ("error" if error else "ok", json.dumps(result, ensure_ascii=False,
                                allow_nan=False) if result else None, error, turn_id, session_id))
            connection.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now(), session_id))

    def history(self, session_id: str) -> list[dict]:
        turns = []
        for turn in self.get(session_id)["turns"]:
            if turn["status"] != "ok" or not turn["result"]:
                continue
            result = turn["result"]
            scopes = []
            for evidence in result.get("evidence", []):
                if evidence.get("result", {}).get("status") not in {"ok", "unavailable"}:
                    continue
                try:
                    arguments = json.loads(evidence["arguments"])
                    scopes.append({"tool": evidence["tool"], **{k: v for k, v in arguments.items()
                                   if k in {"by", "target", "start_date", "end_date", "analysis"}}})
                except (ValueError, KeyError, TypeError, AttributeError):
                    continue
            turns.append({"question": turn["question"], "answer": result["answer"],
                          "snapshot_versions": result.get("snapshot_versions", []), "query_scopes": scopes})
        return turns[-6:]


class ChatService:
    def __init__(self, state: Path, agent_factory, status_provider):
        self.store = SessionStore(state)
        self.agent_factory = agent_factory
        self.status_provider = status_provider
        self.token = secrets.token_urlsafe(32)
        self.jobs = {}
        self.active = {}
        self.lock = threading.RLock()

    def status(self) -> dict:
        try:
            status = self.status_provider()
        except (LLMError, research.AnalysisError, OSError, ValueError, KeyError, TypeError):
            status = {"ready": False, "error": "本地配置或飞书同步版本不可用，请检查启动说明。"}
        return {"app": "socialdog-local-research", "csrf_token": self.token, **status}

    def session(self, session_id: str) -> dict:
        with self.lock:
            return {**self.store.get(session_id), "active_job_id": self.active.get(session_id)}

    def submit(self, session_id: str, question: str) -> dict:
        if not isinstance(question, str) or not question.strip() or len(question) > 4000:
            raise WebError("问题不能为空，且最多4000字。")
        question = question.strip()
        with self.lock:
            if session_id in self.active:
                raise WebError("当前会话正在分析，请等待完成后再追问。", 409)
            if len(self.active) >= 3:
                raise WebError("当前分析任务较多，请稍后重试。", 429)
            history = self.store.history(session_id)
            # Validate configuration before saving a pending turn or spending model credits.
            agent = self.agent_factory()
            job_id = uuid.uuid4().hex
            self.store.start(session_id, question, job_id)
            self.jobs[job_id] = {"id": job_id, "session_id": session_id, "status": "running",
                                 "events": [], "result": None, "error": None}
            self.active[session_id] = job_id
            # Bound in-memory completed jobs; durable results remain in the session DB.
            for old_id in list(self.jobs):
                if len(self.jobs) <= 100:
                    break
                if self.jobs[old_id]["status"] != "running":
                    del self.jobs[old_id]
            threading.Thread(target=self._run, args=(agent, job_id, session_id, question, history),
                             daemon=True, name="socialdog-question-" + job_id[:8]).start()
        return {"job_id": job_id}

    def _run(self, agent, job_id, session_id, question, history):
        def event(item):
            with self.lock:
                self.jobs[job_id]["events"].append({**item, "time": now()})
                self.jobs[job_id]["events"] = self.jobs[job_id]["events"][-80:]

        result, error = None, None
        try:
            result = agent.ask(question, history=history, on_event=event)
        except (LLMError, ValueError) as exc:
            error = str(exc)
        except Exception:
            error = "本地分析遇到异常，本次未生成结论，请重试。"
        with self.lock:
            try:
                self.store.finish(session_id, job_id, result=result, error=error)
            except Exception:
                result, error = None, "会话记录保存失败，请检查本机磁盘后重试。"
            self.jobs[job_id].update(status="error" if error else "ok", result=result, error=error)
            self.active.pop(session_id, None)

    def job(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise WebError("任务已结束或服务已重启，请查看会话记录。", 404)
            # Deep copy before another thread updates events.
            return json.loads(json.dumps(self.jobs[job_id], ensure_ascii=False))


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, service: ChatService):
        self.service = service
        super().__init__(("127.0.0.1", port), Handler)
        self.origin = f"http://127.0.0.1:{self.server_port}"


class Handler(BaseHTTPRequestHandler):
    server_version = "SocialDogLocal/1.0"

    def log_message(self, format, *args):
        # No questions, query strings, local paths or credentials in access logs.
        pass

    def respond(self, status: int, payload, content_type="application/json; charset=utf-8"):
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode() if isinstance(payload, dict) else payload
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; "
                         "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def guard(self, mutation=False):
        expected = urlsplit(self.server.origin).netloc
        if self.headers.get("Host") != expected:
            raise WebError("请使用本机启动页的地址访问。", 403)
        origin = self.headers.get("Origin")
        if origin and origin != self.server.origin:
            raise WebError("仅允许本地页面访问。", 403)
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise WebError("仅允许本地页面访问。", 403)
        if mutation and (origin != self.server.origin or not secrets.compare_digest(
                self.headers.get("X-Socialdog-Token", ""), self.server.service.token)):
            raise WebError("页面验证已失效，请刷新后重试。", 403)

    def read_body(self):
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            raise WebError("请求必须使用JSON。", 415)
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise WebError("请求长度不正确。") from None
        if not 0 < size <= 20000:
            raise WebError("请求为空或超过长度限制。", 413)
        try:
            payload = json.loads(self.rfile.read(size))
        except (ValueError, UnicodeError):
            raise WebError("请求格式不正确。") from None
        if not isinstance(payload, dict):
            raise WebError("请求格式不正确。")
        return payload

    def handle_request(self, mutation=False):
        try:
            self.guard(mutation)
            path = urlsplit(self.path).path
            service = self.server.service
            if mutation:
                body = self.read_body()
                if path == "/api/sessions" and not body:
                    return self.respond(201, service.store.create())
                match = re.fullmatch(r"/api/sessions/([0-9a-f]{32})/messages", path)
                if match and set(body) == {"question"}:
                    return self.respond(202, service.submit(match[1], body["question"]))
                raise WebError("未找到请求的操作。", 404)
            if path == "/api/status":
                return self.respond(200, service.status())
            if path == "/api/sessions":
                return self.respond(200, {"sessions": service.store.list()})
            match = re.fullmatch(r"/api/sessions/([0-9a-f]{32})", path)
            if match:
                return self.respond(200, service.session(match[1]))
            match = re.fullmatch(r"/api/jobs/([0-9a-f]{32})", path)
            if match:
                return self.respond(200, service.job(match[1]))
            assets = {"/": ("index.html", "text/html; charset=utf-8"),
                      "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                      "/styles.css": ("styles.css", "text/css; charset=utf-8")}
            if path in assets:
                filename, content_type = assets[path]
                return self.respond(200, (STATIC / filename).read_bytes(), content_type)
            raise WebError("未找到该页面。", 404)
        except WebError as exc:
            self.respond(exc.status, {"error": str(exc)})
        except LLMError as exc:
            self.respond(503, {"error": str(exc)})
        except Exception:
            self.respond(500, {"error": "本地服务暂时不可用，请检查配置后重试。"})

    def do_GET(self):
        self.handle_request()

    def do_POST(self):
        self.handle_request(mutation=True)

