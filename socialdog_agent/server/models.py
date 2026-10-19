"""Public API schemas; credentials and database records never become responses."""
from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Role = Literal["admin", "researcher"]
Permission = Literal["review_feedback", "review_ideas", "view_researchers"]
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


ReasoningEffort = Literal["none", "low", "high", "max"]


class ModelSelection(Input):
    model: Annotated[str, Field(max_length=80)] | None = None
    reasoning_effort: ReasoningEffort | None = None


class QuestionInput(ModelSelection):
    question: Text


class OwnAnswer(ModelSelection):
    own_answer: Text
    bank_version_id: str | None = None
    assignment_id: str | None = None


class NewUser(Input):
    username: Username
    display_name: Annotated[str, Field(min_length=1, max_length=60)]
    role: Role = "researcher"
    daily_limit: Limit | None = None
    evaluation_reserve: Limit | None = None
    permissions: list[Permission] = Field(default_factory=list, max_length=3)


class UserPatch(Input):
    role: Role | None = None
    daily_limit: Limit | None = None
    evaluation_reserve: Limit | None = None
    disabled: bool | None = Field(default=None, strict=True)
    permissions: list[Permission] | None = Field(default=None, max_length=3)

    @model_validator(mode="after")
    def non_null_fields(self):
        if not self.model_fields_set or any(getattr(self, k) is None for k in self.model_fields_set):
            raise ValueError("更新字段不能为空")
        return self


class IdeaField(Input):
    key: Literal["question", "data_used", "findings", "next_steps"]
    label: Annotated[str, Field(min_length=1, max_length=80)]
    hint: Annotated[str, Field(max_length=200)] = ""
    required: bool = True


def default_idea_fields():
    return [IdeaField(key=k, label=v) for k, v in zip(
        ("question", "data_used", "findings", "next_steps"),
        ("研究问题", "用到的数据", "初步发现", "下一步想怎么做"))]


class ResearchPolicy(Input):
    allow_post_cutoff_reference_after_submit: bool = False
    anonymous_ab_evaluation: bool = False
    invitation_criteria: Annotated[str, Field(max_length=4000)] = ""
    idea_fields: list[IdeaField] = Field(default_factory=default_idea_fields, min_length=4, max_length=4)

    @model_validator(mode="after")
    def valid_form(self):
        if {f.key for f in self.idea_fields} != {f.key for f in default_idea_fields()}:
            raise ValueError("研究想法表单需包含四个不同字段")
        if any(not f.label.strip() for f in self.idea_fields) or not next(f for f in self.idea_fields if f.key == "question").required:
            raise ValueError("研究问题须必填，字段名称不能为空")
        return self


class SiteSettings(ResearchPolicy):
    site_daily_limit: Annotated[int, Field(strict=True, ge=0, le=10000)] = 200
    min_orders_per_group: Annotated[int, Field(strict=True, ge=1, le=10000)] = 5
    cutoff_date: date
    input_price_per_million_yuan: Annotated[float, Field(ge=0, le=100000)] | None = None
    output_price_per_million_yuan: Annotated[float, Field(ge=0, le=100000)] | None = None
    max_concurrent_jobs: Annotated[int, Field(strict=True, ge=1, le=10)] = 3
    per_user_concurrent_jobs: Literal[1] = 1
    max_accounts: Annotated[int, Field(strict=True, ge=1, le=100)] = 10
    default_daily_limit: Limit = 30
    default_evaluation_reserve: Limit = 0

    @model_validator(mode="after")
    def valid_defaults(self):
        if self.default_evaluation_reserve > self.default_daily_limit:
            raise ValueError("测评预留不能超过每日上限")
        return self


