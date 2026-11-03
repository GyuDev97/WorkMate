from datetime import datetime, timezone
import os

from server.models.schemas import Briefing, CalendarState, Dashboard, ProviderStatus
from server.services.tasks import TaskService
from server.services.activity import ActivityService
from server.tools.calendar import CalendarTool
from server.providers.calendar.base import CalendarNotConnected, CalendarProviderError
from server.services.mail import MailService


class DashboardService:
    def __init__(self, tasks: TaskService, calendar: CalendarTool, mail: MailService, activity: ActivityService | None = None) -> None:
        self.tasks = tasks
        self.calendar = calendar
        self.mail = mail
        self.activity = activity

    def _calendar(self):
        """Never present mock or stale data as real: not connected and error are explicit states with no events."""
        status = self.calendar.provider.status()
        if status.provider == "mock":
            return self.calendar.today_events(), CalendarState(state="mock", detail="Mock 일정입니다. 실제 Google Calendar가 아닙니다.")
        if status.state != "connected":
            return [], CalendarState(state="not_connected", detail=status.detail)
        try:
            return self.calendar.today_events(), CalendarState(state="connected", detail=status.detail)
        except CalendarNotConnected as error:
            return [], CalendarState(state="not_connected", detail=str(error))
        except Exception as error:
            # Calendar failures degrade to an explicit error state; the rest of the dashboard stays usable.
            return [], CalendarState(state="error", detail=str(error) if isinstance(error, CalendarProviderError) else f"일정을 불러오지 못했습니다({type(error).__name__}).")

    def get_dashboard(self) -> Dashboard:
        events, calendar_state = self._calendar()
        mail_state = self.mail.summary()
        tasks = self.tasks.list_tasks()
        now = datetime.now().astimezone()
        summary = self.tasks.summary(tasks, now)
        priority_items = self.tasks.priority_items(tasks, now)
        hour = now.hour
        mail_overview = f"미확인 메일 {mail_state.unread_count}건" if mail_state.unread_count is not None else "메일 확인 불가"
        return Dashboard(
            briefing=Briefing(
                greeting="좋은 아침입니다." if hour < 12 else "좋은 오후입니다." if hour < 18 else "좋은 저녁입니다.",
                date_label=now.strftime("%Y년 %m월 %d일"),
                overview=[f"오늘 일정 {len(events)}건" if calendar_state.state in ("connected", "mock") else "오늘 일정 확인 불가", f"진행 중인 업무 {summary.active}건", mail_overview],
                # Built from SQLite tasks only, so the briefing works without any LLM call.
                priorities=[item.title for item in priority_items],
                priority_items=priority_items,
                task_summary=summary,
            ),
            events=events, mails=[], tasks=tasks, calendar=calendar_state, mail=mail_state,
            providers=[
                ProviderStatus(name="LLM", state="connected" if os.getenv("WORKMATE_LLM_PROVIDER") == "openai" and os.getenv("OPENAI_API_KEY") else "mock", detail="OpenAI Responses API" if os.getenv("WORKMATE_LLM_PROVIDER") == "openai" and os.getenv("OPENAI_API_KEY") else "Mock provider (set OPENAI_API_KEY to enable OpenAI)"),
                ProviderStatus(name="Mail", state={"mock": "mock", "connected": "connected"}.get(mail_state.state, "offline"), detail=mail_state.detail),
                ProviderStatus(**self.calendar.provider.status().model_dump(exclude={"provider", "calendar_id"}), name="Calendar"),
                ProviderStatus(name="Memory", state="offline", detail="WorkMemory 연결 대기"),
            ],
            activities=self.activity.recent() if self.activity else [],
        )
