from fastapi.testclient import TestClient
import pytest
from types import SimpleNamespace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from server.api import dependencies
from server.agent.agent import WorkMateAgent
from server.providers.llm.openai_provider import OpenAIProvider
from server.providers.calendar.google import GoogleCalendarProvider
from server.providers.calendar.token_store import FileTokenStore
from server.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolate_task_database(tmp_path, monkeypatch):
    """Keep API tests from writing into the desktop app's local SQLite data."""
    monkeypatch.chdir(tmp_path)
    # Tests must never depend on (or spend) a real OpenAI key from .env; the live check is a separate manual run.
    monkeypatch.setenv("WORKMATE_LLM_PROVIDER", "mock")
    monkeypatch.setenv("WORKMATE_CALENDAR_PROVIDER", "mock")
    monkeypatch.setenv("WORKMATE_MAIL_PROVIDER", "none")  # tests never depend on the developer's .env mail settings  # never touch the real Google account from tests
    monkeypatch.setenv("WORKMATE_TOKEN_STORE", "file")  # nor the real Keychain entry
    dependencies.task_service.cache_clear()
    dependencies.memo_service.cache_clear()
    dependencies.local_action_service.cache_clear()
    dependencies.conversation_service.cache_clear()
    dependencies.mail_provider.cache_clear()
    dependencies.mail_service.cache_clear()
    dependencies.mail_action_service.cache_clear()
    dependencies.knowledge_service.cache_clear()
    dependencies.dashboard_service.cache_clear()
    dependencies.activity_service.cache_clear()
    dependencies.calendar_action_service.cache_clear()
    dependencies.calendar_tool.cache_clear()
    dependencies.calendar_provider.cache_clear()
    dependencies.tool_registry.cache_clear()
    dependencies.llm_provider.cache_clear()
    dependencies.agent.cache_clear()
    yield
    dependencies.task_service.cache_clear()
    dependencies.memo_service.cache_clear()
    dependencies.local_action_service.cache_clear()
    dependencies.conversation_service.cache_clear()
    dependencies.mail_provider.cache_clear()
    dependencies.mail_service.cache_clear()
    dependencies.mail_action_service.cache_clear()
    dependencies.knowledge_service.cache_clear()
    dependencies.dashboard_service.cache_clear()
    dependencies.activity_service.cache_clear()
    dependencies.calendar_action_service.cache_clear()
    dependencies.calendar_tool.cache_clear()
    dependencies.calendar_provider.cache_clear()
    dependencies.tool_registry.cache_clear()
    dependencies.llm_provider.cache_clear()
    dependencies.agent.cache_clear()


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "workMate"}


def test_dashboard_and_chat() -> None:
    dashboard = client.get("/api/v1/dashboard")
    assert dashboard.status_code == 200
    body = dashboard.json()
    assert len(body["events"]) == 3
    assert body["mails"] == []  # no sample mail: mail is not connected yet
    assert body["activities"] == []  # no fabricated activity history on a fresh start
    mail_status = next(p for p in body["providers"] if p["name"] == "Mail")
    assert mail_status["state"] == "offline" and "연결되지 않았습니다" in mail_status["detail"]
    assert body["mail"]["state"] == "not_connected" and body["mail"]["unread_count"] is None
    response = client.post("/api/v1/chat", json={"message": "오늘 뭐 해야 돼?"})
    assert response.status_code == 200
    assert "오늘" in response.json()["message"]
    assert {item["tool"] for item in response.json()["trace"]} == {"get_today_events", "list_tasks"}
    assert "김대리" not in response.json()["message"] and "RAG" not in response.json()["message"]
    activity = client.get("/api/v1/dashboard").json()["activities"]
    assert activity and all("Mock" not in a["detail"] for a in activity)  # only real tool activity, newest first


def test_mail_is_not_connected_and_never_fabricated() -> None:
    reply = client.post("/api/v1/chat", json={"message": "김대리가 보낸 메일 찾아봐."}).json()
    assert reply["trace"][0]["tool"] == "search_mail" and reply["action"] is None and reply["ui_results"][0]["kind"] == "mail_error"
    assert "연결되어 있지 않" in reply["message"] and "김대리" not in reply["message"].replace("김대리가 보낸", "")
    registry = dependencies.tool_registry()
    for name, args in (("get_mail_status", {"query": ""}), ("get_unread_mail_count", {"query": ""}), ("search_mail", {"query": "x", "unread_only": False})):
        result, trace = registry.execute(name, args)
        if name == "get_mail_status":
            assert result.llm["connected"] is False and trace.success
        else:
            assert result.llm["reason"] == "not_connected" and not trace.success, name
    missing, trace = registry.execute("propose_mail_send", {"to": [], "cc": [], "bcc": [], "subject": "x", "body": "y"})
    assert not trace.success  # nothing to send to
    blocked, trace = registry.execute("propose_mail_send", {"to": ["a@example.com"], "cc": [], "bcc": [], "subject": "x", "body": "y"})
    assert "error" in blocked and not trace.success
    # The old mail tools that read invented data are gone.
    assert not {"get_unread_mail", "get_important_mail", "get_mail", "create_mail_draft"} & {t["name"] for t in registry.schemas()}


def test_agent_task_and_calendar_proposal() -> None:
    task = client.post("/api/v1/chat", json={"message": "RAG 테스트 결과 확인 업무를 추가해 줘."}).json()
    assert task["trace"][0]["tool"] == "propose_task_create"
    assert task["action"]["kind"] == "task_create" and task["action"]["status"] == "pending_approval"
    proposal = client.post("/api/v1/chat", json={"message": "금요일 오후 2시에 회의 등록해."}).json()
    assert proposal["action"]["kind"] == "calendar_event"
    assert proposal["action"]["status"] == "pending_approval"


