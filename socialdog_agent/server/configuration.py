"""Administrator-managed drafts, immutable publications and research policies."""
import json

from fastapi import Depends

from . import models as m
from .store import APIError, encoded


def bank_detail(row, active):
    questions = json.loads(row["questions"])
    return {**dict(row), "questions": questions, "question_count": len(questions),
            "legacy": bool(row["legacy"]), "active": row["id"] == active}


def publish(workflow, db, draft, prepared, settings):
    version = workflow.save_bank(db, {"name": draft.name.strip(), "cutoff_T": draft.cutoff_T.isoformat(),
        "threshold_N": draft.threshold_N, "dataset_version": prepared.description["dataset_version"],
        "questions": [q.model_dump(mode="json") for q in draft.questions], "legacy": False})
    db.execute("UPDATE question_bank_active SET version_id=? WHERE id=1", (version,))
    settings.update(cutoff_date=draft.cutoff_T.isoformat(), min_orders_per_group=draft.threshold_N)
    db.execute("UPDATE settings SET value=? WHERE id=1", (encoded(settings),))
    return version


def register_configuration(app, workflow, identity, admin, capability):
    store, source = workflow.store, workflow.source

    def workspace(db):
        active_id = workflow.active_bank(db)
        active = bank_detail(db.execute("SELECT * FROM question_bank_version WHERE id=?", (active_id,)).fetchone(), active_id)
        row = db.execute("SELECT * FROM question_bank_draft WHERE id=1").fetchone()
        draft = {"revision": row["revision"], **json.loads(row["value"])} if row else {
            "revision": 0, "name": active["name"] or "研究题库", "cutoff_T": active["cutoff_T"],
            "threshold_N": active["threshold_N"], "questions": active["questions"]}
        versions = [bank_detail(r, active_id) for r in db.execute("SELECT * FROM question_bank_version WHERE legacy=0 ORDER BY rowid DESC")]
        return {"draft": draft, "active": active, "versions": versions, **source.bounds()}

    @app.get("/api/admin/question-bank", response_model=m.BankWorkspace)
    def bank_workspace(auth=Depends(admin)):
        with store.connection() as db:
            return workspace(db)

    @app.put("/api/admin/question-bank/draft", response_model=m.BankWorkspace)
    def save_draft(body: m.BankDraft, auth=Depends(admin)):
        with store.connection(write=True) as db:
            row = db.execute("SELECT revision FROM question_bank_draft WHERE id=1").fetchone()
            if body.revision != (row[0] if row else 0):
                raise APIError(409, "草稿已被另一位管理员修改，请重新加载后编辑。")
            db.execute("INSERT INTO question_bank_draft VALUES (1,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,value=excluded.value",
                       (body.revision + 1, encoded(body.model_dump(mode="json", exclude={"revision"}))))
            return workspace(db)

    @app.post("/api/admin/question-bank/publish", response_model=m.BankWorkspace)
    def publish_draft(body: m.PublishBank, auth=Depends(admin)):
        with store.connection() as db:
            draft = m.BankDraft.model_validate(workspace(db)["draft"])
        if body.revision != draft.revision:
            raise APIError(409, "草稿已更新，请重新加载后发布。")
        if not draft.questions:
            raise APIError(400, "请至少添加一道题目后再发布。")
        bounds = source.bounds()
        for question in draft.questions:
            window = question.post_cutoff_reference
            if window and (window.start_date <= draft.cutoff_T or window.end_date.isoformat() > bounds["latest_date"]):
                raise APIError(400, "提交后核对窗口必须在 T 之后，且不能超出已导入数据范围。")
        prepared = source.prepare(draft.cutoff_T.isoformat())
        with store.connection(write=True) as db:
            current = db.execute("SELECT revision FROM question_bank_draft WHERE id=1").fetchone()
            if (current[0] if current else 0) != body.revision:
                raise APIError(409, "草稿已更新，请重新加载后发布。")
            publish(workflow, db, draft, prepared, store.settings(db))
            return workspace(db)

    @app.get("/api/research-policy", response_model=m.ResearchPolicy)
    def research_policy(auth=Depends(identity)):
        return {k: store.settings()[k] for k in m.ResearchPolicy.model_fields}

    @app.patch("/api/admin/research-policy", response_model=m.SiteSettings)
    def save_policy(body: m.ResearchPolicy, auth=Depends(admin)):
        with store.connection(write=True) as db:
            settings = store.settings(db)
            settings.update(body.model_dump(mode="json"))
            db.execute("UPDATE settings SET value=? WHERE id=1", (encoded(settings),))
            return settings

    @app.patch("/api/admin/settings", response_model=m.SiteSettings)
    def save_runtime(body: m.RuntimeSettings, auth=Depends(admin)):
        with store.connection(write=True) as db:
            settings = store.settings(db)
            if body.max_accounts < db.execute("SELECT count(*) FROM users").fetchone()[0]:
                raise APIError(400, "账号上限不能低于现有账号数量。")
            settings.update(body.model_dump(mode="json"))
            db.execute("UPDATE settings SET value=? WHERE id=1", (encoded(settings),))
            return settings

    @app.get("/api/review/members", response_model=list[m.ReviewMember])
    def review_members(auth=Depends(identity)):
        if auth[0]["role"] != "admin" and not json.loads(auth[0]["permissions"]):
            raise APIError(403, "你没有审核权限。")
        with store.connection() as db:
            return [dict(r) for r in db.execute("SELECT id,display_name FROM users ORDER BY rowid")]
