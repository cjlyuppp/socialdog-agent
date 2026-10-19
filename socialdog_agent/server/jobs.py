"""One dispatcher, database-backed queue, and per-provider-call accounting."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import logging
from pathlib import Path
import threading

from socialdog_agent import ResearchAgent
from socialdog_agent.llm import LLMError, ResponsesLLM

from .models import AgentResult
from .store import APIError

LOG = logging.getLogger(__name__)
PHASES = {"source": "正在读取数据", "model": "正在分析", "tool": "正在查询数据",
          "tool_complete": "数据已就绪", "verify": "正在核对数字", "complete": "分析完成"}


class MeteredLLM(ResponsesLLM):
    def __init__(self, llm, store, job_id, stopping):
        self.llm, self.store, self.job_id, self.stopping = llm, store, job_id, stopping

    def create_response(self, **kwargs):
        if self.stopping.is_set():
            raise LLMError("服务正在停止，请稍后重新提问。")
        call_id = self.store.call_start(self.job_id)
        try:
            response = self.llm.create_response(**kwargs)
        except Exception:
            self.store.call_finish(call_id)
            raise
        self.store.call_finish(call_id, response)
        return response


class JobRunner:
    def __init__(self, store, source, state: Path, llm_factory):
        self.store, self.source, self.state, self.llm_factory = store, source, state, llm_factory
        self.stopping, self.wake = threading.Event(), threading.Event()
        self.pool = ThreadPoolExecutor(max_workers=10, thread_name_prefix="socialdog-job")
        self.thread = threading.Thread(target=self.dispatch, name="socialdog-dispatch", daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stopping.set()
        self.wake.set()
        self.thread.join()
        self.pool.shutdown(wait=True, cancel_futures=True)

    def dispatch(self):
        while not self.stopping.is_set():
            self.wake.clear()
            try:
                claimed = []
                with self.store.connection(write=True) as db:
                    capacity = self.store.settings(db)["max_concurrent_jobs"] - db.execute("SELECT count(*) FROM jobs WHERE status='running'").fetchone()[0]
                    queued = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY rowid LIMIT ?", (max(0, capacity),)).fetchall()
                    for job in queued:
                        db.execute("UPDATE jobs SET status='running' WHERE id=?", (job["id"],))
                        claimed.append(dict(job))
                for job in claimed:
                    self.pool.submit(self.run, job)
            except Exception:
                LOG.exception("Task dispatch failed")
            self.wake.wait(0.5)

    def run(self, job):
        try:
            config = json.loads(job["config"])
            with self.store.connection() as db:
                self.store.user(db, job["user_id"])
                turn = self.store.turn(db, job["turn_id"])
                previous = db.execute("SELECT question,result FROM turns WHERE session_id=? AND status='ok' AND rowid < (SELECT rowid FROM turns WHERE id=?) ORDER BY rowid DESC LIMIT 6",
                                      (job["session_id"], job["turn_id"])).fetchall() if job["session_id"] else []
            history = []
            for row in reversed(previous):
                result = json.loads(row["result"])
                history.append({"question": row["question"], "answer": result["answer"], "snapshot_versions": result["snapshot_versions"],
                                "query_scopes": [{"tool": item["tool"], **json.loads(item["arguments"])} for item in result["evidence"]]})
            state = self.state / job["user_id"]
            # Neither the reference object nor a participant's own answer enters this factory.
            agent = ResearchAgent(MeteredLLM(self.llm_factory(config), self.store, job["id"], self.stopping),
                                  state=state, tool_factory=lambda: self.source.tools(state, config))

            def progress(event):
                if self.stopping.is_set():
                    raise LLMError("服务正在停止，请稍后重新提问。")
                phase = event["phase"]
                if phase in PHASES:
                    self.store.add_event(job["id"], {"phase": phase, "text": PHASES[phase], **({"tool": event["tool"]} if event.get("tool") else {})})

            result = agent.ask(turn["question"], history=history, on_event=progress)
            result["usage"] = self.store.job_usage(job["id"])
            result["reasoning_effort"] = config.get("reasoning_effort")
            public = AgentResult.model_validate(result).model_dump()
            self.store.finish(job["id"], result=public)
        except (LLMError, APIError) as error:
            message = error.message if isinstance(error, APIError) else str(error)
            self.store.finish(job["id"], error=message)
        except Exception:
            LOG.exception("Research task failed")
            self.store.finish(job["id"], error="本次分析未完成，请稍后重试或联系管理员。")
        finally:
            self.wake.set()
