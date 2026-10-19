"""Research submissions, immutable question versions and administrator review."""
from __future__ import annotations

import json
from typing import Literal
from fastapi import Depends, Response

from . import models as m
from .store import APIError, digest, encoded, now, today, uid

NOTICE_VERSION = 1


class Workflow:
    def __init__(self, store, source, questions):
        self.store, self.source, self.questions = store, source, questions
        with store.connection(write=True) as db:
            self.active_bank(db)
            # Old attempts retain only facts recorded at submission time. Unknown
            # historical cutoffs stay NULL instead of inheriting today's settings.
            for row in db.execute("SELECT a.*,j.config,t.question FROM attempts a JOIN jobs j ON j.id=a.job_id JOIN turns t ON t.id=a.turn_id WHERE bank_version_id IS NULL").fetchall():
                config = json.loads(row["config"])
                dataset = config.get("dataset_version")
                suffix = dataset.rsplit("_asof", 1)[-1] if dataset and "_asof" in dataset else None
                cutoff = suffix if suffix and len(suffix) == 10 else None
                question = {"id": row["question_id"], "title": row["question_title"], "question": row["question"]}
                content = {"cutoff_T": cutoff, "threshold_N": config.get("min_orders_per_group"), "dataset_version": dataset, "questions": [question], "legacy": True}
                version = self.save_bank(db, content)
                db.execute("UPDATE attempts SET bank_version_id=? WHERE id=?", (version, row["id"]))

    def save_bank(self, db, content):
        version = digest(json.dumps(content, sort_keys=True, ensure_ascii=False))[:32]
        db.execute("INSERT OR IGNORE INTO question_bank_version VALUES (?,?,?,?,?,?,?)", (version, content["cutoff_T"], content["threshold_N"], content["dataset_version"], encoded(content["questions"]), int(content["legacy"]), now()))
        return version

    def active_bank(self, db):
        settings = self.store.settings(db)
        return self.save_bank(db, {"cutoff_T": self.source.cutoff, "threshold_N": settings["min_orders_per_group"], "dataset_version": self.source.description["dataset_version"], "questions": list(self.questions.values()), "legacy": False})

    def question(self, db, question_id, version=None):
        version = version or self.active_bank(db)
        bank = db.execute("SELECT * FROM question_bank_version WHERE id=? AND legacy=0", (version,)).fetchone()
        question = next((q for q in json.loads(bank["questions"]) if q["id"] == question_id), None) if bank else None
        if not question:
            raise APIError(404, "未找到该版本的题目。")
        return {**question, "bank_version_id": version}

    def add_issue(self, db, user_id, source, turn_id, attempt_id, draft):
        turn = db.execute("SELECT * FROM turns WHERE id=? AND user_id=?", (turn_id, user_id)).fetchone()
        if not turn:
            raise APIError(404, "未找到该回答。")
        actual_attempt = db.execute("SELECT id FROM attempts WHERE turn_id=? AND user_id=?", (turn_id, user_id)).fetchone()
        if source == "evaluation":
            if not actual_attempt or actual_attempt[0] != attempt_id:
                raise APIError(404, "未找到该测评回答。")
        elif actual_attempt or attempt_id or not turn["session_id"]:
            raise APIError(400, "反馈来源与回答不匹配。")
        if turn["status"] != "ok" or not turn["result"]:
            raise APIError(409, "回答完成后才能提交问题反馈。")
        result = json.loads(turn["result"])
        evidence = result["evidence"]
        if set(draft.evidence_refs) - {e["call_id"] for e in evidence}:
            raise APIError(400, "关联依据不属于这条回答。")
        issue_id = uid()
        db.execute("INSERT INTO feedback_issue(id,user_id,source,turn_id,attempt_id,question,model_answer,evidence_snapshot,stage,description,correct_approach,evidence_refs,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (issue_id, user_id, source, turn_id, attempt_id, turn["question"], result["answer"], encoded(evidence), draft.stage, draft.description.strip(), draft.correct_approach.strip(), encoded(list(dict.fromkeys(draft.evidence_refs))), now()))
        return issue_id

    def issues(self, db, where="1=1", params=()):
        rows = db.execute(f"SELECT f.*,u.display_name FROM feedback_issue f JOIN users u ON u.id=f.user_id WHERE {where} ORDER BY f.created_at DESC,f.rowid DESC", params)
        return [{**dict(r), "evidence_snapshot": json.loads(r["evidence_snapshot"]), "evidence_refs": json.loads(r["evidence_refs"])} for r in rows]

    def ideas(self, db, where="1=1", params=()):
        return [{**dict(r), "linked_conversations": json.loads(r["linked_conversations"])} for r in db.execute(f"SELECT i.*,u.display_name FROM research_idea i JOIN users u ON u.id=i.user_id WHERE {where} ORDER BY i.created_at DESC,i.rowid DESC", params)]

    def assignments(self, db, user_id=None):
        rows = db.execute("SELECT a.*,u.display_name FROM assignment a JOIN users u ON u.id=a.user_id" + (" WHERE a.user_id=?" if user_id else "") + " ORDER BY a.created_at DESC,a.rowid DESC", (user_id,) if user_id else ())
        return [{**dict(r), "completed": r["attempt_id"] is not None, "overdue": bool(not r["attempt_id"] and r["due_at"] and r["due_at"] < today()), "question": self.question(db, r["question_id"], r["bank_version_id"])} for r in rows]


def register_workflow(app, workflow, identity, admin):
    store = workflow.store

    @app.post("/api/notice/accept", response_model=m.Me)
    def accept_notice(auth=Depends(identity)):
        with store.connection(write=True) as db:
            db.execute("UPDATE users SET notice_version=?,notice_accepted_at=? WHERE id=?", (NOTICE_VERSION, now(), auth[0]["id"]))
            return store.me(db, store.user(db, auth[0]["id"]), auth[1])

    @app.post("/api/feedback", response_model=m.FeedbackIssue, status_code=201)
    def feedback(body: m.FeedbackInput, auth=Depends(identity)):
        with store.connection(write=True) as db:
            issue_id = workflow.add_issue(db, auth[0]["id"], body.source, body.turn_id, body.attempt_id, body)
            return workflow.issues(db, "f.id=?", (issue_id,))[0]

    @app.get("/api/feedback/mine", response_model=list[m.FeedbackIssue])
    def my_feedback(auth=Depends(identity)):
        with store.connection() as db:
            return workflow.issues(db, "f.user_id=?", (auth[0]["id"],))

    @app.get("/api/admin/feedback", response_model=list[m.FeedbackIssue])
    def feedback_queue(source: Literal["chat", "evaluation"] | None = None, stage: m.Stage | None = None, user_id: str | None = None, review_status: m.ReviewStatus | None = None, auth=Depends(admin)):
        conditions, params = [], []
        for name, value in (("source", source), ("stage", stage), ("user_id", user_id), ("review_status", review_status)):
            if value is not None:
                conditions.append("f." + name + "=?")
                params.append(value)
        with store.connection() as db:
            return workflow.issues(db, " AND ".join(conditions) or "1=1", params)

    @app.patch("/api/admin/feedback/{issue_id}", response_model=m.FeedbackIssue)
    def review_issue(issue_id: str, body: m.IssueReview, auth=Depends(admin)):
        with store.connection(write=True) as db:
            if not db.execute("SELECT 1 FROM feedback_issue WHERE id=?", (issue_id,)).fetchone():
                raise APIError(404, "未找到该问题反馈。")
            db.execute("UPDATE feedback_issue SET review_status=?,review_note=?,reviewed_by=?,reviewed_at=? WHERE id=?", (body.review_status, body.review_note.strip(), auth[0]["id"], now(), issue_id))
            return workflow.issues(db, "f.id=?", (issue_id,))[0]

    @app.get("/api/admin/improvements/export.json", response_model=list[m.FeedbackIssue])
    def export_improvements(auth=Depends(admin)):
        with store.connection() as db:
            rows = workflow.issues(db, "f.review_status='confirmed'")
        return Response(encoded(rows), media_type="application/json", headers={"Content-Disposition": 'attachment; filename="model-improvements.json"'})

    @app.post("/api/ideas", response_model=m.ResearchIdea, status_code=201)
    def create_idea(body: m.IdeaInput, auth=Depends(identity)):
        idea_id, stamp = uid(), now()
        with store.connection(write=True) as db:
            for session_id in body.linked_conversations:
                store.session(db, session_id, auth[0]["id"])
            db.execute("INSERT INTO research_idea(id,user_id,question,data_used,findings,next_steps,linked_conversations,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)", (idea_id, auth[0]["id"], body.question.strip(), body.data_used.strip(), body.findings.strip(), body.next_steps.strip(), encoded(list(dict.fromkeys(body.linked_conversations))), stamp, stamp))
            return workflow.ideas(db, "i.id=?", (idea_id,))[0]

    @app.get("/api/ideas/mine", response_model=list[m.ResearchIdea])
    def my_ideas(auth=Depends(identity)):
        with store.connection() as db:
            return workflow.ideas(db, "i.user_id=?", (auth[0]["id"],))

    @app.get("/api/admin/ideas", response_model=list[m.ResearchIdea])
    def all_ideas(auth=Depends(admin)):
        with store.connection() as db:
            return workflow.ideas(db)

    @app.patch("/api/admin/ideas/{idea_id}", response_model=m.ResearchIdea)
    def review_idea(idea_id: str, body: m.IdeaReview, auth=Depends(admin)):
        with store.connection(write=True) as db:
            if not db.execute("SELECT 1 FROM research_idea WHERE id=?", (idea_id,)).fetchone():
                raise APIError(404, "未找到该研究想法。")
            db.execute("UPDATE research_idea SET status=?,reviewer_feedback=?,reviewed_by=?,updated_at=? WHERE id=?", (body.status, body.reviewer_feedback.strip(), auth[0]["id"], now(), idea_id))
            return workflow.ideas(db, "i.id=?", (idea_id,))[0]

    @app.get("/api/assignments/mine", response_model=list[m.Assignment])
    def my_assignments(auth=Depends(identity)):
        with store.connection() as db:
            return workflow.assignments(db, auth[0]["id"])

    @app.get("/api/admin/assignments", response_model=list[m.Assignment])
    def all_assignments(auth=Depends(admin)):
        with store.connection() as db:
            return workflow.assignments(db)

    @app.post("/api/admin/assignments", response_model=m.Assignment, status_code=201)
    def assign(body: m.AssignmentInput, auth=Depends(admin)):
        with store.connection(write=True) as db:
            user = db.execute("SELECT * FROM users WHERE id=?", (body.user_id,)).fetchone()
            if not user:
                raise APIError(404, "未找到该成员。")
            if user["role"] != "researcher" or user["disabled"]:
                raise APIError(400, "请选择已启用的研究员。")
            workflow.question(db, body.question_id, body.bank_version_id)
            if db.execute("SELECT 1 FROM assignment WHERE question_id=? AND bank_version_id=? AND user_id=?", (body.question_id, body.bank_version_id, body.user_id)).fetchone():
                raise APIError(409, "这道题已经分配给该研究员。")
            assignment_id = uid()
            db.execute("INSERT INTO assignment(id,question_id,bank_version_id,user_id,assigned_by,due_at,created_at) VALUES (?,?,?,?,?,?,?)", (assignment_id, body.question_id, body.bank_version_id, body.user_id, auth[0]["id"], body.due_at, now()))
            return next(a for a in workflow.assignments(db, body.user_id) if a["id"] == assignment_id)

    @app.get("/api/admin/progress", response_model=list[m.AssignmentProgress])
    def progress(auth=Depends(admin)):
        with store.connection() as db:
            assignments = workflow.assignments(db)
            return [{"user_id": u["id"], "display_name": u["display_name"], "assigned": sum(a["user_id"] == u["id"] for a in assignments), "completed": sum(a["user_id"] == u["id"] and a["completed"] for a in assignments), "overdue": sum(a["user_id"] == u["id"] and a["overdue"] for a in assignments)} for u in db.execute("SELECT id,display_name FROM users WHERE role='researcher'")]

    @app.get("/api/admin/question-bank/versions", response_model=list[m.BankVersion])
    def versions(auth=Depends(admin)):
        with store.connection() as db:
            return [{**dict(r), "legacy": bool(r["legacy"]), "question_count": len(json.loads(r["questions"]))} for r in db.execute("SELECT * FROM question_bank_version ORDER BY created_at DESC,rowid DESC")]

    @app.get("/api/eval/attempts", response_model=list[m.EvalAttempt])
    def my_attempts(auth=Depends(identity)):
        with store.connection() as db:
            return [store.attempt(db, r[0], auth[0]["id"]) for r in db.execute("SELECT id FROM attempts WHERE user_id=? ORDER BY rowid DESC", (auth[0]["id"],))]

    @app.get("/api/admin/researchers/{user_id}", response_model=m.ResearcherSummary)
    def researcher_summary(user_id: str, auth=Depends(admin)):
        with store.connection() as db:
            user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not user:
                raise APIError(404, "未找到该成员。")
            issues = workflow.issues(db, "f.user_id=?", (user_id,))
            reviewed = sum(i["review_status"] in ("confirmed", "rejected") for i in issues)
            confirmed = sum(i["review_status"] == "confirmed" for i in issues)
            recent = [dict(r) for r in db.execute("SELECT id,question,session_id,created_at FROM turns WHERE user_id=? AND session_id IS NOT NULL ORDER BY rowid DESC LIMIT 20", (user_id,))]
            count = db.execute("SELECT count(*) FROM turns WHERE user_id=? AND session_id IS NOT NULL", (user_id,)).fetchone()[0]
            return {"user": store.public_user(db, user), "quota": store.quota(db, user), "question_count": count, "recent_questions": recent, "ratings": [json.loads(r[0]) for r in db.execute("SELECT value FROM ratings WHERE user_id=? ORDER BY created_at DESC", (user_id,))], "feedback_count": len(issues), "confirmed_count": confirmed, "reviewed_count": reviewed, "confirmed_ratio": confirmed / reviewed if reviewed else None, "ideas": workflow.ideas(db, "i.user_id=?", (user_id,)), "assignments": workflow.assignments(db, user_id)}

    @app.get("/api/admin/researchers/{user_id}/sessions/{session_id}", response_model=m.SessionDetail)
    def inspect_conversation(user_id: str, session_id: str, auth=Depends(admin)):
        with store.connection() as db:
            owner = db.execute("SELECT notice_version FROM users WHERE id=?", (user_id,)).fetchone()
            if not owner or owner[0] < NOTICE_VERSION:
                raise APIError(403, "该成员尚未确认使用须知，暂不能查看其对话。")
            session = store.session(db, session_id, user_id)
            return {**session, "turns": [store.turn(db, r[0]) for r in db.execute("SELECT id FROM turns WHERE session_id=? ORDER BY rowid", (session_id,))]}