class RuntimeSettings(Input):
    site_daily_limit: Annotated[int, Field(strict=True, ge=0, le=10000)]
    input_price_per_million_yuan: Annotated[float, Field(ge=0, le=100000)] | None
    output_price_per_million_yuan: Annotated[float, Field(ge=0, le=100000)] | None
    max_concurrent_jobs: Annotated[int, Field(strict=True, ge=1, le=10)]
    per_user_concurrent_jobs: Literal[1]
    max_accounts: Annotated[int, Field(strict=True, ge=1, le=100)]
    default_daily_limit: Limit
    default_evaluation_reserve: Limit

    @model_validator(mode="after")
    def valid_reserve(self):
        if self.default_evaluation_reserve > self.default_daily_limit:
            raise ValueError("测评预留不能超过每日上限")
        return self


class Quota(BaseModel):
    daily_limit: int
    used_today: int
    remaining_today: int
    site_remaining_today: int
    evaluation_reserve: int
    chat_remaining_today: int


class Me(BaseModel):
    id: str
    username: str
    display_name: str
    role: Role
    csrf_token: str
    quota: Quota
    running_job_id: str | None
    notice_version: int
    permissions: list[Permission]


class AdminUser(BaseModel):
    id: str
    username: str
    display_name: str
    role: Role
    daily_limit: int
    evaluation_reserve: int
    disabled: bool
    used_today: int
    last_active_at: str | None
    invite_pending: bool
    permissions: list[Permission]


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


class ModelOption(BaseModel):
    id: str
    label: str
    reasoning_efforts: list[ReasoningEffort]


class ModelCatalog(BaseModel):
    models: list[ModelOption]
    default_model: str
    default_reasoning_effort: ReasoningEffort


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
    reasoning_effort: str | None = None


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
    attempt_id: str | None = None
    assignment_id: str | None = None
    bank_version_id: str | None = None

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


class ReferenceWindow(Input):
    start_date: date
    end_date: date
    product: Annotated[str, Field(min_length=1, max_length=100)] | None = None

    @model_validator(mode="after")
    def ordered(self):
        if self.start_date > self.end_date:
            raise ValueError("核对窗口开始日期不能晚于结束日期")
        return self


class EvalQuestion(Input):
    id: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")]
    category: Literal["time_series", "product", "price_event", "quality", "unavailable", "out_of_sample"]
    title: Annotated[str, Field(min_length=1, max_length=120)]
    question: Text
    reference_hint: ReferenceHint | None = None
    post_cutoff_reference: ReferenceWindow | None = None

    @model_validator(mode="after")
    def valid_question(self):
        hint = self.reference_hint
        if not self.title.strip() or not self.question.strip():
            raise ValueError("题目标题和问题不能为空")
        if hint and hint.start_date and hint.end_date and hint.start_date > hint.end_date:
            raise ValueError("参考日期顺序错误")
        return self


class VersionedQuestion(EvalQuestion):
    bank_version_id: str


class RatingScores(Input):
    numbers: Literal["correct", "partial", "wrong", "not_applicable"]
    reasoning: Annotated[int, Field(strict=True, ge=1, le=5)]
    definitions: Annotated[int, Field(strict=True, ge=1, le=5)]
    limitations: Annotated[int, Field(strict=True, ge=1, le=5)]


Stage = Literal["retrieval", "definition", "calculation", "reasoning", "conclusion", "other"]
ReviewStatus = Literal["pending", "confirmed", "rejected", "discuss"]
IdeaStatus = Literal["pending", "feedback_given", "invited", "not_suitable"]


class IssueDraft(Input):
    stage: Stage
    description: Text
    correct_approach: Annotated[str, Field(max_length=4000)] = ""
    evidence_refs: list[Annotated[str, Field(max_length=160)]] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def meaningful_description(self):
        if not self.description.strip():
            raise ValueError("请描述问题")
        return self


class FeedbackInput(IssueDraft):
    source: Literal["chat", "evaluation"]
    turn_id: str
    attempt_id: str | None = None


class FeedbackIssue(IssueDraft):
    id: str
    user_id: str
    display_name: str
    source: Literal["chat", "evaluation"]
    turn_id: str
    attempt_id: str | None
    question: str
    model_answer: str
    evidence_snapshot: list[Evidence]
    review_status: ReviewStatus
    review_note: str
    reviewed_by: str | None
    reviewed_at: str | None
    created_at: str


