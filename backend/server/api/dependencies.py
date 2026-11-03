from functools import lru_cache
import os

from server.agent.agent import WorkMateAgent
from server.agent.tool_registry import ToolRegistry
from server.database.repository import ConversationRepository, MemoRepository, TaskRepository
from server.services.dashboard import DashboardService
from server.services.conversations import ConversationService
from server.services.local_actions import LocalActionService
from server.services.memos import MemoService
from server.services.tasks import TaskService
from server.services.activity import ActivityService
from server.tools.calendar import CalendarTool
from server.tools.mail import MailTool
from server.providers.mail.bizmeka import BizmekaMailProvider, BizmekaSettings
from server.providers.mail.mock import MockMailProvider
from server.providers.mail.none import UnavailableMailProvider
from server.services.mail import MailActionService, MailService
from server.tools.memory import MemoryTool
from server.providers.llm.mock import MockLLMProvider
from server.providers.llm.openai_provider import OpenAIProvider
from server.providers.calendar.google import GoogleCalendarProvider
from server.providers.calendar.mock import MockCalendarProvider
from server.services.calendar_actions import CalendarActionService
from server.services.knowledge import KnowledgeService


@lru_cache
def task_service() -> TaskService:
    return TaskService(TaskRepository())


@lru_cache
def memo_service() -> MemoService:
    task_service()  # memo queries join the tasks table, so make sure it exists first
    return MemoService(MemoRepository())


@lru_cache
def knowledge_service() -> KnowledgeService:
    return KnowledgeService()


@lru_cache
def activity_service() -> ActivityService:
    return ActivityService()


@lru_cache
def local_action_service() -> LocalActionService:
    return LocalActionService(task_service(), memo_service(), activity_service())


def _bizmeka_session_cookie() -> str | None:
    """The session cookie comes from BIZMEKA_SESSION_COOKIE or the OS credential store; it is never read from a file in the repo."""
    try:
        import keyring
        return keyring.get_password("workMate", "bizmeka-session")
    except Exception:
        return None


@lru_cache
def mail_provider():
    requested = os.getenv("WORKMATE_MAIL_PROVIDER", "none").lower()
    if requested == "mock":
        return MockMailProvider()
    if requested == "bizmeka":
        return BizmekaMailProvider(BizmekaSettings.from_env(cookie=None if os.getenv("BIZMEKA_SESSION_COOKIE") else _bizmeka_session_cookie()))
    return UnavailableMailProvider()


@lru_cache
def mail_service() -> MailService:
    return MailService(mail_provider(), max_download_bytes=int(os.getenv("BIZMEKA_MAX_DOWNLOAD_MB", "25")) * 1024 * 1024)


@lru_cache
def mail_action_service() -> MailActionService:
    return MailActionService(mail_service(), activity_service())


@lru_cache
def calendar_provider():
    requested = os.getenv("WORKMATE_CALENDAR_PROVIDER", "mock").lower()
    if requested == "google":
        return GoogleCalendarProvider()
    return MockCalendarProvider()


@lru_cache
def calendar_tool() -> CalendarTool:
    return CalendarTool(calendar_provider())


@lru_cache
def calendar_action_service() -> CalendarActionService:
    return CalendarActionService(calendar_tool(), activity_service())


@lru_cache
def dashboard_service() -> DashboardService:
    return DashboardService(task_service(), calendar_tool(), mail_service(), activity_service())


@lru_cache
def tool_registry() -> ToolRegistry:
    return ToolRegistry(task_service(), calendar_tool(), MailTool(mail_service(), mail_action_service()), MemoryTool(), calendar_action_service(), local_action_service(), memo_service())


@lru_cache
def llm_provider():
    requested = os.getenv("WORKMATE_LLM_PROVIDER", "mock").lower()
    api_key = os.getenv("OPENAI_API_KEY", "")
    if requested == "openai" and api_key:
        return OpenAIProvider(api_key, os.getenv("WORKMATE_LLM_MODEL", "gpt-4.1-mini"))
    return MockLLMProvider()


@lru_cache
def agent() -> WorkMateAgent:
    return WorkMateAgent(llm_provider(), tool_registry(), activity_service())


@lru_cache
def conversation_service() -> ConversationService:
    # Resolved lazily so each call uses the current agent and approval stores.
    return ConversationService(ConversationRepository(), lambda: agent(), lambda: (local_action_service(), calendar_action_service(), mail_action_service()))
