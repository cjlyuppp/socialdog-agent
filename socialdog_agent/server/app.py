"""FastAPI endpoints for the research workspace."""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
import csv
import fcntl
import io
import json
from pathlib import Path
import secrets
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException

import chat_agent

from . import models as m
from .data import AgentData, ReferenceData, load_questions
from .jobs import JobRunner
from .store import APIError, PASSWORDS, Store, TZ, digest, encoded, now, uid
from .workflow import Workflow, register_workflow

COOKIE = "socialdog_session"


@dataclass(frozen=True)
class ServerConfig:
    database: Path
    state_dir: Path
    public_origin: str = "https://localhost"
    history_package: Path | None = None
    reference_db: Path | None = None
    question_bank: Path | None = None
    secure_cookie: bool = True
    static_dir: Path | None = None

    def __post_init__(self):
        origin = urlsplit(self.public_origin)
        if (origin.scheme not in ("http", "https") or not origin.hostname or origin.username or origin.password
                or origin.path not in ("", "/") or origin.query or origin.fragment):
            raise ValueError("公开地址必须是有效的 HTTP(S) 来源地址")
        if not self.secure_cookie and (origin.scheme != "http" or origin.hostname not in ("127.0.0.1", "localhost", "::1")):
            raise ValueError("非 Secure cookie 仅可用于显式配置的本机 HTTP 开发环境")


