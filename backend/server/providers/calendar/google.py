from __future__ import annotations

import json
import logging
import os
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from server.models.schemas import CalendarConnectionStatus, CalendarEventDraft, CalendarEventPatch, Event
from server.providers.calendar.base import CalendarEventNotFound, CalendarNotConnected, CalendarProviderError
from server.providers.calendar.token_store import OAuthTokenStore, default_token_store

logger = logging.getLogger("workmate.calendar")
EVENT_SCOPE = "https://www.googleapis.com/auth/calendar.events"
ENABLE_API_URL = "https://console.cloud.google.com/apis/library/calendar-json.googleapis.com"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
OAUTH_TIMEOUT_SECONDS = 180
SUCCESS_PAGE = "WorkMate 연동이 완료되었습니다. 이 창을 닫고 WorkMate 앱으로 돌아가세요."
MAX_PAGES = 5


class GoogleCalendarProvider:
    """Google Calendar v3 adapter. OAuth 2.0 Authorization Code + PKCE through the loopback (desktop) flow."""

    def __init__(
        self,
        credentials_path: str | None = None,
        token_store: OAuthTokenStore | None = None,
        calendar_id: str | None = None,
        timezone_name: str | None = None,
        service_factory: Any | None = None,
    ) -> None:
        self.credentials_path = Path(credentials_path or os.getenv("GOOGLE_CALENDAR_CREDENTIALS_PATH", "")).expanduser()
        token_path = Path(os.getenv("GOOGLE_CALENDAR_TOKEN_PATH", "data/google_calendar_token.json")).expanduser()
        self.token_store = token_store or default_token_store(token_path)
        self.calendar_id = calendar_id or os.getenv("GOOGLE_CALENDAR_ID", "primary")
        self.timezone = ZoneInfo(timezone_name or os.getenv("WORKMATE_TIMEZONE", "Asia/Seoul"))
        self._service_factory = service_factory

    # ----- connection -----
    def status(self) -> CalendarConnectionStatus:
        if not self.credentials_path or not self.credentials_path.is_file():
            return CalendarConnectionStatus(provider="google", state="offline", detail="OAuth Desktop Client JSON이 설정되지 않았습니다. docs/google-calendar-setup.md를 참고하세요.", calendar_id=self.calendar_id)
        try:
            has_token = bool(self.token_store.read())
        except Exception as error:
            logger.warning("calendar_token_store_read_failed type=%s", type(error).__name__)
            return CalendarConnectionStatus(provider="google", state="offline", detail="토큰 저장소를 읽지 못했습니다. 다시 연결해 주세요.", calendar_id=self.calendar_id)
        if not has_token:
            return CalendarConnectionStatus(provider="google", state="offline", detail="Google 계정 연결이 필요합니다.", calendar_id=self.calendar_id)
        return CalendarConnectionStatus(provider="google", state="connected", detail=f"Google Calendar 연결됨 · 토큰: {self.token_store.label}", calendar_id=self.calendar_id)

    def connect(self) -> CalendarConnectionStatus:
        self._service(interactive=True)
        logger.info("calendar_connected provider=google calendar_id=%s", self.calendar_id)
        return self.status()

    def disconnect(self) -> CalendarConnectionStatus:
        """Revoke the grant at Google (best effort) and always remove the local token."""
        try:
            raw = self.token_store.read()
            if raw:
                self._revoke(raw)
        except Exception as error:
            logger.warning("calendar_revoke_skipped type=%s", type(error).__name__)
        self.token_store.delete()
        logger.info("calendar_disconnected provider=google calendar_id=%s", self.calendar_id)
        return self.status()

    def verify(self) -> CalendarConnectionStatus:
        # An events list is authorized by the narrow calendar.events scope; calendar metadata may require a broader scope.
        self._call(lambda service: service.events().list(calendarId=self.calendar_id, maxResults=1, singleEvents=True).execute())
        logger.info("calendar_verified provider=google calendar_id=%s", self.calendar_id)
        return self.status()

    # ----- reads -----
    def today_events(self) -> list[Event]:
        start = datetime.now(self.timezone).replace(hour=0, minute=0, second=0, microsecond=0)
        return self.events_between(start, start + timedelta(days=1))

    def tomorrow_events(self) -> list[Event]:
        start = datetime.now(self.timezone).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        return self.events_between(start, start + timedelta(days=1))

    def events_between(self, start: datetime, end: datetime, query: str | None = None) -> list[Event]:
        """Single-occurrence view: recurring series are expanded (`singleEvents`), all-day and timed events included."""
        def read(service: Any) -> list[Event]:
            events: list[Event] = []
            token: str | None = None
            for _ in range(MAX_PAGES):
                payload = service.events().list(
                    calendarId=self.calendar_id, timeMin=start.isoformat(), timeMax=end.isoformat(), q=(query or "").strip() or None,
                    maxResults=250, singleEvents=True, orderBy="startTime", pageToken=token,
                ).execute()
                events += [self.normalize(item, self.calendar_id, self.timezone) for item in payload.get("items", []) if item.get("status") != "cancelled"]
                token = payload.get("nextPageToken")
                if not token:
                    break
            return events
        events = self._call(read)
        logger.info("calendar_events_read provider=google count=%d calendar_id=%s", len(events), self.calendar_id)
        return events

    def search_events(self, query: str) -> list[Event]:
        now = datetime.now(self.timezone)
        return self.events_between(now, now + timedelta(days=365), query)

    def get_event(self, event_id: str) -> Event:
        item = self._call(lambda service: service.events().get(calendarId=self.calendar_id, eventId=event_id).execute())
        if item.get("status") == "cancelled":
            raise CalendarEventNotFound("이미 삭제된 일정입니다.")
        return self.normalize(item, self.calendar_id, self.timezone)

    # ----- writes (called only after Action Card approval) -----
    def create_event(self, draft: CalendarEventDraft) -> Event:
        calendar_id = draft.calendar_id or self.calendar_id
        item = self._call(lambda service: service.events().insert(calendarId=calendar_id, body=self._draft_body(draft), sendUpdates="none").execute())
        logger.info("calendar_event_created provider=google calendar_id=%s", calendar_id)
        return self.normalize(item, calendar_id, self.timezone)

    def update_event(self, event_id: str, patch: CalendarEventPatch) -> Event:
        """PATCH only the changed fields. A full PUT would erase description, attendees and reminders."""
        body = self._patch_body(patch)
        if not body:
            raise CalendarProviderError("변경할 내용이 없습니다.")
        item = self._call(lambda service: service.events().patch(calendarId=self.calendar_id, eventId=event_id, body=body, sendUpdates="none").execute())
        logger.info("calendar_event_updated provider=google calendar_id=%s", self.calendar_id)
        return self.normalize(item, self.calendar_id, self.timezone)

    def delete_event(self, event_id: str) -> None:
        self._call(lambda service: service.events().delete(calendarId=self.calendar_id, eventId=event_id, sendUpdates="none").execute())
        logger.info("calendar_event_deleted provider=google calendar_id=%s", self.calendar_id)

    # ----- plumbing -----
    def _call(self, operation: Callable[[Any], Any]) -> Any:
        service = self._service()
        try:
            return operation(service)
        except CalendarProviderError:
            raise
        except Exception as error:
            raise self._safe_error(error) from error

    def _service(self, interactive: bool = False) -> Any:
        credentials = self._credentials(interactive=interactive)
        try:
            if self._service_factory:
                return self._service_factory(credentials)
            from googleapiclient.discovery import build
            return build("calendar", "v3", credentials=credentials, cache_discovery=False)
        except ImportError as error:
            raise CalendarProviderError("Google Calendar 라이브러리가 설치되지 않았습니다. requirements를 설치해 주세요.") from error
        except Exception as error:
            raise self._safe_error(error) from error

    def _credentials(self, interactive: bool) -> Any:
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
        except ImportError as error:
            raise CalendarProviderError("Google Calendar 라이브러리가 설치되지 않았습니다. requirements를 설치해 주세요.") from error

        try:
            raw_token = self.token_store.read()
        except Exception as error:
            raise CalendarNotConnected("토큰 저장소를 읽지 못했습니다. 다시 연결해 주세요.") from error
        try:
            credentials = Credentials.from_authorized_user_info(json.loads(raw_token), [EVENT_SCOPE]) if raw_token else None
        except Exception as error:
            if not interactive:
                raise CalendarNotConnected("Google Calendar 토큰을 읽지 못했습니다. 다시 연결해 주세요.") from error
            credentials = None
        try:
            if credentials and credentials.valid:
                return credentials
            if credentials and credentials.expired and credentials.refresh_token:
                credentials.refresh(Request())
                self.token_store.write(credentials.to_json())
                return credentials
        except Exception as error:
            logger.warning("calendar_token_refresh_failed type=%s", type(error).__name__)
            if not interactive:
                raise CalendarNotConnected("Google Calendar 연결이 만료되었거나 취소되었습니다. 설정에서 다시 연결해 주세요.") from error
        if not interactive:
            raise CalendarNotConnected("Google Calendar 계정을 먼저 연결해 주세요.")
        if not self.credentials_path or not self.credentials_path.is_file():
            raise CalendarNotConnected("GOOGLE_CALENDAR_CREDENTIALS_PATH에 OAuth Desktop Client JSON 경로를 설정해 주세요.")
        try:
            # PKCE (S256 code challenge) is enabled by default in google-auth-oauthlib; the loopback redirect needs no client secret at rest in the app.
            flow = InstalledAppFlow.from_client_secrets_file(str(self.credentials_path), [EVENT_SCOPE])
            credentials = flow.run_local_server(host="127.0.0.1", port=0, open_browser=True, authorization_prompt_message="", success_message=SUCCESS_PAGE, timeout_seconds=OAUTH_TIMEOUT_SECONDS)
            self.token_store.write(credentials.to_json())
            return credentials
        except Exception as error:
            raise self._safe_error(error) from error

    @staticmethod
    def _revoke(raw_token: str) -> None:
        import requests
        data = json.loads(raw_token)
        token = data.get("refresh_token") or data.get("token")
        if token:
            requests.post(REVOKE_URL, params={"token": token}, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=5)

    @staticmethod
    def _safe_error(error: Exception) -> CalendarProviderError:
        """Translate API failures into short Korean messages; never include request URLs, headers or bodies."""
        status = getattr(getattr(error, "resp", None), "status", None) or getattr(error, "status_code", None)
        if status == 404 or status == 410:
            return CalendarEventNotFound("일정을 찾을 수 없습니다(이미 삭제되었을 수 있습니다).")
        if status == 401:
            return CalendarNotConnected("Google Calendar 권한이 없거나 연결이 만료되었습니다. 설정에서 다시 연결해 주세요.")
        if status == 403:
            reason = GoogleCalendarProvider._error_reason(error)
            if reason in {"accessNotConfigured", "SERVICE_DISABLED"}:
                return CalendarProviderError(f"이 Google Cloud 프로젝트에서 Google Calendar API가 사용 설정되지 않았습니다. {ENABLE_API_URL} 에서 '사용'을 누른 뒤 1~2분 후 다시 시도하세요.")
            if reason in {"insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT", "forbidden"}:
                return CalendarProviderError("Google Calendar 권한 범위가 부족합니다. 연결을 해제한 뒤 다시 연결하고, 허용 화면에서 캘린더 권한을 체크해 주세요.")
            if reason in {"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded"}:
                return CalendarProviderError("Google Calendar 요청 한도를 초과했습니다. 잠시 후 다시 시도해 주세요.")
            return CalendarProviderError("Google Calendar 접근이 거부되었습니다(API 사용 설정, 권한 범위, 계정 권한을 확인하세요).")
        if status == 429:
            return CalendarProviderError("Google Calendar 요청 한도를 초과했습니다. 잠시 후 다시 시도해 주세요.")
        if isinstance(status, int) and status >= 500:
            return CalendarProviderError("Google Calendar 서버에 일시적인 문제가 있습니다. 잠시 후 다시 시도해 주세요.")
        if isinstance(error, TimeoutError) or type(error).__name__ in {"TimeoutError", "ConnectionError", "ServerNotFoundError", "ConnectTimeout"}:
            return CalendarProviderError("Google Calendar에 연결할 수 없습니다. 네트워크를 확인해 주세요.")
        return CalendarProviderError(f"Google Calendar 요청에 실패했습니다({type(error).__name__}).")

    @staticmethod
    def _error_reason(error: Exception) -> str | None:
        """The machine-readable reason Google returns with an HTTP error (never the message, URL or headers)."""
        try:
            body = json.loads(getattr(error, "content", b"") or b"{}").get("error", {})
        except Exception:
            return None
        for detail in body.get("details", []) or []:
            if isinstance(detail, dict) and detail.get("reason"):
                return str(detail["reason"])
        errors = body.get("errors") or []
        return str(errors[0].get("reason")) if errors and isinstance(errors[0], dict) and errors[0].get("reason") else None

    def _time_body(self, moment: datetime) -> dict[str, str]:
        local = moment.astimezone(self.timezone)
        return {"dateTime": local.isoformat(), "timeZone": self.timezone.key}

    def _draft_body(self, draft: CalendarEventDraft) -> dict[str, Any]:
        body: dict[str, Any] = {"summary": draft.title, "description": draft.description, "location": draft.location}
        if draft.all_day:
            start = draft.starts_at.astimezone(self.timezone).date()
            end = draft.ends_at.astimezone(self.timezone).date() if draft.ends_at else start + timedelta(days=1)
            body["start"], body["end"] = {"date": start.isoformat()}, {"date": end.isoformat()}
        else:
            body["start"] = self._time_body(draft.starts_at)
            body["end"] = self._time_body(draft.ends_at or draft.starts_at + timedelta(hours=1))
        return body

    def _patch_body(self, patch: CalendarEventPatch) -> dict[str, Any]:
        changes = patch.model_dump(exclude_unset=True)
        body: dict[str, Any] = {}
        if changes.get("title") is not None:
            body["summary"] = changes["title"]
        if changes.get("description") is not None:
            body["description"] = changes["description"]
        if changes.get("location") is not None:
            body["location"] = changes["location"]
        if patch.starts_at is not None and patch.ends_at is not None:
            if patch.all_day:
                body["start"] = {"date": patch.starts_at.astimezone(self.timezone).date().isoformat()}
                body["end"] = {"date": patch.ends_at.astimezone(self.timezone).date().isoformat()}
            else:
                body["start"], body["end"] = self._time_body(patch.starts_at), self._time_body(patch.ends_at)
        return body

    @staticmethod
    def normalize(item: dict[str, Any], calendar_id: str, timezone: ZoneInfo) -> Event:
        start, all_day = GoogleCalendarProvider._parse_time(item.get("start", {}), timezone)
        end, _ = GoogleCalendarProvider._parse_time(item.get("end", {}), timezone)
        return Event(
            id=item["id"], title=item.get("summary") or "제목 없음", description=item.get("description") or "",
            starts_at=start, ends_at=end, all_day=all_day, location=item.get("location") or "",
            calendar_id=calendar_id, source="google",
            is_recurring=bool(item.get("recurringEventId") or item.get("recurrence")),
            version=item.get("updated") or item.get("etag"),
        )

    @staticmethod
    def _parse_time(value: dict[str, str], timezone: ZoneInfo) -> tuple[datetime, bool]:
        """All-day: a date at local midnight. Timed: honour the event's own offset/timeZone, then express it in the user's zone."""
        if value.get("date"):
            date = datetime.fromisoformat(value["date"]).date()
            return datetime.combine(date, time.min, tzinfo=timezone), True
        raw = value.get("dateTime")
        if not raw:
            raise CalendarProviderError("Google Calendar 이벤트에 시작 시간이 없습니다.")
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            try:
                source_zone = ZoneInfo(value["timeZone"]) if value.get("timeZone") else timezone
            except Exception:
                source_zone = timezone
            parsed = parsed.replace(tzinfo=source_zone)
        return parsed.astimezone(timezone), False
