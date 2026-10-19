"""Public API schemas; credentials and database records never become responses."""
from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Role = Literal["admin", "researcher"]
Group = Literal["all", "day", "week", "month", "hour", "weekday", "category", "product", "delivery", "payment", "status", "type"]
Limit = Annotated[int, Field(strict=True, ge=0, le=500)]
Username = Annotated[str, Field(pattern=r"^[a-z0-9_]{2,32}$")]
Text = Annotated[str, Field(min_length=1, max_length=4000)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Login(Input):
    username: Annotated[str, Field(min_length=1, max_length=32)]
    password: Annotated[str, Field(min_length=1, max_length=256)]


class AcceptInvite(Input):
    token: Annotated[str, Field(min_length=20, max_length=128)]
    password: Annotated[str, Field(min_length=10, max_length=256)]


class QuestionInput(Input):
    question: Text


class OwnAnswer(Input):
    own_answer: Text


class NewUser(Input):
    username: Username
    display_name: Annotated[str, Field(min_length=1, max_length=60)]
    role: Role = "researcher"
    daily_limit: Limit = 30


class UserPatch(Input):
    role: Role | None = None
    daily_limit: Limit | None = None
    disabled: bool | None = Field(default=None, strict=True)

    @model_validator(mode="after")
    def non_null_fields(self):
        if not self.model_fields_set or any(getattr(self, k) is None for k in self.model_fields_set):
            raise ValueError("更新字段不能为空")
        return self


class SiteSettings(Input):
    site_daily_limit: Annotated[int, Field(strict=True, ge=0, le=10000)] = 200
    min_orders_per_group: Annotated[int, Field(strict=True, ge=1, le=10000)] = 5
    cutoff_date: date
    input_price_per_million_yuan: Annotated[float, Field(ge=0, le=100000)] | None = None
    output_price_per_million_yuan: Annotated[float, Field(ge=0, le=100000)] | None = None
    max_concurrent_jobs: Annotated[int, Field(strict=True, ge=1, le=10)] = 3
    per_user_concurrent_jobs: Literal[1] = 1
    allow_post_cutoff_reference_after_submit: Literal[False] = False


class Quota(BaseModel):
    daily_limit: int
    used_today: int
    remaining_today: int
    site_remaining_today: int


class Me(BaseModel):
    id: str
    username: str
    display_name: str
    role: Role
    csrf_token: str
    quota: Quota
    running_job_id: str | None


class AdminUser(BaseModel):
    id: str
    username: str
    display_name: str
    role: Role
    daily_limit: int
    disabled: bool
    used_today: int
    last_active_at: str | None
    invite_pending: bool


class InviteInfo(BaseModel):
    username: str
    display_name: str
    expires_at: str


class InviteLink(BaseModel):
    invite_url: str
    expires_at: str


class CreatedUser(InviteLink):
    user: AdminUser


class DataStatus(BaseModel):
    dataset_version: str
    cutoff_date: str
    agent_observation_start: str
    agent_observation_end: str
    min_orders_per_group: int
    model: str


class Session(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str


class Usage(BaseModel):
    # Missing provider accounting is unknown, never a fabricated zero.
    input_tokens: int | None
    output_tokens: int | None


class Grounding(BaseModel):
    ungrounded_numbers: list[str]
    repair_attempted: bool


class Evidence(BaseModel):
    tool: str
    call_id: str
    arguments: str
    result: dict[str, Any]


class AgentResult(BaseModel):
    answer: str
    evidence: list[Evidence]
    snapshot_versions: list[str]
    grounding: Grounding
    model: str
    request_id: str
    usage: Usage


class Turn(BaseModel):
    id: str
    question: str
    status: Literal["running", "ok", "error"]
    created_at: str
    job_id: str | None
    result: AgentResult | None
    error: str | None


class SessionDetail(Session):
    turns: list[Turn]


class ProgressEvent(BaseModel):
    phase: Literal["queued", "source", "model", "tool", "tool_complete", "verify", "complete"]
    text: str
    tool: str | None = None
    at: str


class Job(BaseModel):
    id: str
    status: Literal["queued", "running", "ok", "error"]
    session_id: str
    turn_id: str
    queue_position: int | None
    events: list[ProgressEvent]
    error: str | None


class Submitted(BaseModel):
    turn: Turn
    job: Job


class ReferenceHint(Input):
    scope: Literal["baseline", "all"] | None = None
    by: Group | None = None
    start_date: date | None = None
    end_date: date | None = None
    product: Annotated[str, Field(min_length=1, max_length=100)] | None = None


class ReferenceQuery(Input):
    scope: Literal["baseline", "all"] = "baseline"
    by: Group = "month"
    start_date: date | None = None
    end_date: date | None = None
    product: Annotated[str, Field(min_length=1, max_length=100)] | None = None

    @model_validator(mode="after")
    def dates_in_order(self):
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("开始日期不能晚于结束日期")
        return self


class ReferenceRow(BaseModel):
    group: str
    order_count: int
    line_count: int
    recorded_quantity: int
    recorded_receipt_yuan: float
    recorded_deal_yuan: float
    wallet_yuan: float
    coupon_yuan: float
    after_cutoff: bool


class ReferenceResult(BaseModel):
    query: ReferenceQuery
    rows: list[ReferenceRow]
    group_order_counts_additive: bool
    notes: list[str]


class ReferenceMeta(BaseModel):
    dataset_version: str
    cutoff_date: str
    observation_start: str
    observation_end: str
    groups: list[dict[str, str]]
    products: list[str]
    can_view_after_cutoff: bool


class EvalQuestion(Input):
    id: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")]
    category: Literal["time_series", "product", "price_event", "quality", "unavailable", "out_of_sample"]
    title: Annotated[str, Field(min_length=1, max_length=120)]
    question: Text
    reference_hint: ReferenceHint | None = None


class RatingScores(Input):
    numbers: Literal["correct", "partial", "wrong", "not_applicable"]
    reasoning: Annotated[int, Field(strict=True, ge=1, le=5)]
    definitions: Annotated[int, Field(strict=True, ge=1, le=5)]
    limitations: Annotated[int, Field(strict=True, ge=1, le=5)]


class RatingInput(Input):
    attempt_id: str
    scores: RatingScores
    note: Annotated[str, Field(max_length=4000)] = ""


class Rating(BaseModel):
    id: str
    attempt_id: str
    question_id: str
    question_title: str
    question: str
    own_answer: str
    agent_answer: str
    scores: RatingScores
    note: str
    created_at: str
    username: str


class EvalAttempt(BaseModel):
    id: str
    question_id: str
    own_answer: str
    job_id: str
    turn: Turn
    rated: bool


class UsageLine(Usage):
    questions: int
    cost_yuan: float | None


class UserUsage(UsageLine):
    user_id: str
    username: str
    display_name: str


class DayUsage(UsageLine):
    date: str


class UsageReport(BaseModel):
    month: str
    total: UsageLine
    by_user: list[UserUsage]
    by_day: list[DayUsage]
