from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from server.models.schemas import CalendarConnectionStatus, CalendarEventDraft, CalendarEventPatch, Event
from server.providers.calendar.base import CalendarEventNotFound

SEOUL = ZoneInfo("Asia/Seoul")


class MockCalendarProvider:
    """Offline provider with the same mutation contract as Google Calendar (fixed demo data; never real events)."""

    def __init__(self) -> None:
        self._created: list[Event] = []
        self._changed: dict[str, Event] = {}
        self._deleted: set[str] = set()
        self._revision = 0

    def status(self) -> CalendarConnectionStatus:
        return CalendarConnectionStatus(provider="mock", state="mock", detail="Google Calendar mock", calendar_id="primary")

    def connect(self) -> CalendarConnectionStatus:
        return self.status()

    def disconnect(self) -> CalendarConnectionStatus:
        return self.status()

    def verify(self) -> CalendarConnectionStatus:
        return self.status()

    def _base_events(self) -> list[Event]:
        today = datetime.now(SEOUL).replace(hour=0, minute=0, second=0, microsecond=0)
        return [
            Event(id="event-poc", title="AI PoC 회의", starts_at=today + timedelta(hours=10), ends_at=today + timedelta(hours=11)),
            Event(id="event-server", title="서버 점검", starts_at=today + timedelta(hours=13, minutes=30), ends_at=today + timedelta(hours=14, minutes=30), is_recurring=True),
            Event(id="event-rag", title="RAG 결과 확인", starts_at=today + timedelta(hours=16), ends_at=today + timedelta(hours=16, minutes=30)),
        ]

    def _all_events(self) -> list[Event]:
        tomorrow = [event.model_copy(update={"id": f"tomorrow-{event.id}", "starts_at": event.starts_at + timedelta(days=1), "ends_at": event.ends_at + timedelta(days=1) if event.ends_at else None}) for event in self._base_events()]
        events = [self._changed.get(event.id, event) for event in self._base_events() + tomorrow + self._created]
        return [event for event in events if event.id not in self._deleted]

    def events_between(self, start: datetime, end: datetime, query: str | None = None) -> list[Event]:
        needle = (query or "").lower().strip()
        found = [e for e in self._all_events() if e.starts_at < end and (e.ends_at or e.starts_at) >= start and (not needle or needle in e.title.lower())]
        return sorted(found, key=lambda event: event.starts_at)

    def get_event(self, event_id: str) -> Event:
        for event in self._all_events():
            if event.id == event_id:
                return event
        raise CalendarEventNotFound("일정을 찾을 수 없습니다.")

    def today_events(self) -> list[Event]:
        start = datetime.now(SEOUL).replace(hour=0, minute=0, second=0, microsecond=0)
        return self.events_between(start, start + timedelta(days=1))

    def tomorrow_events(self) -> list[Event]:
        start = datetime.now(SEOUL).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        return self.events_between(start, start + timedelta(days=1))

    def search_events(self, query: str) -> list[Event]:
        needle = query.lower().strip()
        return [event for event in self._all_events() if not needle or needle in event.title.lower()]

    def _bump(self, event: Event) -> Event:
        self._revision += 1
        return event.model_copy(update={"version": str(self._revision)})

    def create_event(self, draft: CalendarEventDraft) -> Event:
        event = self._bump(Event(id=f"mock-{uuid4()}", source="mock", **draft.model_dump()))
        self._created.append(event)
        return event

    def update_event(self, event_id: str, patch: CalendarEventPatch) -> Event:
        current = self.get_event(event_id)
        changes = {key: value for key, value in patch.model_dump(exclude_unset=True).items() if value is not None}
        updated = self._bump(current.model_copy(update=changes))
        if event_id in {event.id for event in self._created}:
            self._created = [updated if event.id == event_id else event for event in self._created]
        else:
            self._changed[event_id] = updated
        return updated

    def delete_event(self, event_id: str) -> None:
        self.get_event(event_id)
        self._created = [event for event in self._created if event.id != event_id]
        self._deleted.add(event_id)
