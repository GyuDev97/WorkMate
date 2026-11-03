"""Assistant conversation persistence: store, restore after a 'restart', expire stale cards, bounded context, delete, redact."""
from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from server.agent.contracts import ProviderTurn
from server.agent.tool_registry import ToolRegistry
from server.api import dependencies
from server.main import app

client = TestClient(app)
CACHES = ("conversation_service", "mail_provider", "mail_service", "mail_action_service", "task_service", "memo_service", "local_action_service", "knowledge_service", "dashboard_service", "activity_service",
          "calendar_action_service", "calendar_tool", "calendar_provider", "tool_registry", "llm_provider", "agent")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WORKMATE_LLM_PROVIDER", "mock")
    monkeypatch.setenv("WORKMATE_CALENDAR_PROVIDER", "mock")
    monkeypatch.setenv("WORKMATE_MAIL_PROVIDER", "none")  # tests never depend on the developer's .env mail settings
    monkeypatch.setenv("WORKMATE_TOKEN_STORE", "file")
    clear()
    yield
    monkeypatch.undo()
    clear()


def clear() -> None:
    for name in CACHES:
        cached = getattr(dependencies, name)
        if hasattr(cached, "cache_clear"):  # a test may have replaced a factory with a plain function
            cached.cache_clear()


def restart_backend() -> None:
    """A new process: every in-memory object (agent window, pending approval cards) is gone; only SQLite remains."""
    clear()


def chat(message: str, conversation_id: str | None = None) -> dict:
    response = client.post("/api/v1/chat", json={"message": message, **({"conversation_id": conversation_id} if conversation_id else {})})
    assert response.status_code == 200
    return response.json()


def detail(conversation_id: str) -> dict:
    response = client.get(f"/api/v1/conversations/{conversation_id}")
    assert response.status_code == 200
    return response.json()


class Capture:
    """A model stand-in that records exactly what it was sent."""
    name, mode = "capture", "mock"

    def __init__(self) -> None:
        self.inputs: list[list[dict]] = []

    def respond(self, *, instructions, input_items, tools):
        self.inputs.append([dict(item) for item in input_items])
        return ProviderTurn(text=f"답변 {len(self.inputs)}", provider_items=[{"role": "assistant", "content": "x"}])


def use_capture(monkeypatch) -> Capture:
    provider = Capture()
    monkeypatch.setattr(dependencies, "llm_provider", lambda: provider)
    dependencies.agent.cache_clear()
    dependencies.conversation_service.cache_clear()
    return provider


def test_chat_creates_a_conversation_and_restores_messages_with_tools_and_cards() -> None:
    first = chat("내일 마감으로 보고서 작성 업무를 추가해 줘")
    cid = first["conversation_id"]
    assert cid and first["action"]["kind"] == "task_create"
    summaries = client.get("/api/v1/conversations").json()
    assert [(c["id"], c["message_count"]) for c in summaries] == [(cid, 2)]
    assert summaries[0]["title"].startswith("내일 마감으로 보고서 작성")

    stored = detail(cid)
    assert [(m["seq"], m["role"]) for m in stored["messages"]] == [(1, "user"), (2, "assistant")]
    reply = stored["messages"][1]
    assert reply["trace"][0]["tool"] == "propose_task_create" and reply["sources"] == ["propose_task_create"]
    assert reply["action"]["fields"]["제목"] == "보고서 작성" and reply["action"]["status"] == "pending_approval"
    assert reply["content"] == first["message"]


def test_conversation_survives_a_backend_restart_and_unrunnable_cards_expire() -> None:
    pending = chat("A 업무를 추가해 줘")
    approved_msg = chat("B 업무를 추가해 줘", pending["conversation_id"])
    rejected_msg = chat("C 업무를 추가해 줘", pending["conversation_id"])
    cid = pending["conversation_id"]
    client.post(f"/api/v1/actions/{approved_msg['action']['action_id']}/approve")
    client.post(f"/api/v1/actions/{rejected_msg['action']['action_id']}/reject")
    before = detail(cid)
    statuses = {m["action"]["fields"]["제목"]: m["action"]["status"] for m in before["messages"] if m["action"]}
    assert statuses == {"A": "pending_approval", "B": "completed", "C": "rejected"}
    assert len(before["messages"]) == 8  # 3 turns x 2 + the two recorded outcomes

    restart_backend()
    after = detail(cid)
    assert [m["content"] for m in after["messages"]] == [m["content"] for m in before["messages"]]
    statuses = {m["action"]["fields"]["제목"]: m["action"]["status"] for m in after["messages"] if m["action"]}
    assert statuses == {"A": "expired", "B": "completed", "C": "rejected"}  # the pending card no longer pretends to be runnable
    # ...and it truly cannot run: the approval store forgot it.
    assert client.post(f"/api/v1/actions/{pending['action']['action_id']}/approve").status_code == 404
    assert "A" not in [t["title"] for t in client.get("/api/v1/tasks").json()]
    assert "B" in [t["title"] for t in client.get("/api/v1/tasks").json()]