def test_openai_no_tool_message_output_reaches_agent() -> None:
    """Regression: a Responses `message/output_text` must not become an empty agent reply."""
    class OutputText:
        type = "output_text"
        text = "안녕하세요. 저는 업무 일정, 메일, 할 일을 정리하는 workMate입니다."

    class Message:
        type = "message"
        content = [OutputText()]

        @staticmethod
        def model_dump(**_kwargs):
            return {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": OutputText.text}]}

    response = SimpleNamespace(output_text="", output=[Message()])
    fake_client = SimpleNamespace(responses=SimpleNamespace(create=lambda **_kwargs: response))
    provider = OpenAIProvider("test-key", "test-model", client=fake_client)
    agent = WorkMateAgent(provider, dependencies.tool_registry())
    result = agent.respond("안녕 너는 누구야", "no-tool")
    assert result.message == OutputText.text
    assert result.trace == []


def test_mock_no_tool_responses_are_direct_and_grounded() -> None:
    greeting = client.post("/api/v1/chat", json={"message": "안녕 너는 누구야"}).json()
    assert "workMate" in greeting["message"]
    assert greeting["trace"] == []
    weather = client.post("/api/v1/chat", json={"message": "내일 날씨 뭐임"}).json()
    assert "Tool이 연결되어 있지 않습니다" in weather["message"]


def test_task_lifecycle() -> None:
    created = client.post("/api/v1/tasks", json={"title": "테스트 업무", "priority": "HIGH"})
    assert created.status_code == 201
    completed = client.post(f"/api/v1/tasks/{created.json()['id']}/complete")
    assert completed.status_code == 200
    assert completed.json()["status"] == "DONE"


def test_local_folder_knowledge_collection_and_document_preview(tmp_path) -> None:
    folder = tmp_path / "documents"
    folder.mkdir()
    (folder / "notes.md").write_text("# 회의 메모\n\n다음 주까지 검토 결과를 공유합니다.", encoding="utf-8")
    (folder / ".env").write_text("SECRET=never-collect", encoding="utf-8")
    source = client.post("/api/v1/knowledge/sources", json={"name": "테스트 문서", "local_path": str(folder)})
    assert source.status_code == 201
    run = client.post(f"/api/v1/knowledge/sources/{source.json()['id']}/scan")
    assert run.status_code == 200
    # 수집은 백그라운드 작업으로 시작되므로 시작 응답이 아닌 run 조회 결과를 검증한다.
    completed = client.get(f"/api/v1/knowledge/sync-runs/{run.json()['id']}")
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert completed.json()["scanned"] == 1 and completed.json()["parsed"] == 1
    artifacts = client.get(f"/api/v1/knowledge/sources/{source.json()['id']}/artifacts").json()
    assert len(artifacts) == 1 and artifacts[0]["chunk_count"] >= 1
    preview = client.get(f"/api/v1/knowledge/artifacts/{artifacts[0]['id']}")
    assert preview.status_code == 200 and "검토 결과" in preview.json()["content"]


def test_calendar_action_approval_and_duplicate_prevention() -> None:
    proposal = client.post("/api/v1/chat", json={"message": "금요일 오후 2시에 회의 등록해."}).json()["action"]
    assert proposal["status"] == "pending_approval"
    action_id = proposal["action_id"]
    completed = client.post(f"/api/v1/actions/{action_id}/approve")
    assert completed.status_code == 200
    assert completed.json()["action"]["status"] == "completed"
    repeated = client.post(f"/api/v1/actions/{action_id}/approve")
    assert repeated.json()["message"] == "이 작업은 이미 완료되었습니다."


def test_calendar_update_and_delete_proposals_are_approval_gated() -> None:
    actions = dependencies.calendar_action_service()
    update = actions.propose_update("event-poc", None, None, None, "15:00", None, None, None)
    deletion = actions.propose_delete("event-poc", None)
    assert update.kind == "calendar_update" and update.status.value == "pending_approval"
    assert deletion.kind == "calendar_delete" and deletion.status.value == "pending_approval"


def test_google_calendar_normalizes_timed_and_all_day_events() -> None:
    timezone = ZoneInfo("Asia/Seoul")
    timed = GoogleCalendarProvider.normalize({"id": "timed", "summary": "회의", "start": {"dateTime": "2026-10-09T14:00:00+09:00"}, "end": {"dateTime": "2026-10-09T15:00:00+09:00"}}, "primary", timezone)
    all_day = GoogleCalendarProvider.normalize({"id": "day", "summary": "연차", "start": {"date": "2026-10-10"}, "end": {"date": "2026-10-11"}}, "primary", timezone)
    assert timed.starts_at.hour == 14 and timed.all_day is False
    assert all_day.starts_at == datetime(2026, 10, 10, tzinfo=timezone) and all_day.all_day is True


def test_google_calendar_without_oauth_configuration_stays_offline(tmp_path) -> None:
    provider = GoogleCalendarProvider(credentials_path=str(tmp_path / "missing.json"), token_store=FileTokenStore(tmp_path / "token.json"))
    assert provider.status().state == "offline"
    with pytest.raises(Exception, match="Google Calendar 계정을 먼저 연결"):
        provider.today_events()


def _iso(delta: timedelta) -> str:
    return (datetime.now().astimezone() + delta).isoformat()


def _task(title: str, **extra) -> dict:
    response = client.post("/api/v1/tasks", json={"title": title, **extra})
    assert response.status_code == 201
    return response.json()


def test_task_update_delete_reopen_and_validation() -> None:
    task = _task("원래 제목", description="내용", priority="LOW", due_at=_iso(timedelta(days=1)))
    updated = client.patch(f"/api/v1/tasks/{task['id']}", json={"title": "새 제목", "priority": "HIGH", "status": "IN_PROGRESS"})
    assert updated.status_code == 200
    body = updated.json()
    assert (body["title"], body["priority"], body["status"], body["description"]) == ("새 제목", "HIGH", "IN_PROGRESS", "내용")
    assert body["due_at"] is not None  # untouched fields are preserved
    assert client.patch(f"/api/v1/tasks/{task['id']}", json={"due_at": None}).json()["due_at"] is None
    assert client.patch(f"/api/v1/tasks/{task['id']}", json={"title": ""}).status_code == 422
    assert client.patch(f"/api/v1/tasks/{task['id']}", json={"status": "BOGUS"}).status_code == 422
    assert client.patch("/api/v1/tasks/missing", json={"title": "x"}).status_code == 404

    assert client.post(f"/api/v1/tasks/{task['id']}/complete").json()["status"] == "DONE"
    assert client.post(f"/api/v1/tasks/{task['id']}/reopen").json()["status"] == "TODO"

    assert client.delete(f"/api/v1/tasks/{task['id']}").status_code == 204
    assert client.delete(f"/api/v1/tasks/{task['id']}").status_code == 404
    assert client.get(f"/api/v1/tasks/{task['id']}").status_code == 404


def test_task_views_and_search() -> None:
    before = {view: len(client.get("/api/v1/tasks", params={"view": view}).json()) for view in ("all", "active", "done", "overdue")}
    late = _task("지난 보고서 쿼카", due_at=_iso(timedelta(days=-2)))
    finished = _task("끝난 일", description="쿼카 완료")
    client.post(f"/api/v1/tasks/{finished['id']}/complete")
    _task("기한 없는 일")
    ids = lambda view, **params: {task["id"] for task in client.get("/api/v1/tasks", params={"view": view, **params}).json()}
    assert late["id"] in ids("overdue") and late["id"] in ids("active") and finished["id"] not in ids("active")
    assert finished["id"] in ids("done") and finished["id"] not in ids("overdue")
    assert len(client.get("/api/v1/tasks").json()) == before["all"] + 3
    assert ids("all", q="쿼카") == {late["id"], finished["id"]}  # matches title and description
    assert ids("active", q="쿼카") == {late["id"]}
    assert client.get("/api/v1/tasks", params={"view": "nope"}).status_code == 422


def test_dashboard_counts_come_from_real_tasks() -> None:
    summary = client.get("/api/v1/dashboard").json()["briefing"]["task_summary"]
    late = _task("기한 초과 업무", priority="LOW", due_at=_iso(timedelta(days=-1)))
    today = _task("오늘 마감", priority="MEDIUM", due_at=_iso(timedelta(minutes=5)))
    urgent = _task("긴급 업무", priority="HIGH")
    done_high = _task("끝난 긴급", priority="HIGH", due_at=_iso(timedelta(days=-3)))
    client.post(f"/api/v1/tasks/{done_high['id']}/complete")
    cancelled = _task("취소됨", priority="HIGH", due_at=_iso(timedelta(days=-3)))
    client.patch(f"/api/v1/tasks/{cancelled['id']}", json={"status": "CANCELLED"})

    briefing = client.get("/api/v1/dashboard").json()["briefing"]
    now = briefing["task_summary"]
    assert now["active"] == summary["active"] + 3
    assert now["overdue"] == summary["overdue"] + 1
    assert now["high_priority"] == summary["high_priority"] + 1
    assert now["done"] == summary["done"] + 1
    reasons = {item["task_id"]: item["reason"] for item in briefing["priority_items"]}
    assert reasons[late["id"]] == "overdue"
    assert done_high["id"] not in reasons and cancelled["id"] not in reasons
    assert list(reasons)[0] == late["id"]  # overdue ranks first
    assert briefing["priorities"][0] == "기한 초과 업무"
    assert "진행 중인 업무" in briefing["overview"][1]
    assert today["id"] and urgent["id"]


def test_memo_crud_search_and_task_link() -> None:
    task = _task("연결할 업무")
    created = client.post("/api/v1/memos", json={"title": "회의 메모", "content": "RAG 100% 일정_확인", "task_id": task["id"]})
    assert created.status_code == 201
    memo = created.json()
    assert memo["task_title"] == "연결할 업무" and memo["created_at"] == memo["updated_at"]
    other = client.post("/api/v1/memos", json={"title": "아이디어"}).json()
    assert other["content"] == "" and other["task_id"] is None

    assert {m["id"] for m in client.get("/api/v1/memos", params={"q": "회의"}).json()} == {memo["id"]}
    assert {m["id"] for m in client.get("/api/v1/memos", params={"q": "일정_"}).json()} == {memo["id"]}
    assert client.get("/api/v1/memos", params={"q": "%"}).json()[0]["id"] == memo["id"]  # wildcard is literal
    assert client.get("/api/v1/memos", params={"q": "100% 일"}).json()[0]["id"] == memo["id"]
    assert client.get("/api/v1/memos", params={"q": "없는단어"}).json() == []
    assert {m["id"] for m in client.get("/api/v1/memos", params={"task_id": task["id"]}).json()} == {memo["id"]}

    edited = client.patch(f"/api/v1/memos/{memo['id']}", json={"content": "수정됨"}).json()
    assert edited["content"] == "수정됨" and edited["title"] == "회의 메모" and edited["task_id"] == task["id"]
    assert edited["updated_at"] > memo["updated_at"] and edited["created_at"] == memo["created_at"]
    assert client.patch(f"/api/v1/memos/{memo['id']}", json={"task_id": None}).json()["task_id"] is None
    assert client.patch(f"/api/v1/memos/{memo['id']}", json={"task_id": "missing"}).status_code == 422
    assert client.post("/api/v1/memos", json={"title": "x", "task_id": "missing"}).status_code == 422
    assert client.post("/api/v1/memos", json={"title": ""}).status_code == 422

    assert client.delete(f"/api/v1/memos/{memo['id']}").status_code == 204
    assert client.get(f"/api/v1/memos/{memo['id']}").status_code == 404
    assert client.delete(f"/api/v1/memos/{memo['id']}").status_code == 404


def test_deleting_a_task_keeps_its_memos() -> None:
    task = _task("곧 삭제")
    memo = client.post("/api/v1/memos", json={"title": "남는 메모", "task_id": task["id"]}).json()
    client.delete(f"/api/v1/tasks/{task['id']}")
    kept = client.get(f"/api/v1/memos/{memo['id']}").json()
    assert kept["task_id"] is None and kept["task_title"] is None


def test_data_persists_across_service_restart_and_legacy_tasks_load() -> None:
    import sqlite3
    task = _task("재시작 확인")
    memo = client.post("/api/v1/memos", json={"title": "재시작 메모", "content": "본문"}).json()
    # Simulate an app restart: drop every cached service so repositories reopen the same SQLite file.
    for cached in (dependencies.task_service, dependencies.memo_service, dependencies.dashboard_service):
        cached.cache_clear()
    assert client.get(f"/api/v1/tasks/{task['id']}").json()["title"] == "재시작 확인"
    assert client.get(f"/api/v1/memos/{memo['id']}").json()["content"] == "본문"
    # A row written by the pre-CRUD version (naive timestamp, no migration) still loads and is classified.
    with sqlite3.connect("data/workmate.db") as db:
        db.execute("INSERT INTO tasks (id, title, description, priority, due_at, status, source, created_at, updated_at) VALUES ('legacy', '예전 업무', '', 'MEDIUM', '2020-01-01T09:00:00', 'TODO', 'mock', '2020-01-01T00:00:00+00:00', '2020-01-01T00:00:00+00:00')")
    assert "legacy" in {t["id"] for t in client.get("/api/v1/tasks", params={"view": "overdue"}).json()}


# ---------------------------------------------------------------------------
# Assistant Task/Memo tools: read tools run directly, every change is an approval-gated Action Card.
# The Mock provider only understands a few fixed phrasings; these tests verify the application-side
# guarantees (approval, rejection, idempotency, ambiguity, conflicts), not natural-language understanding.
# ---------------------------------------------------------------------------
from server.services.dates import SEOUL, parse_due  # noqa: E402


def _chat(message: str, conversation: str = "t") -> dict:
    response = client.post("/api/v1/chat", json={"message": message, "conversation_id": conversation})
    assert response.status_code == 200
    return response.json()


def _titles() -> list[str]:
    return [t["title"] for t in client.get("/api/v1/tasks").json()]


def _task_by_title(title: str) -> dict | None:
    return next((t for t in client.get("/api/v1/tasks").json() if t["title"] == title), None)


def _snapshot() -> tuple[list, list]:
    return client.get("/api/v1/tasks").json(), client.get("/api/v1/memos").json()


def _approve(action: dict) -> dict:
    response = client.post(f"/api/v1/actions/{action['action_id']}/approve")
    assert response.status_code == 200
    return response.json()


def _reject(action: dict) -> dict:
    response = client.post(f"/api/v1/actions/{action['action_id']}/reject")
    assert response.status_code == 200
    return response.json()


def _activity_actions() -> list[str]:
    return [a["action"] for a in client.get("/api/v1/dashboard").json()["activities"]]


def test_task_read_tools_need_no_approval_and_change_nothing() -> None:
    end_of_today = datetime.now(SEOUL).replace(hour=23, minute=59, second=0, microsecond=0)
    _task("오늘 끝낼 쿼카 업무", priority="HIGH", due_at=end_of_today.isoformat())
    _task("지난 쿼카 업무", priority="LOW", due_at=_iso(timedelta(days=-3)))
    before = _snapshot()
    for text, view, expected in [
        ("오늘 마감인 업무 알려줘", "due_today", "오늘 끝낼 쿼카 업무"),
        ("기한 지난 업무 찾아줘", "overdue", "지난 쿼카 업무"),
        ("우선순위 높은 업무 알려줘", "high_priority", "오늘 끝낼 쿼카 업무"),
        ("진행 중인 업무 보여줘", "active", "지난 쿼카 업무"),
    ]:
        reply = _chat(text)
        assert reply["trace"][0]["tool"] == "list_tasks" and reply["trace"][0]["risk"] == "READ"
        assert reply["action"] is None and expected in reply["message"], (text, reply["message"])
    assert _snapshot() == before
    assert "지난 쿼카 업무" not in _chat("오늘 마감인 업무 알려줘")["message"]


def test_task_create_requires_approval_and_is_not_duplicated() -> None:
    before = _titles()
    reply = _chat("내일 마감으로 신규 보고서 작성 업무를 추가해 줘")
    card = reply["action"]
    assert card["kind"] == "task_create" and card["status"] == "pending_approval"
    assert card["fields"]["제목"] == "신규 보고서 작성" and "승인 전" in reply["message"]
    assert _titles() == before  # proposing never writes

    summary_before = client.get("/api/v1/dashboard").json()["briefing"]["task_summary"]
    result = _approve(card)
    assert result["action"]["status"] == "completed" and "추가했습니다" in result["message"]
    created = _task_by_title("신규 보고서 작성")
    due = datetime.fromisoformat(created["due_at"]).astimezone(SEOUL)
    assert due.date() == (datetime.now(SEOUL) + timedelta(days=1)).date() and (due.hour, due.minute) == (23, 59)
    assert created["source"] == "agent"
    assert client.get("/api/v1/dashboard").json()["briefing"]["task_summary"]["active"] == summary_before["active"] + 1

    again = _approve(card)  # duplicate approval must not create a second task
    assert "이미 완료" in again["message"]
    assert _titles().count("신규 보고서 작성") == 1
    actions = _activity_actions()
    assert "업무 추가 승인" in actions


def test_rejected_action_changes_nothing_and_cannot_be_approved_later() -> None:
    task = _task("거절 대상 업무", priority="LOW")
    card = _chat("거절 대상 업무를 삭제해 줘")["action"]
    assert card["kind"] == "task_delete"
    before = _snapshot()
    rejected = _reject(card)
    assert rejected["action"]["status"] == "rejected" and "변경되지 않았습니다" in rejected["message"]
    assert _snapshot() == before
    assert "이미 처리" in _approve(card)["message"]  # a rejected card can never run
    assert client.get(f"/api/v1/tasks/{task['id']}").status_code == 200
    assert "업무 삭제 거절" in _activity_actions()
    assert client.post("/api/v1/actions/unknown/approve").status_code == 404
    assert client.post("/api/v1/actions/unknown/reject").status_code == 404


def test_task_update_complete_and_delete_flows() -> None:
    task = _task("흐름 업무", priority="MEDIUM", due_at=_iso(timedelta(days=1)))
    update = _chat("흐름 업무의 마감일을 금요일로 변경해 줘")["action"]
    assert update["kind"] == "task_update" and "마감" in update["fields"]["변경"] and update["fields"]["대상"] == "흐름 업무"
    assert client.get(f"/api/v1/tasks/{task['id']}").json()["due_at"] == task["due_at"]  # untouched until approval
    _approve(update)
    expected = parse_due("금요일").astimezone(SEOUL)
    assert datetime.fromisoformat(client.get(f"/api/v1/tasks/{task['id']}").json()["due_at"]).astimezone(SEOUL) == expected

    complete = _chat("흐름 업무를 완료 처리해 줘")["action"]
    assert complete["kind"] == "task_complete"
    assert client.get(f"/api/v1/tasks/{task['id']}").json()["status"] == "TODO"
    _approve(complete)
    assert client.get(f"/api/v1/tasks/{task['id']}").json()["status"] == "DONE"

    delete = _chat("흐름 업무를 삭제해 줘")["action"]
    assert delete["kind"] == "task_delete" and "복구할 수 없습니다" in delete["fields"]["주의"]
    assert client.get(f"/api/v1/tasks/{task['id']}").status_code == 200
    _approve(delete)
    assert client.get(f"/api/v1/tasks/{task['id']}").status_code == 404
    assert {"업무 수정 승인", "업무 완료 처리 승인", "업무 삭제 승인"} <= set(_activity_actions())


def test_ambiguous_target_is_never_chosen_for_the_user() -> None:
    _task("보고서 초안 A")
    _task("보고서 초안 B")
    before = _snapshot()
    reply = _chat("보고서 초안 업무를 삭제해 줘")
    assert reply["action"] is None
    assert "보고서 초안 A" in reply["message"] and "보고서 초안 B" in reply["message"] and "어느 것" in reply["message"]
    assert _snapshot() == before
    result, trace = dependencies.tool_registry().execute("propose_task_update", {"target": "보고서 초안", "title": None, "description": None, "priority": None, "due": None, "clear_due": False, "status": "DONE"})
    assert result["status"] == "needs_clarification" and len(result["candidates"]) == 2 and trace.success
    # An exact title among near-duplicates is unambiguous; an unknown title is an error, not a guess.
    exact, _ = dependencies.tool_registry().execute("propose_task_delete", {"target": "보고서 초안 A"})
    assert exact.kind == "task_delete"
    missing, trace = dependencies.tool_registry().execute("propose_task_delete", {"target": "존재하지 않는 업무"})
    assert "error" in missing and not trace.success


def test_approval_is_blocked_when_target_changed_after_proposal() -> None:
    task = _task("충돌 업무", priority="LOW")
    card = _chat("충돌 업무의 마감일을 금요일로 변경해 줘")["action"]
    client.patch(f"/api/v1/tasks/{task['id']}", json={"title": "다른 사람이 바꾼 제목"})
    result = _approve(card)
    assert result["action"]["status"] == "failed" and "변경되었습니다" in result["message"]
    current = client.get(f"/api/v1/tasks/{task['id']}").json()
    assert current["title"] == "다른 사람이 바꾼 제목" and current["due_at"] is None  # proposal was not applied
    assert "업무 수정 실패" in _activity_actions()
    assert "이미 처리" in _approve(card)["message"]  # failed cards are final

    gone = _task("사라질 업무")
    delete = _chat("사라질 업무를 삭제해 줘")["action"]
    client.delete(f"/api/v1/tasks/{gone['id']}")
    outcome = _approve(delete)
    assert outcome["action"]["status"] == "failed" and "이미 삭제" in outcome["message"]


def test_memo_read_tools_and_full_approval_lifecycle() -> None:
    task = _task("연결용 업무")
    client.post("/api/v1/memos", json={"title": "RAG 설계 메모", "content": "청크 크기는 1500자"})
    client.post("/api/v1/memos", json={"title": "점심 메뉴", "content": "김치찌개"})
    before = _snapshot()
    found = _chat("RAG 관련 메모 찾아줘")
    assert found["trace"][0]["tool"] == "list_memos" and "RAG 설계 메모" in found["message"] and "점심 메뉴" not in found["message"]
    assert "점심 메뉴" in _chat("최근 작성한 메모 보여줘")["message"]
    assert _snapshot() == before
    full, trace = dependencies.tool_registry().execute("get_memo", {"target": "RAG 설계 메모"})
    assert full.content == "청크 크기는 1500자" and trace.risk.value == "READ"

    registry = dependencies.tool_registry()
    create, trace = registry.execute("propose_memo_create", {"title": "회의 결과", "content": "다음 주 월요일 재논의", "task_target": "연결용 업무"})
    assert create.kind == "memo_create" and trace.risk.value == "PROPOSED_LOCAL_WRITE" and create.fields["연결 업무"] == "연결용 업무"
    assert len(client.get("/api/v1/memos").json()) == 2  # nothing saved yet
    assert "저장했습니다" in _approve(create.model_dump())["message"]
    memo = next(m for m in client.get("/api/v1/memos").json() if m["title"] == "회의 결과")
    assert memo["task_id"] == task["id"] and memo["content"] == "다음 주 월요일 재논의"
    assert "저장했습니다" not in _approve(create.model_dump())["message"]
    assert len(client.get("/api/v1/memos").json()) == 3

    rename, _ = registry.execute("propose_memo_update", {"target": "회의 결과", "title": "회의 결과(확정)", "content": None, "task_target": None, "unlink_task": False})
    assert rename.kind == "memo_update" and "회의 결과 → 회의 결과(확정)" in rename.fields["변경"]
    assert client.get(f"/api/v1/memos/{memo['id']}").json()["title"] == "회의 결과"
    _approve(rename.model_dump())
    assert client.get(f"/api/v1/memos/{memo['id']}").json()["title"] == "회의 결과(확정)"

    unlink, _ = registry.execute("propose_memo_update", {"target": memo["id"], "title": None, "content": None, "task_target": None, "unlink_task": True})
    _approve(unlink.model_dump())
    assert client.get(f"/api/v1/memos/{memo['id']}").json()["task_id"] is None
    link, _ = registry.execute("propose_memo_update", {"target": memo["id"], "title": None, "content": None, "task_target": "연결용 업무", "unlink_task": False})
    _approve(link.model_dump())
    assert client.get(f"/api/v1/memos/{memo['id']}").json()["task_id"] == task["id"]
    noop, _ = registry.execute("propose_memo_update", {"target": memo["id"], "title": None, "content": None, "task_target": "연결용 업무", "unlink_task": False})
    assert noop["status"] == "no_change"

    delete = _chat("회의 결과(확정) 메모를 삭제해 줘")["action"]
    assert delete["kind"] == "memo_delete"
    assert client.get(f"/api/v1/memos/{memo['id']}").status_code == 200
    _reject(delete)
    assert client.get(f"/api/v1/memos/{memo['id']}").status_code == 200
    delete = _chat("회의 결과(확정) 메모를 삭제해 줘")["action"]
    _approve(delete)
    assert client.get(f"/api/v1/memos/{memo['id']}").status_code == 404
    assert {"메모 작성 승인", "메모 수정 승인", "메모 삭제 거절", "메모 삭제 승인"} <= set(_activity_actions())


def test_memo_ambiguity_conflicts_and_failures_are_reported_honestly() -> None:
    first = client.post("/api/v1/memos", json={"title": "주간 회의 A", "content": "a"}).json()
    client.post("/api/v1/memos", json={"title": "주간 회의 B", "content": "b"})
    reply = _chat("주간 회의 메모를 삭제해 줘")
    assert reply["action"] is None and "주간 회의 A" in reply["message"] and "주간 회의 B" in reply["message"]
    assert len(client.get("/api/v1/memos").json()) == 2

    registry = dependencies.tool_registry()
    edit, _ = registry.execute("propose_memo_update", {"target": first["id"], "title": None, "content": "새 내용", "task_target": None, "unlink_task": False})
    client.patch(f"/api/v1/memos/{first['id']}", json={"content": "동시 수정"})
    outcome = _approve(edit.model_dump())
    assert outcome["action"]["status"] == "failed" and client.get(f"/api/v1/memos/{first['id']}").json()["content"] == "동시 수정"

    task = _task("곧 사라질 업무")
    create, _ = registry.execute("propose_memo_create", {"title": "고아 메모", "content": "x", "task_target": task["id"]})
    client.delete(f"/api/v1/tasks/{task['id']}")
    failed = _approve(create.model_dump())
    assert failed["action"]["status"] == "failed" and "저장했습니다" not in failed["message"]
    assert not [m for m in client.get("/api/v1/memos").json() if m["title"] == "고아 메모"]
    assert "메모 작성 실패" in _activity_actions()

    assert "제목과 내용" in _chat("새 메모 작성해 줘")["message"]  # Mock never invents memo content
    bad, trace = registry.execute("propose_memo_create", {"title": "", "content": "", "task_target": None})
    assert "error" in bad and not trace.success


def test_due_date_parsing_uses_korean_time_and_the_request_time() -> None:
    wednesday = datetime(2026, 11, 4, 22, 30, tzinfo=SEOUL)  # a Wednesday, late evening in Seoul
    assert wednesday.weekday() == 2
    assert parse_due("내일", wednesday) == datetime(2026, 11, 5, 23, 59, tzinfo=SEOUL)
    assert parse_due("오늘", wednesday).date() == wednesday.date()
    assert parse_due("모레 09:30", wednesday) == datetime(2026, 11, 6, 9, 30, tzinfo=SEOUL)
    assert parse_due("금요일", wednesday).date().isoformat() == "2026-11-06"
    assert parse_due("수요일", wednesday).date().isoformat() == "2026-11-11"  # same weekday means next week
    assert parse_due("다음 주 월요일", wednesday).date().isoformat() == "2026-11-09"
    assert parse_due("이번 주 금요일", wednesday).date().isoformat() == "2026-11-06"
    assert parse_due("2026-12-25", wednesday) == datetime(2026, 12, 25, 23, 59, tzinfo=SEOUL)
    assert parse_due("2026-12-25T10:00", wednesday).utcoffset() == timedelta(hours=9)
    assert parse_due("2026-12-25T10:00:00+00:00", wednesday) == datetime(2026, 12, 25, 19, 0, tzinfo=SEOUL)
    # A UTC server clock that is still "yesterday" must not shift Seoul's tomorrow.
    utc_clock = datetime(2026, 11, 4, 16, 0, tzinfo=ZoneInfo("UTC"))  # = 2026-11-05 01:00 KST
    assert parse_due("내일", utc_clock).date().isoformat() == "2026-11-06"
    for bad in ("이번 주 월요일", "다음달", "내일 25:00", ""):
        with pytest.raises(ValueError):
            parse_due(bad, wednesday)


def test_openai_provider_tool_calling_reuses_the_same_approval_flow() -> None:
    """Provider plumbing only: a scripted Responses payload stands in for the model's tool choice."""
    import json as _json

    class Call:
        type = "function_call"

        def __init__(self, name: str, arguments: dict) -> None:
            self.call_id, self.name, self.arguments = "call-1", name, _json.dumps(arguments, ensure_ascii=False)
            self._dump = {"type": "function_call", "call_id": self.call_id, "name": name, "arguments": self.arguments}

        def model_dump(self, **_kwargs):
            return self._dump

    class Message:
        type = "message"

        def __init__(self, text: str) -> None:
            self.content = [SimpleNamespace(type="output_text", text=text)]
            self._text = text

        def model_dump(self, **_kwargs):
            return {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": self._text}]}

    seen: list[dict] = []
    args = {"title": "OpenAI 경로 업무", "description": "", "priority": "HIGH", "due": "내일"}
    scripted = [SimpleNamespace(output_text="", output=[Call("propose_task_create", args)]), SimpleNamespace(output_text="", output=[Message("승인 카드를 준비했습니다.")])]

    def create(**kwargs):
        seen.append(kwargs)
        return scripted[len(seen) - 1]

    agent = WorkMateAgent(OpenAIProvider("k", "m", client=SimpleNamespace(responses=SimpleNamespace(create=create))), dependencies.tool_registry())
    reply = agent.respond("내일까지 OpenAI 경로 업무 추가해줘", "openai-flow")
    assert reply.action and reply.action.kind == "task_create" and reply.metadata["provider"] == "openai"
    assert _task_by_title("OpenAI 경로 업무") is None
    assert "Asia/Seoul" in seen[0]["instructions"] and "요일" in seen[0]["instructions"]
    names = {tool["name"] for tool in seen[0]["tools"]}
    assert {"list_tasks", "list_memos", "get_memo", "propose_task_create", "propose_task_update", "propose_task_delete", "propose_memo_create", "propose_memo_update", "propose_memo_delete"} <= names
    assert "create_task" not in names and "complete_task" not in names  # no tool can write without approval
    def keys(node):
        if isinstance(node, dict):
            for key, value in node.items():
                yield key
                yield from keys(value)
        elif isinstance(node, list):
            for item in node:
                yield from keys(item)

    for tool in seen[0]["tools"]:
        assert tool["parameters"]["required"] == list(tool["parameters"]["properties"]), tool["name"]
        assert "default" not in set(keys(tool["parameters"])), tool["name"]  # live OpenAI rejects `$ref` + `default`
    _approve(reply.action.model_dump())
    assert _task_by_title("OpenAI 경로 업무")["priority"] == "HIGH"


def test_calendar_actions_can_be_rejected_without_changing_the_calendar() -> None:
    before = len(client.get("/api/v1/dashboard").json()["events"])
    card = client.post("/api/v1/chat", json={"message": "금요일 오후 2시에 회의 등록해."}).json()["action"]
    rejected = _reject(card)
    assert rejected["action"]["status"] == "rejected"
    assert "이미 처리" in _approve(card)["message"] or "실행할 수 없는" in _approve(card)["message"]
    assert len(client.get("/api/v1/dashboard").json()["events"]) == before


# ---------------------------------------------------------------------------
# Task period (start_at / due_at): both optional, start <= due, additive migration, approval-gated via Assistant.
# ---------------------------------------------------------------------------
def _card(response: dict) -> dict:
    assert response["action"], response["message"]
    return response["action"]


def _db_task(task_id: str) -> tuple:
    import sqlite3
    with sqlite3.connect("data/workmate.db") as db:
        return db.execute("SELECT title, start_at, due_at FROM tasks WHERE id = ?", (task_id,)).fetchone()


def test_task_period_create_combinations() -> None:
    none = client.post("/api/v1/tasks", json={"title": "기간 없음"}).json()
    only_start = client.post("/api/v1/tasks", json={"title": "시작만", "start_at": "2026-11-05T00:00:00+09:00"}).json()
    only_due = client.post("/api/v1/tasks", json={"title": "종료만", "due_at": "2026-11-10T23:59:00+09:00"}).json()
    both = client.post("/api/v1/tasks", json={"title": "둘 다", "start_at": "2026-11-05T00:00:00+09:00", "due_at": "2026-11-10T23:59:00+09:00"}).json()
    assert (none["start_at"], none["due_at"]) == (None, None)
    assert only_start["start_at"] and only_start["due_at"] is None
    assert only_due["start_at"] is None and only_due["due_at"]
    assert both["start_at"] and both["due_at"]
    stored = _db_task(both["id"])
    assert stored[1] is not None and stored[2] is not None and _db_task(none["id"])[1:] == (None, None)


def test_task_period_rejects_start_after_due() -> None:
    bad = client.post("/api/v1/tasks", json={"title": "역전", "start_at": "2026-11-11T00:00:00+09:00", "due_at": "2026-11-10T00:00:00+09:00"})
    assert bad.status_code == 422
    task = client.post("/api/v1/tasks", json={"title": "부분 수정", "due_at": "2026-11-10T23:59:00+09:00"}).json()
    # Moving only the start past the stored due date is rejected and leaves the task untouched.
    late = client.patch(f"/api/v1/tasks/{task['id']}", json={"start_at": "2026-12-01T00:00:00+09:00"})
    assert late.status_code == 422
    assert client.get(f"/api/v1/tasks/{task['id']}").json()["start_at"] is None
    both = client.patch(f"/api/v1/tasks/{task['id']}", json={"start_at": "2026-11-12T00:00:00+09:00", "due_at": "2026-11-20T00:00:00+09:00"})
    assert both.status_code == 200


def test_task_period_update_and_individual_clear() -> None:
    task = client.post("/api/v1/tasks", json={"title": "개별 삭제", "start_at": "2026-11-05T00:00:00+09:00", "due_at": "2026-11-10T23:59:00+09:00"}).json()
    cleared_start = client.patch(f"/api/v1/tasks/{task['id']}", json={"start_at": None}).json()
    assert cleared_start["start_at"] is None and cleared_start["due_at"] is not None
    cleared_due = client.patch(f"/api/v1/tasks/{task['id']}", json={"start_at": "2026-11-06T00:00:00+09:00", "due_at": None}).json()
    assert cleared_due["start_at"] is not None and cleared_due["due_at"] is None
    untouched = client.patch(f"/api/v1/tasks/{task['id']}", json={"title": "제목만"}).json()
    assert untouched["start_at"] == cleared_due["start_at"]


def test_task_start_migration_keeps_existing_rows() -> None:
    import sqlite3
    from server.database.repository import TaskRepository
    import os
    os.makedirs("data", exist_ok=True)
    path = "data/migrate_old.db"
    if os.path.exists(path):
        os.remove(path)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL, priority TEXT NOT NULL, due_at TEXT, status TEXT NOT NULL, source TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        db.execute("INSERT INTO tasks VALUES ('old1', '기존 업무', '설명', 'HIGH', '2026-11-10T23:59:00+09:00', 'TODO', 'manual', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')")
    try:
        repo = TaskRepository(path)
        TaskRepository(path)  # a second start must not fail or change anything
        task = repo.get("old1")
        assert task and task.title == "기존 업무" and task.start_at is None and task.due_at is not None and task.priority.value == "HIGH"
        assert len(repo.list()) == 1
    finally:
        os.remove(path)


def test_assistant_task_period_requires_approval_and_runs_once() -> None:
    before = len(client.get("/api/v1/tasks").json())
    response = _chat("2026-11-05부터 2026-11-10까지 보고서 작성 업무 등록해 줘")
    card = _card(response)
    assert card["kind"] == "task_create"
    assert card["fields"]["시작일"].startswith("2026-11-05") and card["fields"]["종료일(마감)"].startswith("2026-11-10")
    assert len(client.get("/api/v1/tasks").json()) == before  # nothing saved before approval
    _approve(card)
    _approve(card)  # duplicate approval must not run it again
    created = [t for t in client.get("/api/v1/tasks").json() if t["title"] == "보고서 작성"]
    assert len(created) == 1 and created[0]["start_at"] and created[0]["due_at"]
    assert len(client.get("/api/v1/tasks").json()) == before + 1


def test_assistant_end_only_never_invents_a_start_and_update_start() -> None:
    card = _card(_chat("2026-12-04까지 완료할 분기 정리 업무 등록해 줘"))
    assert card["fields"]["시작일"] == "시작일 없음"
    _approve(card)
    task = next(t for t in client.get("/api/v1/tasks").json() if t["title"] == "분기 정리")
    assert task["start_at"] is None and task["due_at"]
    change = _card(_chat("분기 정리 업무의 시작일을 2026-12-01로 변경해 줘"))
    assert _db_task(task["id"])[1] is None  # still unchanged before approval
    _approve(change)
    assert _db_task(task["id"])[1] is not None
    remove = _card(_chat("분기 정리 업무의 시작일을 없애 줘"))
    assert _db_task(task["id"])[1] is not None
    _approve(remove)
    assert _db_task(task["id"])[1] is None and _db_task(task["id"])[2] is not None


def test_assistant_rejects_start_after_end_without_card() -> None:
    response = _chat("2026-12-10부터 2026-12-05까지 역전 업무 등록해 줘")
    assert response["action"] is None
    assert not [t for t in client.get("/api/v1/tasks").json() if t["title"] == "역전"]
