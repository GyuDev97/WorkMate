from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

SEOUL = ZoneInfo("Asia/Seoul")
WEEKDAYS = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}
DEFAULT_DUE_TIME = time(23, 59)


def parse_due(text: str, now: datetime | None = None) -> datetime:
    """Interpret a due date in Asia/Seoul. Accepts ISO dates/datetimes and simple Korean relative words.

    A date without a time means end of that day (23:59). A bare weekday means the next such day after today.
    """
    now = (now or datetime.now(SEOUL)).astimezone(SEOUL)
    value = text.strip()
    if not value:
        raise ValueError("마감일이 비어 있습니다.")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        day = date.fromisoformat(value)
        return datetime.combine(day, DEFAULT_DUE_TIME, SEOUL)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        parsed = None
    if parsed is not None:
        return (parsed.replace(tzinfo=SEOUL) if parsed.tzinfo is None else parsed).astimezone(SEOUL)

    match = re.fullmatch(r"(?P<day>(?:이번 주|다음 주)\s+\S+|\S+)(?:\s+(?P<h>\d{1,2}):(?P<m>\d{2}))?", value)
    if not match:
        raise ValueError(f"마감일을 해석하지 못했습니다: {text}")
    day_text, hour, minute = match["day"], match["h"], match["m"]
    today = now.date()
    fixed = {"오늘": 0, "내일": 1, "모레": 2}
    if day_text in fixed:
        target = today + timedelta(days=fixed[day_text])
    else:
        weekday = re.fullmatch(r"(?:(?P<scope>이번 주|다음 주)\s+)?(?P<wd>[월화수목금토일])요일", day_text)
        if not weekday:
            raise ValueError(f"마감일을 해석하지 못했습니다: {text}")
        index = WEEKDAYS[weekday["wd"]]
        monday = today - timedelta(days=today.weekday())
        if weekday["scope"] == "이번 주":
            target = monday + timedelta(days=index)
            if target < today:
                raise ValueError("이번 주의 지난 요일입니다. 날짜(YYYY-MM-DD)로 지정해 주세요.")
        elif weekday["scope"] == "다음 주":
            target = monday + timedelta(days=7 + index)
        else:
            target = today + timedelta(days=(index - today.weekday()) % 7 or 7)
    due_time = DEFAULT_DUE_TIME if hour is None else time(int(hour), int(minute))
    return datetime.combine(target, due_time, SEOUL)


def parse_start(text: str, now: datetime | None = None) -> datetime:
    """A start date in Asia/Seoul. Same formats as parse_due, but a date without a time means the start of that day (00:00)."""
    value = text.strip()
    if not value:
        raise ValueError("시작일이 비어 있습니다.")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return datetime.combine(date.fromisoformat(value), time(0, 0), SEOUL)
    parsed = parse_due(value, now)
    return parsed if re.search(r"\d{1,2}:\d{2}", value) else parsed.replace(hour=0, minute=0)


def parse_day(text: str, now: datetime | None = None) -> date:
    """A calendar day in Asia/Seoul: YYYY-MM-DD, 오늘/내일/모레, or [이번 주|다음 주] 요일 (a bare weekday is the next one after today)."""
    value = text.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return date.fromisoformat(value)
    now = (now or datetime.now(SEOUL)).astimezone(SEOUL)
    today = now.date()
    fixed = {"오늘": 0, "내일": 1, "모레": 2}
    if value in fixed:
        return today + timedelta(days=fixed[value])
    weekday = re.fullmatch(r"(?:(?P<scope>이번 주|다음 주)\s+)?(?P<wd>[월화수목금토일])요일", value)
    if not weekday:
        raise ValueError(f"날짜를 해석하지 못했습니다: {text}")
    index = WEEKDAYS[weekday["wd"]]
    monday = today - timedelta(days=today.weekday())
    if weekday["scope"] == "이번 주":
        return monday + timedelta(days=index)
    if weekday["scope"] == "다음 주":
        return monday + timedelta(days=7 + index)
    return today + timedelta(days=(index - today.weekday()) % 7 or 7)


def parse_day_range(start: str, end: str | None = None, now: datetime | None = None, max_days: int = 62) -> tuple[date, date]:
    """Inclusive day range. '이번 주'/'다음 주' mean Monday through Sunday."""
    now = (now or datetime.now(SEOUL)).astimezone(SEOUL)
    monday = now.date() - timedelta(days=now.date().weekday())
    if start.strip() in ("이번 주", "다음 주"):
        first = monday + timedelta(days=7 if start.strip() == "다음 주" else 0)
        return first, first + timedelta(days=6)
    first = parse_day(start, now)
    last = parse_day(end, now) if end and end.strip() else first
    if last < first:
        raise ValueError("종료 날짜가 시작 날짜보다 빠릅니다.")
    if (last - first).days >= max_days:
        raise ValueError(f"조회 범위는 최대 {max_days}일입니다.")
    return first, last
