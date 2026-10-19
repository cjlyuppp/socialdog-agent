"""SQLite transactions, opaque credentials, durable turns and accounting."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
from zoneinfo import ZoneInfo

from pwdlib import PasswordHash

from .models import SiteSettings
from .migrations import migrate

TZ = ZoneInfo("Asia/Shanghai")
PASSWORDS = PasswordHash.recommended()


def now():
    return datetime.now(TZ).isoformat()


def today():
    return datetime.now(TZ).date().isoformat()


def uid():
    return secrets.token_hex(16)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class APIError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


class Store:
    def __init__(self, path: Path, cutoff: str):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            migrate(db)
            defaults = SiteSettings(cutoff_date=cutoff).model_dump(mode="json")
            db.execute("INSERT OR IGNORE INTO settings VALUES (1,?)", (encoded(defaults),))
        self.path.chmod(0o600)

    @contextmanager
    def connection(self, write=False):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except BaseException:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    def settings(self, db=None):
        if db is None:
            with self.connection() as conn:
                return self.settings(conn)
        return SiteSettings.model_validate(json.loads(db.execute("SELECT value FROM settings WHERE id=1").fetchone()[0])).model_dump(mode="json")

    def recover(self, cutoff=None):
        with self.connection(write=True) as db:
            message = "服务已重启，本次分析未完成，请重新提问。"
            db.execute("UPDATE turns SET status='error',error=? WHERE job_id IN (SELECT id FROM jobs WHERE status IN ('queued','running'))", (message,))
            db.execute("UPDATE jobs SET status='error',error=? WHERE status IN ('queued','running')", (message,))
            db.execute("UPDATE model_calls SET status='interrupted',finished_at=? WHERE status='running'", (now(),))
            if cutoff and not db.execute("SELECT 1 FROM question_bank_active").fetchone():
                settings = self.settings(db)
                settings["cutoff_date"] = cutoff
                db.execute("UPDATE settings SET value=? WHERE id=1", (encoded(settings),))

    def user(self, db, user_id):
        user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not user or user["disabled"]:
            raise APIError(401, "请重新登录。")
        return user

    def quota(self, db, user):
        used = db.execute("SELECT count(*) FROM jobs WHERE user_id=? AND quota_day=?", (user["id"], today())).fetchone()[0]
        site = db.execute("SELECT count(*) FROM jobs WHERE quota_day=?", (today(),)).fetchone()[0]
        chat_used = db.execute("SELECT count(*) FROM jobs j WHERE user_id=? AND quota_day=? AND NOT EXISTS (SELECT 1 FROM attempts a WHERE a.job_id=j.id)", (user["id"], today())).fetchone()[0]
        return {"daily_limit": user["daily_limit"], "used_today": used,
                "remaining_today": max(0, user["daily_limit"] - used),
                "evaluation_reserve": user["evaluation_reserve"],
                "chat_remaining_today": max(0, min(user["daily_limit"] - used, user["daily_limit"] - user["evaluation_reserve"] - chat_used)),
                "site_remaining_today": max(0, self.settings(db)["site_daily_limit"] - site)}

    def me(self, db, user, csrf):
        job = db.execute("SELECT id FROM jobs WHERE user_id=? AND status IN ('queued','running')", (user["id"],)).fetchone()
        return {**{k: user[k] for k in ("id", "username", "display_name", "role")},
                "csrf_token": csrf, "quota": self.quota(db, user), "running_job_id": job[0] if job else None,
                "notice_version": user["notice_version"], "permissions": json.loads(user["permissions"])}

    def public_user(self, db, user):
        return {**{k: user[k] for k in ("id", "username", "display_name", "role", "daily_limit", "evaluation_reserve", "last_active_at")},
                "disabled": bool(user["disabled"]), "invite_pending": bool(user["invite_hash"]),
                "used_today": self.quota(db, user)["used_today"], "permissions": json.loads(user["permissions"])}

    def new_auth(self, db, user_id):
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        expires = (datetime.now(TZ) + timedelta(days=7)).isoformat()
        db.execute("DELETE FROM auth_sessions WHERE expires_at<=?", (now(),))
        db.execute("INSERT INTO auth_sessions VALUES (?,?,?,?)", (digest(token), user_id, csrf, expires))
        db.execute("UPDATE users SET last_active_at=? WHERE id=?", (now(), user_id))
        return token, csrf

    def authenticate(self, token):
        with self.connection() as db:
            session = db.execute("SELECT * FROM auth_sessions WHERE token_hash=? AND expires_at>?", (digest(token or ""), now())).fetchone()
            if not session:
                raise APIError(401, "请先登录。")
            user = self.user(db, session["user_id"])
            if not user["last_active_at"] or user["last_active_at"] < (datetime.now(TZ) - timedelta(minutes=1)).isoformat():
                db.execute("UPDATE users SET last_active_at=? WHERE id=?", (now(), user["id"]))
            return dict(user), session["csrf"]

    def invite(self, db, user_id):
        token = secrets.token_urlsafe(32)
        expires = (datetime.now(TZ) + timedelta(days=3)).isoformat()
        db.execute("UPDATE users SET password_hash=NULL,invite_hash=?,invite_expires_at=? WHERE id=?", (digest(token), expires, user_id))
        db.execute("DELETE FROM auth_sessions WHERE user_id=?", (user_id,))
        return token, expires

    def invite_info(self, db, token):
        user = db.execute("SELECT * FROM users WHERE invite_hash=? AND invite_expires_at>? AND disabled=0", (digest(token), now())).fetchone()
        if not user:
            raise APIError(404, "邀请链接无效或已过期，请联系管理员。")
        return user

    def bootstrap(self, username, display_name):
        from .models import NewUser
        values = NewUser(username=username, display_name=display_name, role="admin")
        with self.connection(write=True) as db:
            if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                raise ValueError("已有账号，请在管理后台创建成员")
            user_id = uid()
            db.execute("INSERT INTO users(id,username,display_name,role,daily_limit) VALUES (?,?,?,?,?)",
                       (user_id, values.username, values.display_name, values.role, self.settings(db)["default_daily_limit"]))
            return self.invite(db, user_id)

    def session(self, db, session_id, user_id):
        row = db.execute("SELECT * FROM sessions WHERE id=? AND user_id=?", (session_id, user_id)).fetchone()
        if not row:
            raise APIError(404, "未找到该对话。")
        return dict(row)

    def turn(self, db, turn_id):
        row = db.execute("SELECT * FROM turns WHERE id=?", (turn_id,)).fetchone()
        return {**{k: row[k] for k in ("id", "question", "status", "created_at", "job_id", "error")},
                "result": json.loads(row["result"]) if row["result"] else None}

    def job(self, db, job_id, user_id):
        row = db.execute("SELECT * FROM jobs WHERE id=? AND user_id=?", (job_id, user_id)).fetchone()
        if not row:
            raise APIError(404, "未找到该任务。")
        position = None
        if row["status"] == "queued":
            position = db.execute("SELECT count(*) FROM jobs WHERE status='queued' AND rowid <= (SELECT rowid FROM jobs WHERE id=?)", (job_id,)).fetchone()[0]
        return {**{k: row[k] for k in ("id", "status", "turn_id", "error")},
                "session_id": row["session_id"] or "eval", "queue_position": position, "events": json.loads(row["events"])}

    def attempt(self, db, attempt_id, user_id):
        row = db.execute("SELECT * FROM attempts WHERE id=? AND user_id=?", (attempt_id, user_id)).fetchone()
        if not row:
            raise APIError(404, "未找到该测评。")
        bank = db.execute("SELECT questions FROM question_bank_version WHERE id=?", (row["bank_version_id"],)).fetchone()
        question = next((q for q in json.loads(bank[0]) if q["id"] == row["question_id"]), None) if bank else None
        rated = bool(db.execute("SELECT 1 FROM ratings WHERE attempt_id=?", (attempt_id,)).fetchone())
        turn = self.turn(db, row["turn_id"])
        panels = []
        if row["anonymous_ab"] and turn["result"]:
            answers = {"own": row["own_answer"], "agent": turn["result"]["answer"]}
            panels = [{"label": label, "answer": answers[kind], **({"source": kind} if rated else {})}
                      for label, kind in zip(("A", "B"), json.loads(row["answer_order"]))]
            if not rated:
                turn["result"] = None
        return {**{k: row[k] for k in ("id", "question_id", "own_answer", "job_id", "bank_version_id")},
                "question_snapshot": question,
                "turn": turn, "anonymous_ab": bool(row["anonymous_ab"]), "answer_panels": panels,
                "rated": rated}

    def submit(self, user_id, question, config, session_id=None, evaluation=None):
        if not question.strip():
            raise APIError(400, "问题不能为空。")
        with self.connection(write=True) as db:
            user = self.user(db, user_id)
            if session_id:
                self.session(db, session_id, user_id)
            if db.execute("SELECT 1 FROM jobs WHERE user_id=? AND status IN ('queued','running')", (user_id,)).fetchone():
                raise APIError(409, "上一个问题还在分析，请稍后继续。")
            quota = self.quota(db, user)
            if quota["remaining_today"] <= 0 or quota["site_remaining_today"] <= 0:
                raise APIError(429, "今日提问次数已用完，请明天再来。")
            if not evaluation and quota["chat_remaining_today"] <= 0:
                raise APIError(429, "今日研究对话额度已用完，预留额度仍可用于测评。")
            # The quota reservation, task and turn commit together, before any model call.
            turn_id, job_id, stamp = uid(), uid(), now()
            settings = self.settings(db)
            config = {**config, "min_orders_per_group": settings["min_orders_per_group"]}
            active = db.execute("SELECT version_id FROM question_bank_active WHERE id=1").fetchone()
            if not evaluation and active and config.get("bank_version_id") != active[0]:
                raise APIError(409, "研究数据范围已更新，请刷新后重新提问。")
            if evaluation:
                bank = db.execute("SELECT * FROM question_bank_version WHERE id=?", (evaluation["bank_version_id"],)).fetchone()
                if not bank or bank["dataset_version"] != config["dataset_version"]:
                    raise APIError(409, "这份题目对应的数据包未加载，请联系管理员。")
                config["min_orders_per_group"] = bank["threshold_N"]
                config["bank_version_id"] = bank["id"]
                assignment_id = evaluation.get("assignment_id")
                if assignment_id:
                    assigned = db.execute("SELECT * FROM assignment WHERE id=? AND user_id=? AND question_id=? AND bank_version_id=?", (assignment_id, user_id, evaluation["id"], bank["id"])).fetchone()
                    if not assigned:
                        raise APIError(404, "未找到该待办。")
                    if assigned["attempt_id"]:
                        raise APIError(409, "这项待办已经提交，请在我的待办中查看。")
            db.execute("INSERT INTO turns VALUES (?,?,?,?,?,?,?,?,?)", (turn_id, user_id, session_id, question.strip(), "running", stamp, job_id, None, None))
            events = [{"phase": "queued", "text": "正在排队", "at": stamp}]
            db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?)", (job_id, user_id, session_id, turn_id, "queued", stamp, today(), encoded(config), encoded(events), None))
            if session_id:
                db.execute("UPDATE sessions SET title=CASE WHEN title='新的对话' THEN ? ELSE title END,updated_at=? WHERE id=?", (question.strip()[:28], stamp, session_id))
            if evaluation:
                attempt_id = uid()
                order = ["own", "agent"] if secrets.randbelow(2) else ["agent", "own"]
                db.execute("INSERT INTO attempts(id,user_id,question_id,question_title,own_answer,turn_id,job_id,bank_version_id,anonymous_ab,answer_order) VALUES (?,?,?,?,?,?,?,?,?,?)", (attempt_id, user_id, evaluation["id"], evaluation["title"], evaluation["own_answer"], turn_id, job_id, evaluation["bank_version_id"], int(settings["anonymous_ab_evaluation"]), encoded(order)))
                # A saved independent answer is a submission, even if the model later fails.
                db.execute("UPDATE assignment SET attempt_id=? WHERE user_id=? AND question_id=? AND bank_version_id=? AND attempt_id IS NULL", (attempt_id, user_id, evaluation["id"], evaluation["bank_version_id"]))
                return self.attempt(db, attempt_id, user_id)
            return {"turn": self.turn(db, turn_id), "job": self.job(db, job_id, user_id)}

    def add_event(self, job_id, event):
        with self.connection(write=True) as db:
            row = db.execute("SELECT events,status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row["status"] != "running":
                raise APIError(409, "任务已结束。")
            events = json.loads(row["events"])
            events.append({**event, "at": now()})
            db.execute("UPDATE jobs SET events=? WHERE id=?", (encoded(events), job_id))

    def finish(self, job_id, result=None, error=None):
        status = "error" if error else "ok"
        with self.connection(write=True) as db:
            job = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if job["status"] not in ("queued", "running"):
                return
            db.execute("UPDATE turns SET status=?,result=?,error=? WHERE id=?", (status, encoded(result) if result else None, error, job["turn_id"]))
            db.execute("UPDATE jobs SET status=?,error=? WHERE id=?", (status, error, job_id))
            if job["session_id"]:
                db.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now(), job["session_id"]))

    def call_start(self, job_id):
        with self.connection(write=True) as db:
            job = db.execute("SELECT user_id,status FROM jobs WHERE id=?", (job_id,)).fetchone()
            self.user(db, job["user_id"])
            if job["status"] != "running":
                raise APIError(409, "任务已结束。")
            call_id = uid()
            db.execute("INSERT INTO model_calls(id,job_id,started_at,status) VALUES (?,?,?,'running')", (call_id, job_id, now()))
            return call_id

    def call_finish(self, call_id, response=None):
        usage = response.usage if response else {}
        def count(key):
            value = usage.get(key)
            return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
        with self.connection(write=True) as db:
            db.execute("UPDATE model_calls SET finished_at=?,model=?,response_id=?,status=?,input_tokens=?,output_tokens=?,raw_usage=? WHERE id=?",
                       (now(), response.model if response else None, response.id if response else None,
                        "ok" if response else "error", count("input_tokens"), count("output_tokens"), encoded(usage), call_id))

    def job_usage(self, job_id):
        with self.connection() as db:
            rows = db.execute("SELECT input_tokens,output_tokens FROM model_calls WHERE job_id=?", (job_id,)).fetchall()
        return {key: sum(r[key] for r in rows) if rows and all(r[key] is not None for r in rows) else None
                for key in ("input_tokens", "output_tokens")}
