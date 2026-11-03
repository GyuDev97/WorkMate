from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from server.models.schemas import ActionCard, CalendarEventDraft, CalendarEventPatch, Event
from server.providers.calendar.base import CalendarEventNotFound, CalendarNotConnected, CalendarProviderError
from server.services.activity import ActivityService
from server.services.approvals import ApprovalStore
from server.services.dates import SEOUL, parse_day
from server.tools.calendar import CalendarTool

Proposal = ActionCard | dict[str, Any]
RECURRING_NOTICE = "반복 일정입니다. 이번 단계에서는 단일 일정만 수정·삭제할 수 있고, 반복 일정(이번만/전체)의 변경은 지원하지 않습니다. Google Calendar에서 직접 변경해 주세요."
SEARCH_BACK_DAYS, SEARCH_AHEAD_DAYS = 1, 60


def _signature(event: Event) -> tuple:
    return (event.version, event.title, event.starts_at, event.ends_at)


class CalendarActionService(ApprovalStore):
    """Approval-gated calendar mutations. Proposals only read; the provider is written to after the user approves."""

    unchanged_text = "캘린더는 변경되지 않았습니다."

    def __init__(self, calendar: CalendarTool, activity: ActivityService | None = None, timezone: ZoneInfo = SEOUL) -> None:
        super().__init__(activity)
        self.calendar, self.timezone = calendar, timezone

    def _describe_error(self, error: Exception) -> str:
        return str(error) if isinstance(error, CalendarProviderError) else f"일정을 처리하지 못했습니다({type(error).__name__})."

    # ----- formatting -----
    def when(self, event: Event) -> str:
        start = event.starts_at.astimezone(self.timezone)
        if event.all_day:
            last = (event.ends_at.astimezone(self.timezone).date() - timedelta(days=1)) if event.ends_at else start.date()
            return f"{start:%Y-%m-%d} (종일)" if last <= start.date() else f"{start:%Y-%m-%d} ~ {last:%Y-%m-%d} (종일)"
        end = event.ends_at.astimezone(self.timezone) if event.ends_at else None
        span = f"{start:%Y-%m-%d %H:%M}" + (f" – {end:%H:%M}" if end and end.date() == start.date() else f" – {end:%Y-%m-%d %H:%M}" if end else "")
        return span

    def _ready(self) -> dict[str, Any] | None:
        status = self.calendar.provider.status()
        if status.state == "offline":
            return {"error": f"Google Calendar를 사용할 수 없습니다. {status.detail}"}
        return None

    # ----- target resolution (never silently picks among several matches) -----
    def resolve_event(self, target: str, date_hint: str | None = None) -> tuple[Event | None, dict[str, Any] | None]:
        target = target.strip()
        if not target:
            return None, {"error": "대상 일정을 지정해 주세요."}
        if problem := self._ready():
            return None, problem
        if re.fullmatch(r"[A-Za-z0-9_\-]{4,}", target):
            try:
                return self.calendar.get_event(target), None
            except CalendarNotConnected:
                raise
            except CalendarProviderError:
                pass  # not an id; treat it as a title
        now = datetime.now(self.timezone)
        if date_hint and date_hint.strip():
            day = parse_day(date_hint, now)
            start = datetime.combine(day, time.min, self.timezone)
            end = start + timedelta(days=1)
        else:
            start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=SEARCH_BACK_DAYS)
            end = start + timedelta(days=SEARCH_BACK_DAYS + SEARCH_AHEAD_DAYS)
        needle = target.casefold()
        pool = self.calendar.events_between(start, end)
        words = needle.split()
        # exact title, then contiguous text, then every word present ("[테스트] 회의" finds "[테스트] 기획 회의")
        matches = [e for e in pool if e.title.casefold() == needle] or [e for e in pool if needle in e.title.casefold()] or [e for e in pool if all(w in e.title.casefold() for w in words)]
        if len(matches) == 1:
            return matches[0], None
        if not matches:
            return None, {"error": f"‘{target}’에 해당하는 일정을 찾지 못했습니다." + ("" if date_hint else f" (오늘부터 {SEARCH_AHEAD_DAYS}일 이내만 검색합니다. 날짜를 알려 주세요.)")}
        candidates = [{"id": e.id, "title": e.title, "when": self.when(e), "recurring": e.is_recurring} for e in matches[:10]]
        return None, {"status": "needs_clarification", "message": f"‘{target}’와 일치하는 일정이 {len(matches)}개입니다. 어느 일정인지 사용자에게 확인하세요. 임의로 고르지 마세요.", "candidates": candidates}

    # ----- proposals -----
    def propose_create(self, title: str, day: str, at_time: str | None, duration_minutes: int, all_day: bool, description: str = "", location: str = "") -> Proposal:
        if problem := self._ready():
            return problem
        target_day = parse_day(day)
        if all_day:
            starts_at = datetime.combine(target_day, time.min, self.timezone)
            ends_at = starts_at + timedelta(days=1)
        else:
            if not at_time:
                return {"error": "시간을 알려 주세요. (종일 일정이면 all_day를 true로 지정)"}
            starts_at = datetime.combine(target_day, self._parse_time(at_time), self.timezone)
            ends_at = starts_at + timedelta(minutes=duration_minutes)
        draft = CalendarEventDraft(title=title, starts_at=starts_at, ends_at=ends_at, all_day=all_day, description=description, location=location, calendar_id=self.calendar.provider.status().calendar_id)
        fields = {"제목": draft.title, "일시": self.when(Event(id="draft", title=title, starts_at=starts_at, ends_at=ends_at, all_day=all_day)), "캘린더": draft.calendar_id}
        if location:
            fields["장소"] = location
        if not all_day and starts_at < datetime.now(self.timezone):
            fields["주의"] = "이미 지난 시각입니다."
        if overlap := self._overlaps(starts_at, ends_at):
            fields["겹치는 일정"] = overlap
        return self._save("calendar_event", "일정 등록", fields, lambda: None, lambda: f"‘{self.calendar.create_event(draft).title}’ 일정을 등록했습니다.", "일정 등록")

    def propose_update(self, target: str, date_hint: str | None, title: str | None, new_date: str | None, new_time: str | None, duration_minutes: int | None, description: str | None, location: str | None) -> Proposal:
        event, problem = self.resolve_event(target, date_hint)
        if problem:
            return problem
        assert event is not None
        if event.is_recurring:
            return {"status": "unsupported", "message": f"‘{event.title}’ — {RECURRING_NOTICE}"}
        cur_start = event.starts_at.astimezone(self.timezone)
        cur_end = (event.ends_at or event.starts_at + timedelta(hours=1)).astimezone(self.timezone)
        lines: list[str] = []
        patch: dict[str, Any] = {}
        if title is not None and title != event.title:
            patch["title"] = title; lines.append(f"제목: {event.title} → {title}")
        if description is not None and description != event.description:
            patch["description"] = description; lines.append("설명: 변경")
        if location is not None and location != event.location:
            patch["location"] = location; lines.append(f"장소: {event.location or '없음'} → {location or '없음'}")
        new_start, new_end = cur_start, cur_end
        if event.all_day:
            if new_time or duration_minutes:
                return {"error": "종일 일정에는 시간을 지정할 수 없습니다. 날짜만 변경할 수 있습니다."}
            if new_date:
                days = max(1, (cur_end.date() - cur_start.date()).days)
                new_start = datetime.combine(parse_day(new_date), time.min, self.timezone)
                new_end = new_start + timedelta(days=days)
        elif new_date or new_time or duration_minutes:
            day = parse_day(new_date) if new_date else cur_start.date()
            clock = self._parse_time(new_time) if new_time else cur_start.timetz().replace(tzinfo=None)
            new_start = datetime.combine(day, clock, self.timezone)
            new_end = new_start + (timedelta(minutes=duration_minutes) if duration_minutes else cur_end - cur_start)
        if (new_start, new_end) != (cur_start, cur_end):
            patch.update(starts_at=new_start, ends_at=new_end, all_day=event.all_day)
            lines.append(f"일시: {self.when(event)} → {self.when(event.model_copy(update={'starts_at': new_start, 'ends_at': new_end}))}")
        if not patch:
            return {"status": "no_change", "message": f"‘{event.title}’ 일정에 적용할 변경 사항이 없습니다."}
        change = CalendarEventPatch(**patch)
        fields = {"대상": event.title, "현재": self.when(event), "변경": "\n".join(lines)}
        if "starts_at" in patch and not event.all_day and (overlap := self._overlaps(new_start, new_end, exclude=event.id)):
            fields["겹치는 일정"] = overlap
        snapshot, event_id = event, event.id
        return self._save("calendar_update", "일정 변경", fields, lambda: self._stale(event_id, snapshot),
                          lambda: f"‘{self.calendar.update_event(event_id, change).title}’ 일정을 변경했습니다.", "일정 수정")

    def propose_delete(self, target: str, date_hint: str | None) -> Proposal:
        event, problem = self.resolve_event(target, date_hint)
        if problem:
            return problem
        assert event is not None
        if event.is_recurring:
            return {"status": "unsupported", "message": f"‘{event.title}’ — {RECURRING_NOTICE}"}
        snapshot, event_id = event, event.id
        fields = {"대상": event.title, "일시": self.when(event), "주의": "삭제하면 되돌리기 어렵습니다."}
        return self._save("calendar_delete", "일정 삭제", fields, lambda: self._stale(event_id, snapshot),
                          lambda: (self.calendar.delete_event(event_id), f"‘{snapshot.title}’ 일정을 삭제했습니다.")[1], "일정 삭제")

    # ----- helpers -----
    def _stale(self, event_id: str, snapshot: Event) -> str | None:
        try:
            current = self.calendar.get_event(event_id)
        except CalendarEventNotFound:
            return "대상 일정이 이미 삭제되었습니다."
        return "승인 전에 이 일정이 변경되었습니다. 최신 내용으로 다시 요청해 주세요." if _signature(current) != _signature(snapshot) else None

    def _overlaps(self, start: datetime, end: datetime, exclude: str | None = None) -> str:
        try:
            found = [e for e in self.calendar.events_between(start, end) if e.id != exclude and not e.all_day and e.starts_at < end and (e.ends_at or e.starts_at) > start]
        except Exception:
            return ""  # conflict hints are best-effort and never block a proposal
        return ", ".join(f"{e.title}({e.starts_at.astimezone(self.timezone):%H:%M})" for e in found[:3])

    @staticmethod
    def _parse_time(value: str) -> time:
        try:
            hour, minute = (int(part) for part in value.strip().split(":", maxsplit=1))
            return time(hour, minute)
        except ValueError as error:
            raise ValueError("시간은 14:00 형식으로 지정해 주세요.") from error