def test_decisions_are_stored_once_and_update_the_card() -> None:
    reply = chat("D 업무를 추가해 줘")
    card = reply["action"]
    for _ in range(3):  # repeated clicks must not spam the history
        client.post(f"/api/v1/actions/{card['action_id']}/approve")
    messages = detail(reply["conversation_id"])["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "assistant"]
    assert messages[1]["action"]["status"] == "completed" and "추가했습니다" in messages[2]["content"]
    assert sum(t["title"] == "D" for t in client.get("/api/v1/tasks").json()) == 1


def test_restoring_never_reruns_tools_or_actions(monkeypatch) -> None:
    reply = chat("E 업무를 추가해 줘")
    calls: list[str] = []
    original = ToolRegistry.execute
    monkeypatch.setattr(ToolRegistry, "execute", lambda self, name, arguments: (calls.append(name), original(self, name, arguments))[1])
    tasks_before = client.get("/api/v1/tasks").json()
    for _ in range(3):
        restart_backend()
        detail(reply["conversation_id"]); client.get("/api/v1/conversations")
    assert calls == [] and client.get("/api/v1/tasks").json() == tasks_before


def test_continuing_an_old_conversation_uses_its_stored_context_only(monkeypatch) -> None:
    provider = use_capture(monkeypatch)
    cid = chat("첫 질문입니다")["conversation_id"]
    restart_backend()  # new process: the agent's in-memory window is gone, SQLite is not
    chat("이어서 질문합니다", cid)
    sent = provider.inputs[-1]
    assert [i["content"] for i in sent] == ["첫 질문입니다", "답변 1", "이어서 질문합니다"]  # restored from SQLite, not memory
    other = chat("다른 대화 시작")["conversation_id"]
    assert other != cid and [i["content"] for i in provider.inputs[-1]] == ["다른 대화 시작"]  # conversations do not leak into each other


def test_long_conversations_send_only_the_recent_window_but_store_everything(monkeypatch) -> None:
    provider = use_capture(monkeypatch)
    cid = chat("질문 0")["conversation_id"]
    for i in range(1, 15):
        chat(f"질문 {i}", cid)
    sent = [i["content"] for i in provider.inputs[-1]]
    assert len(sent) == 9 and sent[-1] == "질문 14" and "질문 0" not in sent and "질문 5" not in sent  # last 8 stored messages + the new one
    assert sent[0] == "질문 10" and "질문 9" not in sent  # exactly the last 4 turns (8 messages) precede the new question
    assert detail(cid)["message_count"] == 30 and len(detail(cid)["messages"]) == 30


def test_list_order_new_conversation_and_delete_cleans_up_messages() -> None:
    a = chat("첫 번째 대화")["conversation_id"]
    b = chat("두 번째 대화")["conversation_id"]
    chat("첫 번째 대화에 이어서", a)
    assert [c["id"] for c in client.get("/api/v1/conversations").json()] == [a, b]  # most recently updated first
    assert client.delete(f"/api/v1/conversations/{a}").status_code == 204
    assert client.get(f"/api/v1/conversations/{a}").status_code == 404 and client.delete(f"/api/v1/conversations/{a}").status_code == 404
    with sqlite3.connect("data/workmate.db") as db:
        assert db.execute("SELECT COUNT(*) FROM conversation_messages WHERE conversation_id = ?", (a,)).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM conversation_messages WHERE conversation_id = ?", (b,)).fetchone()[0] == 2
    assert [c["id"] for c in client.get("/api/v1/conversations").json()] == [b]


def test_credentials_and_raw_tool_output_are_never_stored() -> None:
    client.post("/api/v1/memos", json={"title": "비밀 메모", "content": "오직-메모-본문-ZZZ"})
    secret_openai = "sk-proj-" + "A" * 40
    secret_google = "ya29." + "b" * 40
    reply = chat(f"내 키는 {secret_openai} 이고 토큰은 {secret_google} 이야. 최근 작성한 메모 보여줘")
    cid = reply["conversation_id"]
    with sqlite3.connect("data/workmate.db") as db:
        dump = json.dumps(db.execute("SELECT * FROM conversation_messages").fetchall(), ensure_ascii=False)
        traces = [row[0] for row in db.execute("SELECT trace FROM conversation_messages WHERE trace IS NOT NULL")]
    assert secret_openai not in dump and secret_google not in dump and "[삭제됨]" in dump
    assert "오직-메모-본문-ZZZ" not in dump  # tool output is not persisted, only a display summary
    assert all(set(item) == {"tool", "risk", "success", "duration_ms"} for t in traces for item in json.loads(t))
    assert detail(cid)["messages"][0]["content"].count("[삭제됨]") == 2


def test_existing_data_is_untouched_and_unknown_ids_are_accepted() -> None:
    task = client.post("/api/v1/tasks", json={"title": "기존 업무"}).json()
    memo = client.post("/api/v1/memos", json={"title": "기존 메모", "content": "본문"}).json()
    restart_backend()
    reply = chat("안녕", "my-fixed-id")  # callers that choose their own id keep working
    assert reply["conversation_id"] == "my-fixed-id"
    assert client.get(f"/api/v1/tasks/{task['id']}").json()["title"] == "기존 업무"
    assert client.get(f"/api/v1/memos/{memo['id']}").json()["content"] == "본문"
    assert client.get("/api/v1/conversations/nope").status_code == 404