class IssueReview(Input):
    review_status: ReviewStatus
    review_note: Annotated[str, Field(max_length=4000)] = ""


class IdeaInput(Input):
    question: Text
    data_used: Annotated[str, Field(max_length=4000)] = ""
    findings: Annotated[str, Field(max_length=4000)] = ""
    next_steps: Annotated[str, Field(max_length=4000)] = ""
    linked_conversations: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def meaningful_fields(self):
        if not self.question.strip():
            raise ValueError("请填写研究想法")
        return self


class ResearchIdea(IdeaInput):
    id: str
    user_id: str
    display_name: str
    status: IdeaStatus
    reviewer_feedback: str
    reviewed_by: str | None
    created_at: str
    updated_at: str
    form_snapshot: list[IdeaField]
    criteria_snapshot: str


class IdeaReview(Input):
    status: IdeaStatus
    reviewer_feedback: Annotated[str, Field(max_length=4000)] = ""


class AssignmentInput(Input):
    question_id: str
    bank_version_id: str
    user_id: str
    due_at: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")] | None = None

    @model_validator(mode="after")
    def valid_due_date(self):
        if self.due_at:
            date.fromisoformat(self.due_at)
        return self


class Assignment(BaseModel):
    id: str
    question_id: str
    bank_version_id: str
    user_id: str
    display_name: str
    assigned_by: str
    due_at: str | None
    created_at: str
    attempt_id: str | None
    completed: bool
    overdue: bool
    question: VersionedQuestion


class AssignmentProgress(BaseModel):
    user_id: str
    display_name: str
    assigned: int
    completed: int
    overdue: int


class BankVersion(BaseModel):
    id: str
    cutoff_T: str | None
    threshold_N: int | None
    dataset_version: str | None
    legacy: bool
    created_at: str
    question_count: int


class BankDraft(Input):
    revision: Annotated[int, Field(strict=True, ge=0)] = 0
    name: Annotated[str, Field(min_length=1, max_length=120)]
    cutoff_T: date
    threshold_N: Annotated[int, Field(strict=True, ge=1, le=10000)]
    questions: list[EvalQuestion] = Field(max_length=100)

    @model_validator(mode="after")
    def distinct_questions(self):
        if not self.name.strip() or len({q.id for q in self.questions}) != len(self.questions):
            raise ValueError("题库名称不能为空，题目编号不能重复")
        if any(not q.title.strip() or not q.question.strip() for q in self.questions):
            raise ValueError("题目标题和问题不能为空")
        return self


class BankDetail(BankVersion):
    name: str
    questions: list[EvalQuestion]
    active: bool


class BankWorkspace(BaseModel):
    draft: BankDraft
    active: BankDetail
    versions: list[BankDetail]
    can_change_cutoff: bool
    earliest_date: str
    latest_date: str


class PublishBank(Input):
    revision: Annotated[int, Field(strict=True, ge=0)]


class ReviewMember(BaseModel):
    id: str
    display_name: str


class RatingInput(Input):
    attempt_id: str
    scores: RatingScores | None = None
    anonymous_scores: dict[Literal["A", "B"], RatingScores] | None = None
    note: Annotated[str, Field(max_length=4000)] = ""
    issues: list[IssueDraft] = Field(default_factory=list, max_length=20)


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
    anonymous_scores: dict[str, RatingScores] | None = None


class EvalAttempt(BaseModel):
    id: str
    question_id: str
    own_answer: str
    job_id: str
    turn: Turn
    rated: bool
    bank_version_id: str | None
    question_snapshot: dict[str, Any] | None
    anonymous_ab: bool
    answer_panels: list[dict[str, str]]


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


class ResearcherSummary(BaseModel):
    user: AdminUser
    quota: Quota
    question_count: int
    recent_questions: list[dict[str, str]]
    ratings: list[Rating]
    feedback_count: int
    confirmed_count: int
    reviewed_count: int
    confirmed_ratio: float | None
    ideas: list[ResearchIdea]
    assignments: list[Assignment]