def create_app(config: ServerConfig, *, llm_factory=None, model_name=None):
    if config.history_package is None:
        raise ValueError("请配置经过物理截断的历史回测包")
    source = AgentData(config.history_package) if config.history_package else None
    reference = ReferenceData(config.reference_db, source.cutoff) if config.reference_db and source else None
    if config.reference_db and not source:
        raise ValueError("请先配置对应的 agent 截断包")
    if reference and source:
        # Reference and agent must originate from the same raw export, not merely share T.
        full_version = reference.meta["dataset_version"]
        if not source.description["dataset_version"].startswith(full_version + "_asof"):
            raise ValueError("数据核对库与 agent 数据包来源不一致")
    questions = load_questions(config.question_bank)
    settings = chat_agent.load_settings()
    ready = llm_factory is not None or bool(settings.get("DEEPSEEK_API_KEY", "").strip())
    factory = (lambda job_config: llm_factory()) if llm_factory else (lambda job_config: chat_agent.make_llm({**settings, "DEEPSEEK_MODEL": job_config["model"], "DEEPSEEK_REASONING_EFFORT": job_config["reasoning_effort"]}))
    model = model_name or settings["DEEPSEEK_MODEL"]
    cutoff = source.cutoff if source else "1970-01-01"
    store = Store(config.database, cutoff)
    workflow = Workflow(store, source, questions)
    runner = JobRunner(store, source, Path(config.state_dir), factory)
    dummy_password = PASSWORDS.hash(secrets.token_urlsafe(32))

    @asynccontextmanager
    async def lifespan(app):
        # A second Uvicorn worker must not mark a live worker's jobs as interrupted.
        lock = open(str(store.path) + ".lock", "a")
        try:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("同一工作区只允许一个服务进程，请使用 --workers 1") from None
            store.recover(source.cutoff if source else None)
            runner.start()
            try:
                yield
            finally:
                runner.stop()
        finally:
            lock.close()

    app = FastAPI(title="SocialDog Workspace", version="0.2.0", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.runner, app.state.source = store, runner, source
    app.state.reference = reference

    @app.exception_handler(APIError)
    async def business_error(request, error):
        return JSONResponse({"error": error.message}, status_code=error.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        # Validation bodies may contain passwords. Never echo them into a response/log.
        return JSONResponse({"error": "输入内容不符合要求，请检查日期、数值范围和必填项。"}, status_code=400)

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        return JSONResponse({"error": "未找到该接口。" if error.status_code == 404 else "请求无法完成。"}, status_code=error.status_code)

    @app.exception_handler(Exception)
    async def unexpected_error(request, error):
        return JSONResponse({"error": "服务暂时不可用，请稍后重试。"}, status_code=500)

    origin = config.public_origin.rstrip("/")
    host = urlsplit(origin).hostname

    @app.middleware("http")
    async def request_boundary(request, call_next):
        try:
            request_host = urlsplit("//" + request.headers.get("host", "")).hostname
        except ValueError:
            request_host = None
        if request_host != host:
            return JSONResponse({"error": "访问地址不受支持。"}, status_code=400)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            supplied_origin = request.headers.get("origin")
            if supplied_origin is not None and supplied_origin != origin:
                return JSONResponse({"error": "请求来源不匹配，请刷新页面后重试。"}, status_code=403)
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"error": "不接受跨站请求。"}, status_code=403)
            length = request.headers.get("content-length", "0")
            if not length.isdigit() or int(length) > 65536:
                return JSONResponse({"error": "请求内容过大。"}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def identity(request: Request):
        user, csrf = store.authenticate(request.cookies.get(COOKIE))
        if request.method not in ("GET", "HEAD") and not secrets.compare_digest(request.headers.get("X-CSRF-Token", "").encode(), csrf.encode()):
            raise APIError(403, "页面验证已失效，请刷新后重试。")
        return user, csrf

    def admin(auth=Depends(identity)):
        if auth[0]["role"] != "admin":
            raise APIError(403, "此操作仅管理员可用。")
        return auth

    def set_cookie(response, token):
        response.set_cookie(COOKIE, token, max_age=7 * 86400, httponly=True, secure=config.secure_cookie, samesite="lax", path="/")

    def invite_link(token, expires):
        return {"invite_url": origin + "/invite/" + token, "expires_at": expires}

    model_options = [
        {"id": "deepseek-flash", "label": "DeepSeek Flash", "reasoning_efforts": ["none", "low", "high", "max"]},
        {"id": "deepseek-v4-pro", "label": "DeepSeek V4 Pro", "reasoning_efforts": ["none", "low", "high", "max"]},
    ]
    default_effort = {"minimal": "low", "medium": "high"}.get(settings["DEEPSEEK_REASONING_EFFORT"], settings["DEEPSEEK_REASONING_EFFORT"])

    def prepared(selection):
        if not source:
            raise APIError(503, "历史数据尚未配置，请联系管理员。")
        if not ready:
            raise APIError(503, "研究助理尚未配置，请联系管理员。")
        selected = selection.model or model
        if selection.model is not None and selected not in {item["id"] for item in model_options}:
            raise APIError(400, "请选择可用的 DeepSeek 模型。")
        return {"dataset_version": source.description["dataset_version"], "model": selected,
                "reasoning_effort": selection.reasoning_effort or default_effort}

    @app.get("/api/models", response_model=m.ModelCatalog)
    def models(auth=Depends(identity)):
        return {"models": model_options, "default_model": model, "default_reasoning_effort": default_effort}

    @app.get("/api/me", response_model=m.Me)
    def me(auth=Depends(identity)):
        with store.connection() as db:
            return store.me(db, store.user(db, auth[0]["id"]), auth[1])

    @app.post("/api/auth/login", response_model=m.Me)
    def login(body: m.Login, request: Request, response: Response):
        key = digest((request.client.host if request.client else "") + ":" + body.username)
        with store.connection(write=True) as db:
            db.execute("DELETE FROM login_attempts WHERE expires_at<=?", (now(),))
            attempt = db.execute("SELECT count FROM login_attempts WHERE key=?", (key,)).fetchone()
            if attempt and attempt[0] >= 10:
                raise APIError(429, "登录尝试过多，请 10 分钟后再试。")
            user = db.execute("SELECT * FROM users WHERE username=?", (body.username,)).fetchone()
            password_hash = user["password_hash"] if user and user["password_hash"] else dummy_password
            valid = PASSWORDS.verify(body.password, password_hash) and user is not None and bool(user["password_hash"]) and not user["disabled"]
            if not valid:
                expires = (datetime.now(TZ) + timedelta(minutes=10)).isoformat()
                db.execute("INSERT INTO login_attempts VALUES (?,1,?) ON CONFLICT(key) DO UPDATE SET count=count+1", (key, expires))
            else:
                db.execute("DELETE FROM login_attempts WHERE key=?", (key,))
                token, csrf = store.new_auth(db, user["id"])
                result = store.me(db, store.user(db, user["id"]), csrf)
        if not valid:
            raise APIError(401, "用户名或密码不正确。")
        set_cookie(response, token)
        return result

    @app.post("/api/auth/logout", status_code=204)
    def logout(request: Request, auth=Depends(identity)):
        with store.connection(write=True) as db:
            db.execute("DELETE FROM auth_sessions WHERE token_hash=?", (digest(request.cookies.get(COOKIE, "")),))
        response = Response(status_code=204)
        response.delete_cookie(COOKIE, path="/", secure=config.secure_cookie, httponly=True, samesite="lax")
        return response

    @app.get("/api/auth/invite/{token}", response_model=m.InviteInfo)
    def invite(token: str):
        with store.connection() as db:
            user = store.invite_info(db, token)
            return {"username": user["username"], "display_name": user["display_name"], "expires_at": user["invite_expires_at"]}

    @app.post("/api/auth/invite/accept", response_model=m.Me)
    def accept(body: m.AcceptInvite, response: Response):
        hashed = PASSWORDS.hash(body.password)
        with store.connection(write=True) as db:
            user = store.invite_info(db, body.token)
            db.execute("UPDATE users SET password_hash=?,invite_hash=NULL,invite_expires_at=NULL WHERE id=?", (hashed, user["id"]))
            token, csrf = store.new_auth(db, user["id"])
            result = store.me(db, store.user(db, user["id"]), csrf)
        set_cookie(response, token)
        return result

    @app.get("/api/status", response_model=m.DataStatus)
    def status(auth=Depends(identity)):
        if not source:
            raise APIError(503, "历史数据尚未配置，请联系管理员。")
        return source.status(store.settings(), model)

    @app.get("/api/sessions", response_model=list[m.Session])
    def sessions(auth=Depends(identity)):
        with store.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM sessions WHERE user_id=? ORDER BY updated_at DESC,rowid DESC", (auth[0]["id"],))]

    @app.post("/api/sessions", response_model=m.Session, status_code=201)
    def new_session(auth=Depends(identity)):
        session_id, stamp = uid(), now()
        with store.connection(write=True) as db:
            db.execute("INSERT INTO sessions VALUES (?,?,?,?,?)", (session_id, auth[0]["id"], "新的对话", stamp, stamp))
            return store.session(db, session_id, auth[0]["id"])

    @app.get("/api/sessions/{session_id}", response_model=m.SessionDetail)
    def session_detail(session_id: str, auth=Depends(identity)):
        with store.connection() as db:
            session = store.session(db, session_id, auth[0]["id"])
            session["turns"] = [store.turn(db, row[0]) for row in db.execute("SELECT id FROM turns WHERE session_id=? ORDER BY rowid", (session_id,))]
            return session

    @app.post("/api/sessions/{session_id}/messages", response_model=m.Submitted, status_code=202)
    def ask(session_id: str, body: m.QuestionInput, auth=Depends(identity)):
        result = store.submit(auth[0]["id"], body.question, prepared(body), session_id=session_id)
        runner.wake.set()
        return result

    @app.get("/api/jobs/{job_id}", response_model=m.Job)
    def job(job_id: str, auth=Depends(identity)):
        with store.connection() as db:
            return store.job(db, job_id, auth[0]["id"])

    @app.get("/api/reference/meta", response_model=m.ReferenceMeta)
    def reference_meta(auth=Depends(identity)):
        if not reference:
            raise APIError(503, "核对数据尚未配置，请联系管理员。")
        return reference.metadata(can_view_after_cutoff=auth[0]["role"] == "admin")

    @app.get("/api/reference/query", response_model=m.ReferenceResult)
    def reference_query(query: m.ReferenceQuery = Query(), auth=Depends(identity)):
        if not reference:
            raise APIError(503, "核对数据尚未配置，请联系管理员。")
        return reference.query(query, can_view_after_cutoff=auth[0]["role"] == "admin")

    @app.get("/api/eval/questions", response_model=list[m.VersionedQuestion], response_model_exclude_unset=True)
    def eval_questions(auth=Depends(identity)):
        with store.connection(write=True) as db:
            version = workflow.active_bank(db)
            return [{**q, "bank_version_id": version} for q in questions.values()]

    @app.post("/api/eval/questions/{question_id}/run", response_model=m.EvalAttempt, status_code=202)
    def eval_run(question_id: str, body: m.OwnAnswer, auth=Depends(identity)):
        with store.connection(write=True) as db:
            question = workflow.question(db, question_id, body.bank_version_id)
        if not body.own_answer.strip():
            raise APIError(400, "请先写下自己的答案。")
        result = store.submit(auth[0]["id"], question["question"], prepared(body), evaluation={**question, "own_answer": body.own_answer.strip(), "assignment_id": body.assignment_id})
        runner.wake.set()
        return result

    @app.get("/api/eval/attempts/{attempt_id}", response_model=m.EvalAttempt)
    def eval_attempt(attempt_id: str, auth=Depends(identity)):
        with store.connection() as db:
            return store.attempt(db, attempt_id, auth[0]["id"])

    @app.post("/api/ratings", response_model=m.Rating, status_code=201)
    def rate(body: m.RatingInput, auth=Depends(identity)):
        with store.connection(write=True) as db:
            attempt = db.execute("SELECT * FROM attempts WHERE id=? AND user_id=?", (body.attempt_id, auth[0]["id"])).fetchone()
            if not attempt:
                raise APIError(404, "未找到该测评。")
            if store.turn(db, attempt["turn_id"])["status"] != "ok":
                raise APIError(409, "请等待回答完成后再评分。")
            if db.execute("SELECT 1 FROM ratings WHERE attempt_id=?", (body.attempt_id,)).fetchone():
                raise APIError(409, "这次测评已经评分。")
            result = {"id": uid(), "attempt_id": body.attempt_id, "question_id": attempt["question_id"],
                      "question_title": attempt["question_title"], "question": store.turn(db, attempt["turn_id"])["question"],
                      "own_answer": attempt["own_answer"], "agent_answer": store.turn(db, attempt["turn_id"])["result"]["answer"],
                      "scores": body.scores.model_dump(), "note": body.note.strip(), "created_at": now(), "username": auth[0]["username"]}
            db.execute("INSERT INTO ratings VALUES (?,?,?,?,?)", (result["id"], body.attempt_id, auth[0]["id"], encoded(result), result["created_at"]))
            for issue in body.issues:
                workflow.add_issue(db, auth[0]["id"], "evaluation", attempt["turn_id"], body.attempt_id, issue)
            return result

    @app.get("/api/ratings/mine", response_model=list[m.Rating])
    def ratings_mine(auth=Depends(identity)):
        with store.connection() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT value FROM ratings WHERE user_id=? ORDER BY created_at DESC", (auth[0]["id"],))]

    @app.get("/api/admin/users", response_model=list[m.AdminUser])
    def users(auth=Depends(admin)):
        with store.connection() as db:
            return [store.public_user(db, user) for user in db.execute("SELECT * FROM users ORDER BY rowid")]

    @app.post("/api/admin/users", response_model=m.CreatedUser, status_code=201)
    def create_user(body: m.NewUser, auth=Depends(admin)):
        if not body.display_name.strip():
            raise APIError(400, "显示名不能为空。")
        if body.evaluation_reserve > body.daily_limit:
            raise APIError(400, "测评预留不能超过每日上限。")
        with store.connection(write=True) as db:
            if db.execute("SELECT 1 FROM users WHERE username=?", (body.username,)).fetchone():
                raise APIError(409, "用户名已存在。")
            if db.execute("SELECT count(*) FROM users").fetchone()[0] >= 10:
                raise APIError(409, "最多可创建 10 个账号。")
            user_id = uid()
            db.execute("INSERT INTO users(id,username,display_name,role,daily_limit,evaluation_reserve) VALUES (?,?,?,?,?,?)", (user_id, body.username, body.display_name.strip(), body.role, body.daily_limit, body.evaluation_reserve))
            token, expires = store.invite(db, user_id)
            return {"user": store.public_user(db, store.user(db, user_id)), **invite_link(token, expires)}

    @app.patch("/api/admin/users/{user_id}", response_model=m.AdminUser)
    def update_user(user_id: str, body: m.UserPatch, auth=Depends(admin)):
        patch = body.model_dump(exclude_unset=True)
        if user_id == auth[0]["id"] and (patch.get("disabled") or patch.get("role", "admin") != "admin"):
            raise APIError(409, "不能停用自己或撤销自己的管理员权限。")
        with store.connection(write=True) as db:
            user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not user:
                raise APIError(404, "未找到该成员。")
            if patch.get("evaluation_reserve", user["evaluation_reserve"]) > patch.get("daily_limit", user["daily_limit"]):
                raise APIError(400, "测评预留不能超过每日上限。")
            for key, value in patch.items():
                db.execute(f"UPDATE users SET {key}=? WHERE id=?", (value, user_id))
            if patch.get("disabled") or "role" in patch:
                db.execute("DELETE FROM auth_sessions WHERE user_id=?", (user_id,))
            updated = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            return store.public_user(db, updated)

    @app.post("/api/admin/users/{user_id}/invite", response_model=m.InviteLink)
    def reinvite(user_id: str, auth=Depends(admin)):
        if user_id == auth[0]["id"]:
            raise APIError(409, "请由另一位管理员重置你的密码。")
        with store.connection(write=True) as db:
            if not db.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
                raise APIError(404, "未找到该成员。")
            token, expires = store.invite(db, user_id)
            return invite_link(token, expires)

    @app.get("/api/admin/settings", response_model=m.SiteSettings)
    def get_settings(auth=Depends(admin)):
        return store.settings()

    @app.put("/api/admin/settings", response_model=m.SiteSettings)
    def save_settings(body: m.SiteSettings, auth=Depends(admin)):
        if not source or body.cutoff_date.isoformat() != source.cutoff:
            raise APIError(409, "截止日期须与已加载数据包一致，请先更换数据包并重启服务。")
        values = body.model_dump(mode="json")
        with store.connection(write=True) as db:
            db.execute("UPDATE settings SET value=? WHERE id=1", (encoded(values),))
            workflow.active_bank(db)
        runner.wake.set()
        return values

    @app.get("/api/admin/ratings", response_model=list[m.Rating])
    def ratings_all(auth=Depends(admin)):
        with store.connection() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT value FROM ratings ORDER BY created_at DESC")]

    @app.get("/api/admin/ratings/export.csv")
    def ratings_export(auth=Depends(admin)):
        rows = ratings_all(auth)
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["时间", "成员", "题目", "我的答案", "研究助理回答", "数字", "推理", "口径", "局限性", "备注"])
        def safe(value):
            text = str(value)
            return "'" + text if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else text
        for row in rows:
            writer.writerow([safe(v) for v in [row["created_at"], row["username"], row["question_title"], row["own_answer"], row["agent_answer"],
                            row["scores"]["numbers"], row["scores"]["reasoning"], row["scores"]["definitions"], row["scores"]["limitations"], row["note"]]])
        return Response("\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="ratings.csv"'})

    @app.get("/api/admin/usage", response_model=m.UsageReport)
    def usage(month: str = Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$"), auth=Depends(admin)):
        with store.connection() as db:
            settings = store.settings(db)
            jobs = [dict(row) for row in db.execute("SELECT id,user_id,quota_day,config FROM jobs WHERE substr(quota_day,1,7)=?", (month,))]
            calls = [dict(row) for row in db.execute("SELECT c.* FROM model_calls c JOIN jobs j ON c.job_id=j.id WHERE substr(j.quota_day,1,7)=?", (month,))]
            members = [dict(row) for row in db.execute("SELECT id,username,display_name FROM users ORDER BY rowid")]
        def totals(selected):
            ids = {row["id"] for row in selected}
            records = [row for row in calls if row["job_id"] in ids]
            result = {"questions": len(selected)}
            for key in ("input_tokens", "output_tokens"):
                result[key] = sum(row[key] for row in records) if all(row[key] is not None for row in records) else None
            incoming, outgoing = result["input_tokens"], result["output_tokens"]
            in_price, out_price = settings["input_price_per_million_yuan"], settings["output_price_per_million_yuan"]
            # Prices describe the configured default model, never other models.
            priced = all(json.loads(job["config"])["model"] == model for job in selected)
            result["cost_yuan"] = float((Decimal(incoming) * Decimal(str(in_price)) + Decimal(outgoing) * Decimal(str(out_price))) / Decimal(1000000)) if priced and all(value is not None for value in (incoming, outgoing, in_price, out_price)) else None
            return result
        return {"month": month, "total": totals(jobs),
                "by_user": [{"user_id": user["id"], "username": user["username"], "display_name": user["display_name"], **totals([j for j in jobs if j["user_id"] == user["id"]])} for user in members],
                "by_day": [{"date": day, **totals([j for j in jobs if j["quota_day"] == day])} for day in sorted({j["quota_day"] for j in jobs})]}

    register_workflow(app, workflow, identity, admin)

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        if path == "api" or path.startswith("api/") or not config.static_dir:
            raise APIError(404, "未找到该接口。")
        root = Path(config.static_dir).resolve()
        file = (root / path).resolve()
        if not file.is_relative_to(root):
            raise APIError(404, "未找到该页面。")
        if file.is_file():
            return FileResponse(file)
        if path.startswith("assets/") or "." in Path(path).name:
            raise APIError(404, "未找到该文件。")
        if not (root / "index.html").is_file():
            raise APIError(503, "网页尚未构建，请联系管理员。")
        return FileResponse(root / "index.html", headers={"Cache-Control": "no-store"})

    return app
