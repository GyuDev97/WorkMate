from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class TaskStatus(str, Enum):
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"
    CANCELLED = "CANCELLED"


class Priority(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ToolRisk(str, Enum):
    READ = "READ"
    WRITE_LOCAL = "WRITE_LOCAL"
    PROPOSED_LOCAL_WRITE = "PROPOSED_LOCAL_WRITE"
    PROPOSED_EXTERNAL_WRITE = "PROPOSED_EXTERNAL_WRITE"


class ApprovalStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    PENDING_APPROVAL = "pending_approval"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"
    NEEDS_CHECK = "needs_check"  # the outcome of an external call is unknown (e.g. a send timed out); never retried automatically
    EXPIRED = "expired"  # a stored card that can no longer be approved (the backend restarted)


class Event(BaseModel):
    id: str
    title: str
    starts_at: datetime
    ends_at: datetime | None = None
    description: str = ""
    all_day: bool = False
    location: str = ""
    calendar_id: str = "primary"
    source: str = "mock"
    is_recurring: bool = False
    version: str | None = None  # provider change marker (Google `updated`), used to detect edits between proposal and approval


class CalendarEventPatch(BaseModel):
    """Partial event change. Only fields that are set are sent, so untouched data (attendees, reminders) survives."""
    title: str | None = Field(default=None, min_length=1, max_length=300)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    all_day: bool | None = None
    description: str | None = None
    location: str | None = None


class CalendarState(BaseModel):
    state: Literal["mock", "connected", "not_connected", "error"] = "mock"
    detail: str = ""


class MailState(BaseModel):
    provider: Literal["none", "mock", "bizmeka"] = "none"
    state: Literal["mock", "connected", "not_connected", "session_expired", "error"] = "not_connected"
    detail: str = ""
    unread_count: int | None = None


class CalendarEventDraft(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    starts_at: datetime
    ends_at: datetime | None = None
    description: str = ""
    all_day: bool = False
    location: str = ""
    calendar_id: str = "primary"


class CalendarConnectionStatus(BaseModel):
    provider: Literal["mock", "google"]
    state: Literal["connected", "mock", "offline"]
    detail: str
    calendar_id: str = "primary"


class MailSummary(BaseModel):
    id: str
    sender: str
    subject: str
    received_at: datetime
    is_unread: bool = True
    needs_reply: bool = False
    preview: str


def check_period(start_at: datetime | None, due_at: datetime | None) -> None:
    """Both dates are optional; when both are set the start must not be after the due date."""
    if start_at is None or due_at is None:
        return
    def aware(value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if aware(start_at) > aware(due_at):
        raise ValueError("시작일은 종료일보다 늦을 수 없습니다.")


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    description: str = ""
    priority: Priority = Priority.MEDIUM
    start_at: datetime | None = None
    due_at: datetime | None = None
    source: str = "manual"

    @model_validator(mode="after")
    def _period(self) -> "TaskCreate":
        check_period(self.start_at, self.due_at)
        return self


class Task(BaseModel):
    id: str
    title: str
    description: str = ""
    priority: Priority
    start_at: datetime | None = None
    due_at: datetime | None = None
    status: TaskStatus
    source: str
    created_at: datetime
    updated_at: datetime


class TaskUpdate(BaseModel):
    """Partial update: only fields present in the request body are changed (start_at and due_at may be null to clear them)."""
    title: str | None = Field(default=None, min_length=1, max_length=180)
    description: str | None = Field(default=None, max_length=10000)
    priority: Priority | None = None
    start_at: datetime | None = None
    due_at: datetime | None = None
    status: TaskStatus | None = None

    @model_validator(mode="after")
    def _period(self) -> "TaskUpdate":
        check_period(self.start_at, self.due_at)
        return self


TaskView = Literal["all", "active", "done", "overdue", "due_today", "high_priority"]


class TaskSummary(BaseModel):
    total: int = 0
    active: int = 0
    done: int = 0
    overdue: int = 0
    due_today: int = 0
    high_priority: int = 0


class MemoCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(default="", max_length=50000)
    task_id: str | None = None


class MemoUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    content: str | None = Field(default=None, max_length=50000)
    task_id: str | None = None


class Memo(BaseModel):
    id: str
    title: str
    content: str = ""
    task_id: str | None = None
    task_title: str | None = None
    created_at: datetime
    updated_at: datetime


class ProviderStatus(BaseModel):
    name: str
    state: Literal["connected", "mock", "offline"]
    detail: str


class Activity(BaseModel):
    id: str
    occurred_at: datetime
    action: str
    detail: str
    category: Literal["read", "create", "system", "reminder"] = "system"


class PriorityItem(BaseModel):
    task_id: str
    title: str
    reason: Literal["overdue", "due_today", "high_priority"]
    priority: Priority
    due_at: datetime | None = None


class Briefing(BaseModel):
    greeting: str
    date_label: str
    overview: list[str]
    priorities: list[str]
    priority_items: list[PriorityItem] = []
    task_summary: TaskSummary = TaskSummary()


class Dashboard(BaseModel):
    briefing: Briefing
    events: list[Event]
    mails: list[MailSummary]
    tasks: list[Task]
    providers: list[ProviderStatus]
    activities: list[Activity]
    calendar: CalendarState = CalendarState()
    mail: MailState = MailState()


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=3000)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=100)  # null starts a new stored conversation


class ActionCard(BaseModel):
    action_id: str | None = None
    kind: Literal["mail_draft", "calendar_event", "calendar_update", "calendar_delete", "task", "task_create", "task_update", "task_complete", "task_delete", "memo_create", "memo_update", "memo_delete", "mail_send", "mail_delete", "mail_move", "mail_read"]
    title: str
    fields: dict[str, str]
    requires_confirmation: bool = True
    status: ApprovalStatus = ApprovalStatus.PENDING_APPROVAL


class ActionExecutionResult(BaseModel):
    action: ActionCard
    message: str


class ToolTrace(BaseModel):
    tool: str
    risk: ToolRisk
    success: bool
    duration_ms: int


class ChatResponse(BaseModel):
    conversation_id: str | None = None
    message: str
    ui_results: list[dict[str, Any]] = []  # display-only payloads (e.g. mail lists) for the screen; never sent to the model, never stored
    sources: list[str] = []
    action: ActionCard | None = None
    trace: list[ToolTrace] = []
    metadata: dict[str, Any] = {}


class KnowledgeSourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    local_path: str = Field(min_length=1, max_length=2048)


class KnowledgeSource(BaseModel):
    id: str
    name: str
    local_path: str
    created_at: str
    last_synced_at: str | None = None
    artifact_count: int = 0


class KnowledgeArtifact(BaseModel):
    id: str
    source_id: str
    title: str
    relative_path: str
    size_bytes: int
    modified_at: str
    chunk_count: int = 0
    parsed: bool = False


class KnowledgeArtifactDetail(KnowledgeArtifact):
    content: str = ""


class SyncRun(BaseModel):
    id: str
    source_id: str
    status: str
    total: int = 0
    scanned: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    deleted: int = 0
    parsed: int = 0
    failed: int = 0
    chunks_created: int = 0
    error: str | None = None
    started_at: str
    finished_at: str | None = None


class ConversationSummary(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


class StoredMessage(BaseModel):
    """Display data only: text, a one-line trace per tool and the card as it was shown. Raw tool output is never stored."""
    id: str
    seq: int
    role: Literal["user", "assistant"]
    content: str
    trace: list[ToolTrace] = []
    sources: list[str] = []
    action: ActionCard | None = None
    created_at: datetime


class ConversationDetail(ConversationSummary):
    messages: list[StoredMessage] = []
