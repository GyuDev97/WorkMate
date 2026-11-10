from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, time as clock, timedelta
from typing import Any, Callable
from uuid import uuid4

from pydantic import BaseModel, Field

from server.models.schemas import ActionCard, Priority, TaskStatus, TaskView, ToolRisk, ToolTrace
from server.services.dates import parse_day_range
from server.services.local_actions import LocalActionService
from server.services.memos import MemoService
from server.services.tasks import TaskService
from server.tools.calendar import CalendarTool
from server.services.calendar_actions import CalendarActionService
from server.tools.mail import MailTool, SplitResult
from server.tools.memory import MemoryTool


class SearchArgs(BaseModel):
    query: str = Field(default="", max_length=300)


class ListTasksArgs(BaseModel):
    view: TaskView = "all"
    query: str = Field(default="", max_length=200)


class TaskCreateArgs(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    description: str = Field(default="", max_length=10000)
    priority: Priority = Priority.MEDIUM
    start: str | None = Field(default=None, description="Start date in Asia/Seoul, same formats as due (a date alone means the start of that day). Null when the user gave no start date; never invent one.")
    due: str | None = Field(default=None, description="End/due date in Asia/Seoul: YYYY-MM-DD, YYYY-MM-DDTHH:MM, or 오늘/내일/모레/[이번 주|다음 주] 금요일 [HH:MM]. Null when the user gave no end date; never invent one.")


class TaskUpdateArgs(BaseModel):
    target: str = Field(min_length=1, description="Task id, or the task title as the user said it.")
    title: str | None = None
    description: str | None = None
    priority: Priority | None = None
    due: str | None = Field(default=None, description="New due date (same formats as create). Null to leave unchanged.")
    clear_due: bool = False
    start: str | None = Field(default=None, description="New start date (same formats as create). Null to leave unchanged.")
    clear_start: bool = False
    status: TaskStatus | None = None


class TargetArgs(BaseModel):
    target: str = Field(min_length=1, description="Id, or the title as the user said it.")


class ListMemosArgs(BaseModel):
    query: str = Field(default="", max_length=200)
    limit: int = Field(default=10, ge=1, le=30)


class MemoCreateArgs(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(default="", max_length=50000, description="Only text the user wrote or that appears in this conversation. Never invent content.")
    task_target: str | None = Field(default=None, description="Optional task id or title to link.")


class MemoUpdateArgs(BaseModel):
    target: str = Field(min_length=1)
    title: str | None = None
    content: str | None = None
    task_target: str | None = None
    unlink_task: bool = False


class SearchMailArgs(BaseModel):
    query: str = Field(default="", max_length=200, description="Search words. Empty lists the inbox. Results are shown on screen only; you will receive just a count.")
    unread_only: bool = False


class MailSendArgs(BaseModel):
    to: list[str] = Field(min_length=1, max_length=20, description="Recipient addresses exactly as the user typed them.")
    cc: list[str] = Field(default_factory=list, max_length=20)
    bcc: list[str] = Field(default_factory=list, max_length=20)
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=10000, description="Only text the user dictated; never invent content.")


class ListEventsArgs(BaseModel):
    start_date: str = Field(description="First day: YYYY-MM-DD, 오늘/내일/모레, [이번 주|다음 주] 요일, or exactly 이번 주 / 다음 주 for Monday-Sunday.")
    end_date: str | None = Field(default=None, description="Last day, inclusive. Null for a single day. Max 62 days.")
    query: str = Field(default="", max_length=200, description="Optional title filter.")


class EventTargetArgs(BaseModel):
    target: str = Field(min_length=1, description="Event id, or the event title as the user said it.")
    date: str | None = Field(default=None, description="Day the event is on (YYYY-MM-DD or 내일/금요일 ...) to narrow the search. Strongly recommended.")


class CalendarCreateArgs(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    date: str = Field(description="YYYY-MM-DD, 오늘/내일/모레, or [이번 주|다음 주] 요일.")
    time: str | None = Field(default=None, description="Start time HH:MM (24h, Asia/Seoul). Null only for all-day events.")
    duration_minutes: int = Field(default=60, ge=5, le=1440)
    all_day: bool = False
    description: str = Field(default="", max_length=5000)
    location: str = Field(default="", max_length=300)


class CalendarUpdateArgs(BaseModel):
    target: str = Field(min_length=1, description="Event id or title as the user said it.")
    date: str | None = Field(default=None, description="Day the event is currently on, to narrow the search.")
    title: str | None = Field(default=None, description="New title, or null.")
    new_date: str | None = None
    new_time: str | None = Field(default=None, description="New start time HH:MM, or null to keep.")
    duration_minutes: int | None = Field(default=None, ge=5, le=1440)
    description: str | None = None
    location: str | None = None


def _strip_defaults(node: Any) -> Any:
    """OpenAI strict schemas reject `default` (notably next to a `$ref`); pydantic still applies defaults server-side."""
    if isinstance(node, dict):
        return {key: _strip_defaults(value) for key, value in node.items() if key != "default"}
    if isinstance(node, list):
        return [_strip_defaults(item) for item in node]
    return node


@dataclass(frozen=True)
class RegisteredTool:
    name: str
    description: str
    risk: ToolRisk
    arguments_model: type[BaseModel]
    handler: Callable[[BaseModel], Any]

    def schema(self) -> dict[str, Any]:
        parameters = self.arguments_model.model_json_schema()
        parameters["additionalProperties"] = False
        # Strict function schemas must list every property as required (optional ones are nullable instead).
        parameters["required"] = list(parameters.get("properties", {}))
        return {
            "type": "function", "name": self.name, "description": self.description,
            "parameters": _strip_defaults(parameters), "strict": True,
        }


class ToolRegistry:
    """Single source of truth for tool descriptions, validation, risk and execution."""

    def __init__(self, tasks: TaskService, calendar: CalendarTool, mail: MailTool, memory: MemoryTool, calendar_actions: CalendarActionService, local_actions: LocalActionService, memos: MemoService) -> None:
        self.tasks, self.calendar, self.mail, self.memory, self.memos = tasks, calendar, mail, memory, memos
        self.calendar_actions, self.local_actions = calendar_actions, local_actions
        self._tools = {tool.name: tool for tool in self._build_tools()}

    def _build_tools(self) -> list[RegisteredTool]:
        return [
            RegisteredTool("get_today_events", "Get the user's events scheduled for today.", ToolRisk.READ, SearchArgs, lambda _args: self.calendar.today_events()),
            RegisteredTool("get_tomorrow_events", "Get the user's events scheduled for tomorrow.", ToolRisk.READ, SearchArgs, lambda _args: self.calendar.tomorrow_events()),
            RegisteredTool("search_events", "Search calendar events by a query.", ToolRisk.READ, SearchArgs, lambda args: self.calendar.search_events(args.query)),
            RegisteredTool("list_tasks", "List local workMate tasks from the current SQLite database. Use view=all for a generic request such as 'my tasks' or 'registered tasks'; all includes completed and no-due-date tasks. Use another view only when the user explicitly asks for that condition. query filters title/description.", ToolRisk.READ, ListTasksArgs, lambda args: self.tasks.list_tasks(args.view, args.query)),
            RegisteredTool("list_memos", "List or search the user's memos, most recently updated first. Content is shortened; use get_memo for the full text.", ToolRisk.READ, ListMemosArgs, self._list_memos),
            RegisteredTool("get_memo", "Get one memo with full content by id or title. Returns a clarification request when several memos match.", ToolRisk.READ, TargetArgs, self._get_memo),
            RegisteredTool("propose_task_create", "Propose adding a task. Does NOT save anything; the user must approve the Action Card first.", ToolRisk.PROPOSED_LOCAL_WRITE, TaskCreateArgs, lambda args: self.local_actions.propose_task_create(args.title, args.description, args.priority, args.due, args.start)),
            RegisteredTool("propose_task_update", "Propose changing one task (title, description, priority, start date, due/end date, status; status DONE completes it, TODO reopens it). Does NOT save; requires approval. If several tasks match, a clarification request is returned: ask the user, never pick one.", ToolRisk.PROPOSED_LOCAL_WRITE, TaskUpdateArgs, lambda args: self.local_actions.propose_task_update(args.target, args.title, args.description, args.priority, args.due, args.clear_due, args.status, args.start, args.clear_start)),
            RegisteredTool("propose_task_delete", "Propose deleting one task. Use ONLY when the user explicitly asks to delete it. Requires approval.", ToolRisk.PROPOSED_LOCAL_WRITE, TargetArgs, lambda args: self.local_actions.propose_task_delete(args.target)),
            RegisteredTool("propose_memo_create", "Propose saving a new memo, optionally linked to a task. Does NOT save; requires approval.", ToolRisk.PROPOSED_LOCAL_WRITE, MemoCreateArgs, lambda args: self.local_actions.propose_memo_create(args.title, args.content, args.task_target)),
            RegisteredTool("propose_memo_update", "Propose changing a memo's title, content, or linked task. Does NOT save; requires approval. Ambiguous matches return a clarification request.", ToolRisk.PROPOSED_LOCAL_WRITE, MemoUpdateArgs, lambda args: self.local_actions.propose_memo_update(args.target, args.title, args.content, args.task_target, args.unlink_task)),
            RegisteredTool("propose_memo_delete", "Propose deleting one memo. Use ONLY when the user explicitly asks to delete it. Requires approval.", ToolRisk.PROPOSED_LOCAL_WRITE, TargetArgs, lambda args: self.local_actions.propose_memo_delete(args.target)),
            RegisteredTool("list_events", "List calendar events for a day or date range (today, 내일, 이번 주, a specific date). Each event has id, time, all_day and is_recurring.", ToolRisk.READ, ListEventsArgs, self._list_events),
            RegisteredTool("get_event", "Get one event's full details by id or title. Returns a clarification request when several events match.", ToolRisk.READ, EventTargetArgs, self._get_event),
            RegisteredTool("propose_calendar_event", "Propose creating ONE calendar event. Use only when the user clearly asks to add/register an event; a mere mention of a meeting is NOT a request. Does NOT create anything; the user must approve the card.", ToolRisk.PROPOSED_EXTERNAL_WRITE, CalendarCreateArgs, lambda args: self.calendar_actions.propose_create(args.title, args.date, args.time, args.duration_minutes, args.all_day, args.description, args.location)),
            RegisteredTool("propose_calendar_update", "Propose changing ONE existing event (title, date, time, duration, place). Pass the event as the user named it plus its date; the app finds it. Does NOT change anything until approved. Several matches return a clarification request: ask, never pick. Recurring events are unsupported and return a notice.", ToolRisk.PROPOSED_EXTERNAL_WRITE, CalendarUpdateArgs, lambda args: self.calendar_actions.propose_update(args.target, args.date, args.title, args.new_date, args.new_time, args.duration_minutes, args.description, args.location)),
            RegisteredTool("propose_calendar_delete", "Propose deleting ONE existing event. Use only when the user explicitly asks to delete/cancel it. Requires approval. Recurring events are unsupported.", ToolRisk.PROPOSED_EXTERNAL_WRITE, EventTargetArgs, lambda args: self.calendar_actions.propose_delete(args.target, args.date)),
            RegisteredTool("get_mail_status", "Check whether mail is connected. Returns connection status only.", ToolRisk.READ, SearchArgs, lambda _args: self.mail.status()),
            RegisteredTool("get_unread_mail_count", "Get the number of unread mails. Returns a count only; mail content is never available to you.", ToolRisk.READ, SearchArgs, lambda _args: self.mail.unread_count()),
            RegisteredTool("search_mail", "Search or list mail. The results appear on the user's screen; you receive only a count and must not guess subjects, senders or contents. Tell the user to look at the list on screen.", ToolRisk.READ, SearchMailArgs, lambda args: self.mail.search(args.query, args.unread_only)),
            RegisteredTool("propose_mail_send", "Prepare a NEW outgoing mail from text the user dictated. Nothing is sent; the user must approve the Action Card. You cannot reply to, forward, delete, move or mark existing mail: tell the user to use the Mail screen for that.", ToolRisk.PROPOSED_EXTERNAL_WRITE, MailSendArgs, lambda args: self.mail.propose_send(args.to, args.cc, args.bcc, args.subject, args.body)),
            RegisteredTool("search_work_memory", "Search WorkMemory. This provider may be unavailable in V0.2.", ToolRisk.READ, SearchArgs, lambda args: self.memory.search(args.query)),
        ]

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self._tools.values()]

    def execute(self, name: str, arguments: dict[str, Any]) -> tuple[Any, ToolTrace]:
        started = time.perf_counter()
        tool = self._tools.get(name)
        if not tool:
            return {"error": f"Unknown tool: {name}"}, ToolTrace(tool=name, risk=ToolRisk.READ, success=False, duration_ms=0)
        try:
            parsed = tool.arguments_model.model_validate(arguments)
            value = tool.handler(parsed)
            model_view = value.llm if isinstance(value, SplitResult) else value
            success = not (isinstance(model_view, dict) and ("error" in model_view or model_view.get("success") is False))
            return value, ToolTrace(tool=name, risk=tool.risk, success=success, duration_ms=round((time.perf_counter() - started) * 1000))
        except Exception as error:  # Tool failures are returned to the model, not hidden.
            return {"error": f"{type(error).__name__}: {error}"}, ToolTrace(tool=name, risk=tool.risk, success=False, duration_ms=round((time.perf_counter() - started) * 1000))

    @staticmethod
    def ui_from(value: Any) -> dict[str, Any] | None:
        return value.ui if isinstance(value, SplitResult) else None

    @staticmethod
    def serialize(value: Any) -> str:
        if isinstance(value, SplitResult):
            value = value.llm  # the model sees only the model-facing half
        if isinstance(value, BaseModel): value = value.model_dump(mode="json")
        if isinstance(value, list): value = [item.model_dump(mode="json") if isinstance(item, BaseModel) else item for item in value]
        return json.dumps(value, ensure_ascii=False, default=str)

    @staticmethod
    def action_from(value: Any) -> ActionCard | None:
        return value if isinstance(value, ActionCard) else None

    def _list_memos(self, args: ListMemosArgs) -> list[dict[str, Any]]:
        return [{"id": m.id, "title": m.title, "preview": m.content[:200], "task_title": m.task_title, "updated_at": m.updated_at.isoformat()}
                for m in self.memos.list_memos(args.query)[: args.limit]]

    def _list_events(self, args: ListEventsArgs) -> list[dict[str, Any]]:
        first, last = parse_day_range(args.start_date, args.end_date)
        tz = self.calendar_actions.timezone
        start = datetime.combine(first, clock.min, tz)
        events = self.calendar.events_between(start, start.replace(hour=0) + timedelta(days=(last - first).days + 1), args.query or None)
        return [{"id": e.id, "title": e.title, "when": self.calendar_actions.when(e), "all_day": e.all_day, "is_recurring": e.is_recurring, "location": e.location} for e in events[:100]]

    def _get_event(self, args: EventTargetArgs) -> Any:
        event, problem = self.calendar_actions.resolve_event(args.target, args.date)
        return problem or event

    def _get_memo(self, args: TargetArgs) -> Any:
        memo, problem = self.local_actions.resolve_memo(args.target)
        return problem or memo

