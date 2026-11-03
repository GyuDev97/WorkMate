"""Google Calendar integration tests against a fake Calendar API (no network, no real account, no real events)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from server.api import dependencies
from server.main import app
from server.providers.calendar import google as google_module
from server.providers.calendar.base import CalendarNotConnected, CalendarProviderError
from server.providers.calendar.google import EVENT_SCOPE, GoogleCalendarProvider
from server.providers.calendar.token_store import FileTokenStore, KeyringTokenStore, default_token_store

KST = ZoneInfo("Asia/Seoul")
client = TestClient(app)
CACHES = ("conversation_service", "task_service", "memo_service", "local_action_service", "knowledge_service", "dashboard_service", "activity_service", "calendar_action_service",
          "calendar_tool", "calendar_provider", "mail_service", "mail_action_service", "mail_provider", "tool_registry", "llm_provider", "agent")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WORKMATE_LLM_PROVIDER", "mock")
    monkeypatch.setenv("WORKMATE_CALENDAR_PROVIDER", "mock")
    monkeypatch.setenv("WORKMATE_MAIL_PROVIDER", "none")  # tests never depend on the developer's .env mail settings
    monkeypatch.setenv("WORKMATE_TOKEN_STORE", "file")
    for name in CACHES:
        getattr(dependencies, name).cache_clear()
    yield
    monkeypatch.undo()  # restore any patched factory before clearing caches
    for name in CACHES:
        getattr(dependencies, name).cache_clear()


# ----------------------------- fake Google API -----------------------------
class FakeHttpError(Exception):
    def __init__(self, status: int, reason: str | None = None) -> None:
        self.content = json.dumps({"error": {"code": status, "message": "secret detail", "errors": [{"reason": reason}] if reason else []}}).encode()
        super().__init__(f"<HttpError {status} when requesting https://www.googleapis.com/calendar/v3/calendars/primary/events?key=SECRET-KEY returned \"boom\">")
        self.resp = SimpleNamespace(status=status)


class _Request:
    def __init__(self, fn): self._fn = fn
    def execute(self): return self._fn()


class FakeGoogle:
    """Just enough of calendar v3 `events()` to exercise the provider; records every call."""

    def __init__(self) -> None:
        self.items: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []
        self.fail: dict[str, int] = {}
        self.page_size = 1000
        self._tick = 0

    # helpers
    def add(self, event_id: str, title: str, start: datetime | str, end: datetime | str, **extra) -> dict:
        def part(value):
            if isinstance(value, str):  # an all-day date
                return {"date": value}
            return {"dateTime": value.isoformat(), "timeZone": "Asia/Seoul"}
        self._tick += 1
        item = {"id": event_id, "summary": title, "start": part(start), "end": part(end), "updated": f"2026-01-01T00:00:{self._tick:02d}Z", **extra}
        self.items[event_id] = item
        return item

    def mutations(self) -> list[str]:
        return [name for name, _ in self.calls if name in {"insert", "patch", "delete", "update"}]

    def _maybe_fail(self, op: str) -> None:
        if op in self.fail:
            code = self.fail[op]
            raise FakeHttpError(*code) if isinstance(code, tuple) else FakeHttpError(code)

    @staticmethod
    def _bounds(item: dict) -> tuple[datetime, datetime]:
        def one(value):
            if "date" in value:
                return datetime.fromisoformat(value["date"]).replace(tzinfo=KST)
            return datetime.fromisoformat(value["dateTime"])
        return one(item["start"]), one(item["end"])

    # API surface
    def events(self): return self
    def list(self, **kwargs):
        def run():
            self.calls.append(("list", kwargs)); self._maybe_fail("list")
            low, high = (datetime.fromisoformat(kwargs[k]) if kwargs.get(k) else None for k in ("timeMin", "timeMax"))
            found = []
            for item in self.items.values():
                start, end = self._bounds(item)
                if (high and start >= high) or (low and end <= low):
                    continue
                if kwargs.get("q") and not all(word in item.get("summary", "").lower() for word in kwargs["q"].lower().split()):
                    continue  # Google's `q` is a free-text match on words, not a contiguous substring
                found.append(item)
            found.sort(key=lambda i: self._bounds(i)[0])
            offset = int(kwargs.get("pageToken") or 0)
            page = found[offset: offset + self.page_size]
            body = {"items": page}
            if offset + self.page_size < len(found):
                body["nextPageToken"] = str(offset + self.page_size)
            return body
        return _Request(run)
    def get(self, **kwargs):
        def run():
            self.calls.append(("get", kwargs)); self._maybe_fail("get")
            if kwargs["eventId"] not in self.items:
                raise FakeHttpError(404)
            return self.items[kwargs["eventId"]]
        return _Request(run)
    def insert(self, **kwargs):
        def run():
            self.calls.append(("insert", kwargs)); self._maybe_fail("insert")
            event_id = f"new{len(self.items) + 1}"
            return self.add(event_id, kwargs["body"]["summary"], datetime.fromisoformat(kwargs["body"]["start"]["dateTime"]), datetime.fromisoformat(kwargs["body"]["end"]["dateTime"]))
        return _Request(run)
    def patch(self, **kwargs):
        def run():
            self.calls.append(("patch", kwargs)); self._maybe_fail("patch")
            item = self.items[kwargs["eventId"]]
            item.update(kwargs["body"])
            self._tick += 1
            item["updated"] = f"2026-01-01T00:01:{self._tick:02d}Z"
            return item
        return _Request(run)
    def delete(self, **kwargs):
        def run():
            self.calls.append(("delete", kwargs)); self._maybe_fail("delete")
            self.items.pop(kwargs["eventId"])
        return _Request(run)


class MemoryTokenStore:
    label = "memory"
    def __init__(self, value: str | None = None) -> None: self.value = value; self.writes = 0
    def read(self): return self.value
    def write(self, value): self.value = value; self.writes += 1
    def delete(self): self.value = None


def token_json(expiry: str = "2099-01-01T00:00:00Z") -> str:
    return json.dumps({"token": "access-token-value", "refresh_token": "refresh-token-value", "client_id": "cid", "client_secret": "csecret", "token_uri": "https://oauth2.googleapis.com/token", "expiry": expiry, "scopes": [EVENT_SCOPE]})


def make_provider(tmp_path, fake: FakeGoogle | None = None, token: str | None = None):
    fake = fake or FakeGoogle()
    credentials = tmp_path / "client.json"
    credentials.write_text("{}")
    store = MemoryTokenStore(token_json() if token is None else token or None)
    return GoogleCalendarProvider(credentials_path=str(credentials), token_store=store, service_factory=lambda _credentials: fake), fake, store


def use_google(monkeypatch, provider) -> None:
    monkeypatch.setattr(dependencies, "calendar_provider", lambda: provider)
    for name in ("calendar_tool", "calendar_action_service", "dashboard_service", "tool_registry", "agent"):
        getattr(dependencies, name).cache_clear()


def at(day_offset: int, hour: int, minute: int = 0) -> datetime:
    base = datetime.now(KST).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=day_offset)
    return base.replace(hour=hour, minute=minute)


def chat(message: str, conversation: str = "c") -> dict:
    response = client.post("/api/v1/chat", json={"message": message, "conversation_id": conversation})
    assert response.status_code == 200
    return response.json()


def approve(card: dict) -> dict:
    return client.post(f"/api/v1/actions/{card['action_id']}/approve").json()


def reject(card: dict) -> dict:
    return client.post(f"/api/v1/actions/{card['action_id']}/reject").json()


def activities() -> list[str]:
    return [a["action"] for a in client.get("/api/v1/dashboard").json()["activities"]]


# ----------------------------- OAuth -----------------------------
class FakeFlow:
    runs: list[dict] = []
    def __init__(self, scopes) -> None: self.scopes = scopes
    def run_local_server(self, **kwargs):
        FakeFlow.runs.append({"scopes": self.scopes, **kwargs})
        return SimpleNamespace(to_json=lambda: token_json(), valid=True, expired=False, refresh_token="r")


def test_oauth_connect_reconnect_and_disconnect(tmp_path, monkeypatch) -> None:
    from google_auth_oauthlib.flow import InstalledAppFlow
    FakeFlow.runs = []
    monkeypatch.setattr(InstalledAppFlow, "from_client_secrets_file", staticmethod(lambda path, scopes: FakeFlow(scopes)))
    revoked: list[str] = []
    monkeypatch.setattr(GoogleCalendarProvider, "_revoke", staticmethod(lambda raw: revoked.append(json.loads(raw)["refresh_token"])))
    provider, _, store = make_provider(tmp_path, token="")
    assert provider.status().state == "offline" and "연결이 필요" in provider.status().detail
    assert provider.connect().state == "connected"
    run = FakeFlow.runs[0]
    assert run["scopes"] == [EVENT_SCOPE]  # least privilege: events only, not full calendar access
    assert run["host"] == "127.0.0.1" and run["timeout_seconds"] == google_module.OAUTH_TIMEOUT_SECONDS and run["open_browser"] is True
    assert store.writes == 1 and "access-token-value" in store.value

    assert provider.disconnect().state == "offline"  # revokes the grant, then removes the local token
    assert revoked == ["refresh-token-value"] and store.value is None
    assert provider.connect().state == "connected" and len(FakeFlow.runs) == 2  # reconnect starts a fresh flow


def test_oauth_uses_pkce_s256_and_no_client_secret_in_app_code() -> None:
    from google_auth_oauthlib.flow import InstalledAppFlow
    config = {"installed": {"client_id": "cid", "client_secret": "cs", "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token", "redirect_uris": ["http://localhost"]}}
    flow = InstalledAppFlow.from_client_config(config, [EVENT_SCOPE])
    flow.redirect_uri = "http://127.0.0.1:5555/"
    url, _ = flow.authorization_url()
    assert "code_challenge=" in url and "code_challenge_method=S256" in url
    import inspect
    source = inspect.getsource(google_module)
    assert "client_secret" not in source.replace("client_secrets_file", "")  # secrets live only in the user's JSON file


def test_expired_token_is_refreshed_and_failed_refresh_asks_to_reconnect(tmp_path, monkeypatch) -> None:
    from google.oauth2.credentials import Credentials
    expired = token_json("2000-01-01T00:00:00Z")
    refreshed: list[int] = []

    def good_refresh(self, request):
        refreshed.append(1)
        self.token, self.expiry = "new-access", datetime(2099, 1, 1)

    monkeypatch.setattr(Credentials, "refresh", good_refresh)
    provider, fake, store = make_provider(tmp_path, token=expired)
    fake.add("a", "회의", at(0, 10), at(0, 11))
    assert [e.title for e in provider.today_events()] == ["회의"]
    assert refreshed and "new-access" in store.value  # the refreshed token is persisted

    def bad_refresh(self, request):
        raise RuntimeError("invalid_grant")
    monkeypatch.setattr(Credentials, "refresh", bad_refresh)
    provider, _, _ = make_provider(tmp_path, token=expired)
    with pytest.raises(CalendarNotConnected, match="다시 연결"):
        provider.today_events()


def test_token_stores(tmp_path, monkeypatch) -> None:
    class FakeKeyring:
        def __init__(self): self.data = {}
        def get_password(self, s, a): return self.data.get((s, a))
        def set_password(self, s, a, v): self.data[(s, a)] = v
        def delete_password(self, s, a):
            if (s, a) not in self.data:
                raise type("PasswordDeleteError", (Exception,), {})()
            del self.data[(s, a)]
    store = KeyringTokenStore(FakeKeyring())
    assert store.read() is None
    store.write("secret-token"); assert store.read() == "secret-token"
    store.delete(); store.delete(); assert store.read() is None  # deleting twice is fine
    import keyring
    monkeypatch.setenv("WORKMATE_TOKEN_STORE", "auto")
    usable = type("Keyring", (), {"__module__": "keyring.backends.macOS"})()
    unusable = type("Keyring", (), {"__module__": "keyring.backends.fail"})()
    monkeypatch.setattr(keyring, "get_keyring", lambda: usable)
    assert isinstance(default_token_store(tmp_path / "t.json"), KeyringTokenStore)  # the macOS backend class is literally named "Keyring"
    monkeypatch.setattr(keyring, "get_keyring", lambda: unusable)
    assert isinstance(default_token_store(tmp_path / "t.json"), FileTokenStore)  # no usable OS store: fall back to the 0600 file
    monkeypatch.setenv("WORKMATE_TOKEN_STORE", "file")
    file_store = default_token_store(tmp_path / "t.json")
    assert isinstance(file_store, FileTokenStore)
    file_store.write("x")
    assert oct((tmp_path / "t.json").stat().st_mode & 0o777) == "0o600"


# ----------------------------- reading -----------------------------
def test_normalize_all_day_timezone_recurring_and_version() -> None:
    n = lambda item: GoogleCalendarProvider.normalize({"id": "e", "summary": "x", **item}, "primary", KST)
    utc = n({"start": {"dateTime": "2026-11-05T06:00:00Z"}, "end": {"dateTime": "2026-11-05T07:00:00Z"}})
    assert (utc.starts_at.hour, utc.starts_at.tzinfo.key) == (15, "Asia/Seoul")  # 06:00Z is 15:00 in Seoul
    zoned = n({"start": {"dateTime": "2026-11-05T09:00:00", "timeZone": "America/New_York"}, "end": {"dateTime": "2026-11-05T10:00:00", "timeZone": "America/New_York"}})
    assert zoned.starts_at.astimezone(ZoneInfo("UTC")).hour == 14  # the event's own zone wins over the default
    multi = n({"start": {"date": "2026-11-05"}, "end": {"date": "2026-11-08"}})
    assert multi.all_day and multi.starts_at == datetime(2026, 11, 5, tzinfo=KST) and multi.ends_at == datetime(2026, 11, 8, tzinfo=KST)
    series = n({"start": {"date": "2026-11-05"}, "end": {"date": "2026-11-06"}, "recurringEventId": "master", "updated": "2026-01-01T00:00:00Z"})
    assert series.is_recurring and series.version == "2026-01-01T00:00:00Z"
    assert n({"start": {"date": "2026-11-05"}, "end": {"date": "2026-11-06"}, "summary": None}).title == "제목 없음"


def test_reading_tools_cover_day_week_date_search_and_detail(tmp_path, monkeypatch) -> None:
    provider, fake, _ = make_provider(tmp_path)
    monday = datetime.now(KST).date() - timedelta(days=datetime.now(KST).weekday())
    fake.add("t", "오늘 회의", at(0, 10), at(0, 11))
    fake.add("m", "내일 면담", at(1, 14), at(1, 15), description="상세 설명", location="3층")
    fake.add("d", "연차", (datetime.now(KST).date() + timedelta(days=2)).isoformat(), (datetime.now(KST).date() + timedelta(days=3)).isoformat())
    fake.add("far", "먼 일정", at(40, 9), at(40, 10))
    use_google(monkeypatch, provider)
    registry = dependencies.tool_registry()
    run = lambda name, **args: registry.execute(name, args)[0]

    assert [e["title"] for e in run("list_events", start_date="오늘", end_date=None, query="")] == ["오늘 회의"]
    assert [e["title"] for e in run("list_events", start_date="내일", end_date=None, query="")] == ["내일 면담"]
    week = run("list_events", start_date="이번 주", end_date=None, query="")
    assert all(monday <= datetime.fromisoformat(f"{e['when'][:10]}").date() <= monday + timedelta(days=6) for e in week)
    specific = (datetime.now(KST).date() + timedelta(days=2)).isoformat()
    day = run("list_events", start_date=specific, end_date=None, query="")
    assert [(e["title"], e["all_day"]) for e in day] == [("연차", True)] and "종일" in day[0]["when"]
    assert [e["title"] for e in run("list_events", start_date="오늘", end_date=(datetime.now(KST).date() + timedelta(days=45)).isoformat(), query="면담")] == ["내일 면담"]
    detail = run("get_event", target="내일 면담", date="내일")
    assert (detail.description, detail.location) == ("상세 설명", "3층")
    assert "error" in run("list_events", start_date="오늘", end_date=(datetime.now(KST).date() + timedelta(days=200)).isoformat(), query="")  # range cap
    assert fake.mutations() == []


def test_paginated_results_are_all_returned(tmp_path) -> None:
    provider, fake, _ = make_provider(tmp_path)
    fake.page_size = 2
    for i in range(5):
        fake.add(f"e{i}", f"일정{i}", at(0, 8 + i), at(0, 9 + i))
    assert len(provider.today_events()) == 5


# ----------------------------- dashboard states -----------------------------
def test_home_calendar_states_never_pass_mock_off_as_real(tmp_path, monkeypatch) -> None:
    mock = client.get("/api/v1/dashboard").json()
    assert mock["calendar"]["state"] == "mock" and "실제" in mock["calendar"]["detail"]

    provider, fake, store = make_provider(tmp_path, token="")
    use_google(monkeypatch, provider)
    body = client.get("/api/v1/dashboard").json()
    assert body["calendar"]["state"] == "not_connected" and body["events"] == [] and "연결" in body["calendar"]["detail"]
    assert "확인 불가" in body["briefing"]["overview"][0]

    store.value = token_json()
    body = client.get("/api/v1/dashboard").json()
    assert body["calendar"]["state"] == "connected" and body["events"] == []  # connected with nothing scheduled

    fake.add("a", "실제 회의", at(0, 9), at(0, 10))
    assert [e["title"] for e in client.get("/api/v1/dashboard").json()["events"]] == ["실제 회의"]

    fake.fail["list"] = 500
    body = client.get("/api/v1/dashboard").json()
    assert body["calendar"]["state"] == "error" and body["events"] == [] and "SECRET" not in json.dumps(body)
    assert client.get("/api/v1/tasks").status_code == 200  # the rest of the app keeps working


# ----------------------------- create / update / delete -----------------------------
def test_create_event_needs_approval_runs_once_and_never_before(tmp_path, monkeypatch) -> None:
    provider, fake, _ = make_provider(tmp_path)
    use_google(monkeypatch, provider)
    registry = dependencies.tool_registry()
    card, trace = registry.execute("propose_calendar_event", {"title": "기획 회의", "date": "내일", "time": "15:00", "duration_minutes": 90, "all_day": False, "description": "", "location": ""})
    assert card.kind == "calendar_event" and card.status.value == "pending_approval" and trace.risk.value == "PROPOSED_EXTERNAL_WRITE"
    tomorrow = (datetime.now(KST) + timedelta(days=1)).strftime("%Y-%m-%d")
    assert card.fields["일시"] == f"{tomorrow} 15:00 – 16:30"
    assert fake.mutations() == [] and not fake.items

    first = approve(card.model_dump())
    assert first["action"]["status"] == "completed" and "등록했습니다" in first["message"]
    assert approve(card.model_dump())["message"] == "이 작업은 이미 완료되었습니다."
    assert fake.mutations() == ["insert"]
    call = next(c for n, c in fake.calls if n == "insert")
    assert call["sendUpdates"] == "none" and call["body"]["start"]["timeZone"] == "Asia/Seoul" and call["body"]["start"]["dateTime"].endswith("+09:00")
    assert "일정 등록 승인" in activities()


def test_rejected_calendar_action_never_calls_the_api(tmp_path, monkeypatch) -> None:
    provider, fake, _ = make_provider(tmp_path)
    fake.add("a", "삭제 후보", at(1, 10), at(1, 11))
    use_google(monkeypatch, provider)
    card = chat("삭제 후보 일정을 삭제해 줘")["action"]
    assert card["kind"] == "calendar_delete" and card["fields"]["대상"] == "삭제 후보"
    assert reject(card)["action"]["status"] == "rejected"
    assert "이미 처리" in approve(card)["message"]
    assert fake.mutations() == [] and "a" in fake.items
    assert "일정 삭제 거절" in activities()


def test_update_patches_only_changed_fields_and_keeps_duration(tmp_path, monkeypatch) -> None:
    provider, fake, _ = make_provider(tmp_path)
    fake.add("a", "주간 회의", at(1, 14), at(1, 15, 30), description="안건 목록", location="회의실", attendees=[{"email": "x@example.com"}])
    use_google(monkeypatch, provider)
    card, _ = dependencies.tool_registry().execute("propose_calendar_update", {"target": "주간 회의", "date": "내일", "title": None, "new_date": None, "new_time": "16:00", "duration_minutes": None, "description": None, "location": None})
    assert "14:00" in card.fields["변경"] and "16:00" in card.fields["변경"] and card.fields["대상"] == "주간 회의"
    assert fake.mutations() == []
    assert approve(card.model_dump())["action"]["status"] == "completed"
    patch_call = next(c for n, c in fake.calls if n == "patch")
    assert set(patch_call["body"]) == {"start", "end"}  # description/location/attendees untouched (no full PUT)
    stored = fake.items["a"]
    assert stored["description"] == "안건 목록" and stored["attendees"]
    start, end = datetime.fromisoformat(stored["start"]["dateTime"]), datetime.fromisoformat(stored["end"]["dateTime"])
    assert (start.hour, end - start) == (16, timedelta(minutes=90))  # original 90-minute length preserved

    rename, _ = dependencies.tool_registry().execute("propose_calendar_update", {"target": "주간 회의", "date": "내일", "title": "주간 회의(확정)", "new_date": None, "new_time": None, "duration_minutes": None, "description": None, "location": None})
    approve(rename.model_dump())
    assert next(c for n, c in reversed(fake.calls) if n == "patch")["body"] == {"summary": "주간 회의(확정)"}
    noop, _ = dependencies.tool_registry().execute("propose_calendar_update", {"target": "주간 회의(확정)", "date": "내일", "title": "주간 회의(확정)", "new_date": None, "new_time": None, "duration_minutes": None, "description": None, "location": None})
    assert noop["status"] == "no_change"


def test_ambiguous_and_missing_events_are_not_guessed(tmp_path, monkeypatch) -> None:
    provider, fake, _ = make_provider(tmp_path)
    fake.add("a", "팀 회의", at(1, 10), at(1, 11))
    fake.add("b", "고객 회의", at(1, 14), at(1, 15))
    use_google(monkeypatch, provider)
    reply = chat("회의 일정을 삭제해 줘")
    assert reply["action"] is None and "팀 회의" in reply["message"] and "고객 회의" in reply["message"]
    registry = dependencies.tool_registry()
    result, trace = registry.execute("propose_calendar_delete", {"target": "회의", "date": "내일"})
    assert result["status"] == "needs_clarification" and len(result["candidates"]) == 2 and trace.success
    unique, _ = registry.execute("propose_calendar_delete", {"target": "고객 회의", "date": "내일"})
    assert unique.kind == "calendar_delete"
    fake.add("c", "[테스트] 기획 회의", at(1, 16), at(1, 17))
    by_words, _ = registry.execute("propose_calendar_delete", {"target": "[테스트] 회의", "date": "내일"})
    assert by_words.fields["대상"] == "[테스트] 기획 회의"  # every word matches exactly one title
    missing, trace = registry.execute("propose_calendar_delete", {"target": "없는 일정", "date": "내일"})
    assert "error" in missing and not trace.success
    assert fake.mutations() == []


def test_delete_flow_and_conflicts_at_approval_time(tmp_path, monkeypatch) -> None:
    provider, fake, _ = make_provider(tmp_path)
    fake.add("a", "정리할 일정", at(2, 10), at(2, 11))
    fake.add("b", "바뀔 일정", at(2, 13), at(2, 14))
    fake.add("c", "사라질 일정", at(2, 15), at(2, 16))
    use_google(monkeypatch, provider)
    registry = dependencies.tool_registry()
    ok, _ = registry.execute("propose_calendar_delete", {"target": "정리할 일정", "date": None})
    assert approve(ok.model_dump())["action"]["status"] == "completed" and "a" not in fake.items

    changed, _ = registry.execute("propose_calendar_update", {"target": "바뀔 일정", "date": None, "title": "내 제안", "new_date": None, "new_time": None, "duration_minutes": None, "description": None, "location": None})
    fake.patch(calendarId="primary", eventId="b", body={"summary": "다른 사람이 바꿈"}).execute()  # edited elsewhere after the proposal
    result = approve(changed.model_dump())
    assert result["action"]["status"] == "failed" and "변경되었습니다" in result["message"]
    assert fake.items["b"]["summary"] == "다른 사람이 바꿈"
    assert [name for name, _ in fake.calls if name == "patch"].count("patch") == 1  # only the outside edit, ours never ran

    gone, _ = registry.execute("propose_calendar_delete", {"target": "사라질 일정", "date": None})
    del fake.items["c"]
    outcome = approve(gone.model_dump())
    assert outcome["action"]["status"] == "failed" and "이미 삭제" in outcome["message"]
    assert "일정 삭제 실패" in activities()


def test_api_errors_are_reported_honestly_and_do_not_leak_details(tmp_path, monkeypatch) -> None:
    provider, fake, _ = make_provider(tmp_path)
    fake.add("a", "대상 일정", at(1, 10), at(1, 11))
    use_google(monkeypatch, provider)
    registry = dependencies.tool_registry()
    for status, needle in [(403, "거부"), (429, "한도"), (500, "일시적인 문제"), (404, "찾을 수 없습니다")]:
        fake.fail["patch"] = status
        card, _ = registry.execute("propose_calendar_update", {"target": "대상 일정", "date": "내일", "title": f"새 제목{status}", "new_date": None, "new_time": None, "duration_minutes": None, "description": None, "location": None})
        outcome = approve(card.model_dump())
        assert outcome["action"]["status"] == "failed" and needle in outcome["message"], (status, outcome)
        assert "변경했습니다" not in outcome["message"] and "SECRET" not in json.dumps(outcome) and "googleapis" not in json.dumps(outcome)
    assert fake.items["a"]["summary"] == "대상 일정"
    reasons = {"accessNotConfigured": "사용 설정되지 않았습니다", "insufficientPermissions": "권한 범위가 부족", "rateLimitExceeded": "한도"}
    for reason, needle in reasons.items():
        fake.fail["list"] = (403, reason)
        with pytest.raises(CalendarProviderError, match=needle) as caught:
            provider.today_events()
        assert "secret detail" not in str(caught.value) and ("calendar-json" in str(caught.value)) == (reason == "accessNotConfigured")
    del fake.fail["list"]
    fake.fail["insert"] = 500
    create, _ = registry.execute("propose_calendar_event", {"title": "실패할 일정", "date": "내일", "time": "09:00", "duration_minutes": 60, "all_day": False, "description": "", "location": ""})
    assert approve(create.model_dump())["action"]["status"] == "failed" and "일정 등록 실패" in activities()


def test_recurring_events_are_read_only_with_a_clear_notice(tmp_path, monkeypatch) -> None:
    provider, fake, _ = make_provider(tmp_path)
    fake.add("r1", "매주 스탠드업", at(1, 9), at(1, 9, 30), recurringEventId="series")
    use_google(monkeypatch, provider)
    assert dependencies.tool_registry().execute("list_events", {"start_date": "내일", "end_date": None, "query": ""})[0][0]["is_recurring"] is True
    for name, args in [("propose_calendar_update", {"target": "매주 스탠드업", "date": "내일", "title": "x", "new_date": None, "new_time": None, "duration_minutes": None, "description": None, "location": None}),
                       ("propose_calendar_delete", {"target": "매주 스탠드업", "date": "내일"})]:
        result, trace = dependencies.tool_registry().execute(name, args)
        assert result["status"] == "unsupported" and "반복 일정" in result["message"] and "지원하지 않습니다" in result["message"] and trace.success
    reply = chat("매주 스탠드업 일정을 삭제해 줘")
    assert reply["action"] is None and "반복 일정" in reply["message"]
    assert fake.mutations() == []


def test_all_day_events_move_by_date_only_and_creation_validates_input(tmp_path, monkeypatch) -> None:
    provider, fake, _ = make_provider(tmp_path)
    today = datetime.now(KST).date()
    fake.add("v", "연차", (today + timedelta(days=3)).isoformat(), (today + timedelta(days=5)).isoformat())
    use_google(monkeypatch, provider)
    registry = dependencies.tool_registry()
    bad, trace = registry.execute("propose_calendar_update", {"target": "연차", "date": None, "title": None, "new_date": None, "new_time": "10:00", "duration_minutes": None, "description": None, "location": None})
    assert "error" in bad and not trace.success
    card, _ = registry.execute("propose_calendar_update", {"target": "연차", "date": None, "title": None, "new_date": (today + timedelta(days=10)).isoformat(), "new_time": None, "duration_minutes": None, "description": None, "location": None})
    approve(card.model_dump())
    body = next(c for n, c in fake.calls if n == "patch")["body"]
    assert body["start"] == {"date": (today + timedelta(days=10)).isoformat()} and body["end"] == {"date": (today + timedelta(days=12)).isoformat()}  # 2-day span kept
    missing_time, trace = registry.execute("propose_calendar_event", {"title": "시간 없음", "date": "내일", "time": None, "duration_minutes": 60, "all_day": False, "description": "", "location": ""})
    assert "error" in missing_time and not trace.success
    past, _ = registry.execute("propose_calendar_event", {"title": "지난 일정", "date": today.isoformat(), "time": "00:01", "duration_minutes": 30, "all_day": False, "description": "", "location": ""})
    if datetime.now(KST).hour or datetime.now(KST).minute > 1:
        assert "지난 시각" in past.fields["주의"]


def test_mentioning_a_meeting_does_not_propose_and_disconnected_calendar_blocks_changes(tmp_path, monkeypatch) -> None:
    assert chat("내일 오후에 회의가 있는 것 같아")["action"] is None
    provider, fake, _ = make_provider(tmp_path, token="")
    use_google(monkeypatch, provider)
    result, trace = dependencies.tool_registry().execute("propose_calendar_event", {"title": "x", "date": "내일", "time": "10:00", "duration_minutes": 60, "all_day": False, "description": "", "location": ""})
    assert "error" in result and "연결" in result["error"] and not trace.success
    assert fake.calls == []
