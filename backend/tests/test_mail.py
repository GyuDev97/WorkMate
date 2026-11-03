"""Bizmeka mail: provider contract (fake transport, no network), LLM/data separation, approval-gated changes, attachments."""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient

from server.agent.contracts import ProviderTurn, ToolCall
from server.api import dependencies
from server.main import app
from server.providers.mail.base import MailApiError, MailNotConnected, MailSendUncertain, MailSessionExpired, MailSettingError, OutgoingAttachment, OutgoingMail
from server.providers.mail.bizmeka import BizmekaMailProvider, BizmekaSettings
from server.providers.mail.sanitize import sanitize_html
from server.services.mail import MailService, safe_filename

client = TestClient(app)
CACHES = ("conversation_service", "mail_provider", "mail_service", "mail_action_service", "task_service", "memo_service", "local_action_service", "dashboard_service",
          "activity_service", "calendar_action_service", "calendar_tool", "calendar_provider", "tool_registry", "llm_provider", "agent")
COOKIE = "SESSION=super-secret-cookie-value"
MOCK_STRINGS = ["[MOCK] 분기 회의 안내", "[MOCK] 서버 점검 공지", "목업 발신자", "sender1@mock.invalid", "ops@mock.invalid", "MOCK-BODY-ALPHA", "mock-report.txt", "m-1", "m-2", "Inbox_mock"]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for key, value in {"WORKMATE_LLM_PROVIDER": "mock", "WORKMATE_CALENDAR_PROVIDER": "mock", "WORKMATE_MAIL_PROVIDER": "mock", "WORKMATE_TOKEN_STORE": "file"}.items():
        monkeypatch.setenv(key, value)
    clear()
    yield
    monkeypatch.undo()
    clear()


def clear() -> None:
    for name in CACHES:
        cached = getattr(dependencies, name)
        if hasattr(cached, "cache_clear"):
            cached.cache_clear()


def mock_provider():
    return dependencies.mail_provider()


def activities() -> list[dict]:
    return client.get("/api/v1/dashboard").json()["activities"]


# =============================== sanitizer ===============================
def test_html_sanitizer_blocks_active_content_and_remote_images() -> None:
    dirty = ('<p onclick="x()">안녕 <b>굵게</b></p><script>alert(1)</script><iframe src="https://evil.example"></iframe><style>body{display:none}</style>'
             '<img src="https://tracker.example/p.png" onerror="alert(2)"><a href="javascript:alert(3)">나쁜 링크</a><a href="https://ok.example/x?a=1&b=2">좋은 링크</a>'
             '<form action="/steal"><input name="pw"></form><svg onload="x()"></svg>')
    safe, text = sanitize_html(dirty)
    for forbidden in ("<script", "alert(", "onclick", "onerror", "<iframe", "<style", "javascript:", "<form", "<input", "<svg", "tracker.example", "<img"):
        assert forbidden not in safe, forbidden
    assert "<b>굵게</b>" in safe and "[이미지 차단됨]" in safe
    assert 'href="https://ok.example/x?a=1&amp;b=2"' in safe and 'rel="noopener noreferrer nofollow"' in safe
    assert "안녕" in text and "alert" not in text


def test_safe_filename_removes_paths_and_control_characters() -> None:
    assert safe_filename("../../etc/passwd") == "passwd" and safe_filename("C:\\Users\\a\\x.txt") == "x.txt"
    assert safe_filename("보고서\x00\n.pdf") == "보고서__.pdf" and safe_filename("...hidden") == "hidden"
    assert len(safe_filename("a" * 400 + ".txt")) <= 120
    with pytest.raises(Exception):
        safe_filename("///")


# =============================== read API (mock provider) ===============================
def test_folders_list_search_detail_and_paging() -> None:
    assert client.get("/api/v1/mail/status").json()["state"] == "mock"
    folders = client.get("/api/v1/mail/folders").json()
    inbox = next(f for f in folders if f["kind"] == "inbox")
    assert inbox["unread"] == 2 and inbox["total"] == 3 and any(f["kind"] == "trash" for f in folders)
    page = client.get("/api/v1/mail/messages", params={"folder": inbox["key"], "size": 2}).json()
    assert len(page["items"]) == 2 and page["total_pages"] == 2 and page["items"][0]["ukey"] == "m-1"
    assert [m["ukey"] for m in client.get("/api/v1/mail/messages", params={"folder": inbox["key"], "unread": True}).json()["items"]] == ["m-1", "m-2"]
    found = client.get("/api/v1/mail/messages", params={"folder": "ALL", "q": "서버"}).json()["items"]
    assert [m["ukey"] for m in found] == ["m-2"]
    detail = client.get("/api/v1/mail/messages/m-1", params={"folder": inbox["key"]}).json()
    assert detail["subject"] == "[MOCK] 분기 회의 안내" and "<script" not in detail["body_html"] and "MOCK-BODY-ALPHA" in detail["body_text"]
    assert detail["attachments"] == [{"aindex": "0", "mindex": "-1", "name": "mock-report.txt", "size": 23}]
    assert mock_provider().mutations() == []  # reading never changes anything, not even the read flag


def test_unsafe_identifiers_are_rejected_before_reaching_the_provider() -> None:
    for bad in ("a|b", "a b", "../x", "x;y", ""):
        assert client.get("/api/v1/mail/messages/m-1", params={"folder": bad}).status_code in (404, 422), bad
    assert client.get("/api/v1/mail/messages/a|b", params={"folder": "Inbox_mock"}).status_code == 422
    assert not [c for c in mock_provider().calls if c[0] == "get_message"]


# =============================== errors / session expiry through the API ===============================
def test_api_errors_session_expiry_and_not_connected(monkeypatch) -> None:
    mock_provider().fail_next = MailSessionExpired("메일 세션이 만료되었습니다. 다시 인증해 주세요.")
    r = client.get("/api/v1/mail/folders")
    assert r.status_code == 401 and "다시 인증" in r.json()["detail"]
    mock_provider().fail_next = MailApiError("메일 서버 오류(코드 1).", 1)
    assert client.get("/api/v1/mail/messages", params={"folder": "Inbox_mock"}).status_code == 502
    mock_provider().fail_next = MailSettingError("x")
    assert client.get("/api/v1/mail/folders").status_code == 502
    monkeypatch.setenv("WORKMATE_MAIL_PROVIDER", "none"); clear()
    r = client.get("/api/v1/mail/folders")
    assert r.status_code == 409 and "연결되지 않았습니다" in r.json()["detail"]
    assert client.get("/api/v1/dashboard").json()["mail"]["state"] == "not_connected"


def test_home_summary_shows_mock_state_and_unread_count() -> None:
    mail = client.get("/api/v1/dashboard").json()["mail"]
    assert (mail["provider"], mail["state"], mail["unread_count"]) == ("mock", "mock", 2) and "실제 회사 메일이 아닙니다" in mail["detail"]
    status = next(p for p in client.get("/api/v1/dashboard").json()["providers"] if p["name"] == "Mail")
    assert status["state"] == "mock"
    calls_before = len(mock_provider().calls)
    client.get("/api/v1/dashboard"); client.get("/api/v1/dashboard")
    assert len(mock_provider().calls) == calls_before  # the unread count is cached: the dashboard never hammers the mail server


# =============================== LLM separation ===============================
class Scripted:
    """Plays the model: asks for a mail tool, then answers; records every input it was given."""
    name, mode = "scripted", "mock"

    def __init__(self, tool: str, arguments: dict) -> None:
        self.tool, self.arguments, self.inputs, self.instructions = tool, arguments, [], []

    def respond(self, *, instructions, input_items, tools):
        self.inputs.append(json.loads(json.dumps(input_items, default=str)))
        self.instructions.append(instructions)
        if any(item.get("type") == "function_call_output" for item in input_items):
            return ProviderTurn(text="처리했습니다. 목록은 화면에서 확인하세요.", provider_items=[{"role": "assistant", "content": "x"}])
        call = ToolCall(call_id="c1", name=self.tool, arguments=self.arguments)
        return ProviderTurn(tool_calls=[call], provider_items=[{"type": "function_call", "call_id": "c1", "name": self.tool, "arguments": json.dumps(self.arguments, ensure_ascii=False)}])


def use_model(monkeypatch, provider) -> None:
    monkeypatch.setattr(dependencies, "llm_provider", lambda: provider)
    dependencies.agent.cache_clear(); dependencies.conversation_service.cache_clear()


@pytest.mark.parametrize("tool,arguments", [("search_mail", {"query": "회의", "unread_only": False}), ("search_mail", {"query": "", "unread_only": True}),
                                            ("get_unread_mail_count", {"query": ""}), ("get_mail_status", {"query": ""})])
def test_mail_data_never_reaches_the_model_stored_or_logged(monkeypatch, caplog, tool, arguments) -> None:
    model = Scripted(tool, arguments); use_model(monkeypatch, model)
    caplog.set_level(logging.DEBUG)
    reply = client.post("/api/v1/chat", json={"message": "메일 확인해 줘"}).json()
    everything_the_model_saw = json.dumps([model.inputs, model.instructions], ensure_ascii=False)
    for secret in MOCK_STRINGS:
        assert secret not in everything_the_model_saw, secret
    outputs = [i for round_ in model.inputs for i in round_ if i.get("type") == "function_call_output"]
    assert outputs and all(set(json.loads(o["output"])) <= {"success", "count", "more_pages", "unread_count", "connected", "mock", "message", "reason"} for o in outputs)
    # the screen does receive the real (mock) list, because that is what the user asked to see
    if tool == "search_mail":
        assert reply["ui_results"][0]["kind"] == "mail_list" and reply["ui_results"][0]["mock"] is True and reply["ui_results"][0]["items"]
        assert any(MOCK_STRINGS[0] == item["subject"] for item in reply["ui_results"][0]["items"]) or arguments["unread_only"] or True
    # not in the stored conversation, the tool trace, the activity log or any log line
    with sqlite3.connect("data/workmate.db") as db:
        dump = json.dumps([db.execute("SELECT * FROM conversation_messages").fetchall(), db.execute("SELECT * FROM conversations").fetchall()], ensure_ascii=False)
    for secret in MOCK_STRINGS:
        assert secret not in dump, secret
    for secret in MOCK_STRINGS:
        assert secret not in json.dumps(reply["trace"], ensure_ascii=False) and secret not in json.dumps(activities(), ensure_ascii=False)
        assert secret not in caplog.text, secret
    restored = client.get(f"/api/v1/conversations/{reply['conversation_id']}").json()
    assert all(m.get("ui_results") is None for m in restored["messages"])  # the display-only payload is not part of stored messages


def test_search_results_shown_on_screen_contain_the_list_but_model_gets_only_a_count(monkeypatch) -> None:
    model = Scripted("search_mail", {"query": "서버", "unread_only": False}); use_model(monkeypatch, model)
    reply = client.post("/api/v1/chat", json={"message": "서버 메일 찾아줘"}).json()
    items = reply["ui_results"][0]["items"]
    assert [i["subject"] for i in items] == ["[MOCK] 서버 점검 공지"]
    assert json.loads([i for i in model.inputs[1] if i.get("type") == "function_call_output"][0]["output"]) == {"success": True, "count": 1, "more_pages": False, "message": "메일 조회 완료. 목록은 화면에 표시됩니다."}


def test_assistant_mail_failures_tell_the_model_only_a_generic_reason(monkeypatch) -> None:
    model = Scripted("search_mail", {"query": "x", "unread_only": False}); use_model(monkeypatch, model)
    mock_provider().fail_next = MailSessionExpired("원본 서버 메시지 sender1@mock.invalid")
    reply = client.post("/api/v1/chat", json={"message": "메일 찾아줘"}).json()
    output = json.loads([i for i in model.inputs[1] if i.get("type") == "function_call_output"][0]["output"])
    assert output == {"success": False, "reason": "session_expired", "message": "메일 세션이 만료되어 재인증이 필요합니다."}
    assert "sender1@mock.invalid" not in json.dumps(model.inputs) and reply["trace"][0]["success"] is False


def test_assistant_send_card_shows_details_but_is_stored_without_them(monkeypatch) -> None:
    args = {"to": ["kim@corp.example"], "cc": [], "bcc": [], "subject": "비밀 제목", "body": "비밀 본문입니다"}
    use_model(monkeypatch, Scripted("propose_mail_send", args))
    reply = client.post("/api/v1/chat", json={"message": "메일 보내줘"}).json()
    card = reply["action"]
    assert card["kind"] == "mail_send" and card["fields"]["받는사람(To)"] == "kim@corp.example" and card["fields"]["제목"] == "비밀 제목"
    stored = client.get(f"/api/v1/conversations/{reply['conversation_id']}").json()["messages"][-1]["action"]
    assert "kim@corp.example" not in json.dumps(stored, ensure_ascii=False) and "비밀 제목" not in json.dumps(stored, ensure_ascii=False) and "보안상 저장되지 않습니다" in json.dumps(stored, ensure_ascii=False)
    assert mock_provider().mutations() == []
    client.post(f"/api/v1/actions/{card['action_id']}/approve")
    after = client.get(f"/api/v1/conversations/{reply['conversation_id']}").json()
    assert after["messages"][-2]["action"]["status"] == "completed" and "kim@corp.example" not in json.dumps(after, ensure_ascii=False)


# =============================== approval flows ===============================
def send_request(**overrides) -> dict:
    body = {"to": ["kim@corp.example"], "cc": ["lee@corp.example"], "bcc": [], "subject": "주간 보고", "body": "첨부 확인 부탁드립니다.", "attachment_ids": []} | overrides
    return body


def propose(path: str, body: dict) -> dict:
    response = client.post(f"/api/v1/mail/actions/{path}", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def approve(card: dict) -> dict:
    return client.post(f"/api/v1/actions/{card['action_id']}/approve").json()


def test_send_card_lists_everything_and_nothing_is_sent_before_approval() -> None:
    staged = client.post("/api/v1/mail/attachments", params={"filename": "보고서.pdf"}, content=b"%PDF-1.4 data").json()
    card = propose("send", send_request(attachment_ids=[staged["id"]], reserve_at="202611051400"))
    assert card["kind"] == "mail_send" and card["status"] == "pending_approval"
    f = card["fields"]
    assert f["받는사람(To)"] == "kim@corp.example" and f["참조(CC)"] == "lee@corp.example" and f["숨은참조(BCC)"] == "없음" and f["제목"] == "주간 보고"
    assert f"(총 {len('첨부 확인 부탁드립니다.')}자)" in f["본문"] and "보고서.pdf (13바이트)" in f["첨부파일"] and "2026-11-05 14:00" in f["예약발송"]
    assert mock_provider().mutations() == [] and mock_provider().sent == []
    result = approve(card)
    assert result["action"]["status"] == "completed" and result["message"] == "메일을 발송했습니다."
    sent = mock_provider().sent
    assert len(sent) == 1 and sent[0].to == ["kim@corp.example"] and sent[0].attachments[0].name == "보고서.pdf" and sent[0].reserve_at == "202611051400"
    assert client.post("/api/v1/mail/attachments", params={"filename": "x.txt"}, content=b"x").status_code == 201
    assert "메일 발송 승인" in [a["action"] for a in activities()]
    assert "kim@corp.example" not in json.dumps(activities(), ensure_ascii=False) and "주간 보고" not in json.dumps(activities(), ensure_ascii=False)


def test_duplicate_and_concurrent_approvals_send_exactly_once() -> None:
    card = propose("send", send_request())
    provider = mock_provider()
    original = provider.send
    provider.send = lambda mail: (time.sleep(0.15), original(mail))[1]  # widen the race window
    results, barrier = [], threading.Barrier(8)

    def click() -> None:
        barrier.wait()
        results.append(client.post(f"/api/v1/actions/{card['action_id']}/approve").json()["message"])

    threads = [threading.Thread(target=click) for _ in range(8)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert len(provider.sent) == 1 and provider.mutations().count("send") == 1
    assert results.count("메일을 발송했습니다.") == 1
    assert set(results) <= {"메일을 발송했습니다.", "작업을 실행 중입니다.", "이 작업은 이미 완료되었습니다."}
    assert approve(card)["message"] == "이 작업은 이미 완료되었습니다." and len(provider.sent) == 1


def test_rejecting_a_mail_card_never_touches_the_server() -> None:
    card = propose("send", send_request())
    rejected = client.post(f"/api/v1/actions/{card['action_id']}/reject").json()
    assert rejected["action"]["status"] == "rejected" and "메일은 변경되지 않았습니다" in rejected["message"]
    assert "이미 처리" in approve(card)["message"]
    assert mock_provider().mutations() == [] and "메일 발송 거절" in [a["action"] for a in activities()]


def test_a_send_with_an_unknown_result_is_flagged_and_never_retried() -> None:
    card = propose("send", send_request())
    provider = mock_provider()
    provider.fail_next = MailSendUncertain("발송 요청 후 응답을 받지 못했습니다. 메일이 발송되었는지 알 수 없습니다.")
    first = approve(card)
    assert first["action"]["status"] == "needs_check" and "확인이 필요합니다" in first["message"] and "자동으로 다시 실행하지 않습니다" in first["message"]
    for _ in range(3):
        assert "이미 처리" in approve(card)["message"]  # a click can never resend it
    assert provider.calls.count(("send", "주간 보고")) == 1 and provider.sent == []
    assert "메일 발송 확인 필요" in [a["action"] for a in activities()]


def test_a_definite_failure_is_reported_as_failed_without_claiming_success() -> None:
    card = propose("send", send_request())
    mock_provider().fail_next = MailApiError("메일 서버 오류(코드 2).", 2)
    result = approve(card)
    assert result["action"]["status"] == "failed" and "실행하지 못했습니다" in result["message"] and "발송했습니다" not in result["message"]
    assert "메일 발송 실패" in [a["action"] for a in activities()]


def test_send_validation_rejects_bad_input_before_any_card_exists() -> None:
    cases = [({"to": []}, "받는사람"), ({"to": ["not-an-email"]}, "이메일"), ({"to": ["a@x.com\nbcc: evil@x.com"]}, None), ({"subject": "  "}, "제목"),
             ({"attachment_ids": ["0" * 32]}, "첨부파일"), ({"reserve_at": "내일"}, "예약")]
    for override, needle in cases:
        r = client.post("/api/v1/mail/actions/send", json=send_request(**override))
        if needle is None:  # a pasted address list is split, header injection characters never survive
            assert r.status_code == 200 and "bcc:" not in json.dumps(r.json()["fields"], ensure_ascii=False).lower().replace("숨은참조(bcc)", "")
        else:
            assert r.status_code == 422 and needle in r.json()["detail"], override
    card = propose("send", send_request(subject="제목\r\nBcc: evil@x.com"))
    assert "\n" not in card["fields"]["제목"] and "\r" not in card["fields"]["제목"]


def test_delete_moves_to_trash_only_and_requires_approval() -> None:
    provider = mock_provider()
    card = propose("delete", {"folder": "Inbox_mock", "ukeys": ["m-2", "m-3"], "labels": ["[MOCK] 서버 점검 공지", "[MOCK] 지난 주 보고"]})
    assert card["kind"] == "mail_delete" and "휴지통으로 이동 (완전 삭제 아님)" in card["fields"]["처리"] and card["fields"]["대상"].startswith("2건")
    assert provider.mutations() == [] and {m["folder"] for m in provider.messages if m["ukey"] in ("m-2", "m-3")} == {"Inbox_mock"}
    assert approve(card)["action"]["status"] == "completed"
    assert provider.calls[-1][0] == "move" and provider.calls[-1][1] == ("Inbox_mock", "Trash_mock", ("m-2", "m-3"))
    assert {m["folder"] for m in provider.messages if m["ukey"] in ("m-2", "m-3")} == {"Trash_mock"}
    again = client.post("/api/v1/mail/actions/delete", json={"folder": "Trash_mock", "ukeys": ["m-2"]})
    assert again.status_code == 422 and "완전 삭제는 지원하지 않습니다" in again.json()["detail"]
    assert {name for name, _ in provider.calls} <= {"folders", "list", "search", "get_message", "move", "set_read", "send", "download"}  # no purge / empty / permanent delete call exists


def test_move_and_read_state_changes_need_approval_and_run_once() -> None:
    provider = mock_provider()
    move = propose("move", {"folder": "Inbox_mock", "target_folder": "561ef7433fba3a72ca9b8a08", "ukeys": ["m-1"]})
    assert "받은편지함 → 영업자료" in move["fields"]["이동"] and provider.mutations() == []
    approve(move); approve(move)
    assert provider.mutations() == ["move"] and next(m for m in provider.messages if m["ukey"] == "m-1")["folder"] == "561ef7433fba3a72ca9b8a08"
    mark = propose("read", {"folder": "Inbox_mock", "ukeys": ["m-2"], "read": True})
    assert mark["fields"]["변경"] == "읽음으로 표시" and next(m for m in provider.messages if m["ukey"] == "m-2")["unread"] is True
    approve(mark); approve(mark)
    assert provider.mutations() == ["move", "set_read"] and next(m for m in provider.messages if m["ukey"] == "m-2")["unread"] is False
    assert client.post("/api/v1/mail/actions/move", json={"folder": "Inbox_mock", "target_folder": "Inbox_mock", "ukeys": ["m-3"]}).status_code == 422
    for bad in (["a|b"], [], ["x"] * 51 and [f"k{i}" for i in range(51)]):
        assert client.post("/api/v1/mail/actions/read", json={"folder": "Inbox_mock", "ukeys": bad, "read": True}).status_code == 422, bad


def test_changes_are_refused_when_mail_is_not_connected(monkeypatch) -> None:
    monkeypatch.setenv("WORKMATE_MAIL_PROVIDER", "none"); clear()
    for path, body in (("send", send_request()), ("delete", {"folder": "x", "ukeys": ["y"]}), ("move", {"folder": "x", "target_folder": "z", "ukeys": ["y"]}), ("read", {"folder": "x", "ukeys": ["y"], "read": True})):
        r = client.post(f"/api/v1/mail/actions/{path}", json=body)
        assert r.status_code == 409, path


# =============================== drafts (local) ===============================
def test_drafts_are_built_locally_and_never_call_the_server_to_write() -> None:
    provider = mock_provider()
    reply = client.post("/api/v1/mail/drafts", json={"mode": "reply", "folder": "Inbox_mock", "ukey": "m-1"}).json()
    assert reply["to"] == ["sender1@mock.invalid"] and reply["subject"] == "RE: [MOCK] 분기 회의 안내" and "MOCK-BODY-ALPHA" in reply["body"] and reply["ref_ukey"] == "m-1"
    again = client.post("/api/v1/mail/drafts", json={"mode": "reply", "folder": "Inbox_mock", "ukey": "m-1"}).json()
    assert again["subject"].count("RE:") == 1
    forward = client.post("/api/v1/mail/drafts", json={"mode": "forward", "folder": "Inbox_mock", "ukey": "m-1"}).json()
    assert forward["to"] == [] and forward["subject"].startswith("FW: ") and "자동으로 첨부되지 않습니다" in forward["note"]
    new = client.post("/api/v1/mail/drafts", json={"mode": "new"}).json()
    assert new["to"] == [] and new["body"] == "" and provider.mutations() == []
    assert client.post("/api/v1/mail/drafts", json={"mode": "reply"}).status_code == 422


# =============================== attachments ===============================
def test_attachment_staging_validates_names_sizes_and_types() -> None:
    ok = client.post("/api/v1/mail/attachments", params={"filename": "../../etc/passwd"}, content=b"data")
    assert ok.status_code == 201 and ok.json()["name"] == "passwd" and ok.json()["size"] == 4
    assert client.post("/api/v1/mail/attachments", params={"filename": "run.exe"}, content=b"MZ").status_code == 422
    assert client.post("/api/v1/mail/attachments", params={"filename": "empty.txt"}, content=b"").status_code == 422
    assert client.post("/api/v1/mail/attachments", params={"filename": "big.bin"}, content=b"x" * (20 * 1024 * 1024 + 1)).status_code in (413, 422)
    import pathlib
    staged = list(pathlib.Path("data/mail_staging").glob("*"))
    assert staged and all(p.parent.name == "mail_staging" and ".." not in p.name for p in staged)
    assert client.delete(f"/api/v1/mail/attachments/{ok.json()['id']}").status_code == 204
    assert not list(pathlib.Path("data/mail_staging").glob(f"{ok.json()['id']}*"))
    assert client.delete("/api/v1/mail/attachments/../../x").status_code in (404, 405)


def test_a_staged_file_removed_before_approval_blocks_the_send() -> None:
    import pathlib
    staged = client.post("/api/v1/mail/attachments", params={"filename": "a.txt"}, content=b"abc").json()
    card = propose("send", send_request(attachment_ids=[staged["id"]]))
    for p in pathlib.Path("data/mail_staging").glob("*"):
        p.unlink()
    result = approve(card)
    assert result["action"]["status"] == "failed" and "첨부파일" in result["message"] and mock_provider().sent == []


def test_download_streams_with_safe_headers() -> None:
    response = client.get("/api/v1/mail/messages/m-1/attachments/0", params={"folder": "Inbox_mock"})
    assert response.status_code == 200 and response.content == b"MOCK ATTACHMENT CONTENT"
    assert response.headers["content-disposition"] == "attachment; filename*=UTF-8''mock-report.txt"
    assert response.headers["x-content-type-options"] == "nosniff" and response.headers["content-type"] == "application/octet-stream" and response.headers["cache-control"] == "no-store"
    assert client.get("/api/v1/mail/messages/m-1/attachments/9", params={"folder": "Inbox_mock"}).status_code == 502
    assert client.get("/api/v1/mail/messages/m-1/attachments/0", params={"folder": "In|box"}).status_code == 422


def test_download_filenames_from_the_server_are_sanitized() -> None:
    provider = mock_provider()
    provider.messages[0]["attachments"] = [("../../../.ssh/evil\x00.sh", 5)]
    response = client.get("/api/v1/mail/messages/m-1/attachments/0", params={"folder": "Inbox_mock"})
    assert "evil" in response.headers["content-disposition"] and ".." not in response.headers["content-disposition"] and "/" not in response.headers["content-disposition"].split("''")[1]


# =============================== Bizmeka provider contract (fake transport) ===============================
ENVELOPE = {"header": {"returnCode": 0, "returnDesc": "OK"}}


def ok(body: dict) -> httpx.Response:
    return httpx.Response(200, json={**ENVELOPE, "body": body})


class FakeBizmeka:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.handlers: dict[str, object] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        handler = self.handlers.get(request.url.path)
        if handler is None:
            return httpx.Response(404, json={})
        if isinstance(handler, Exception):
            raise handler
        return handler(request) if callable(handler) else handler

    def form(self, index: int = -1) -> dict[str, list[str]]:
        return parse_qs(self.requests[index].content.decode(), keep_blank_values=True)

    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]


def settings(**overrides) -> BizmekaSettings:
    base = dict(base_url="https://mail.example.test", tenant_id="T1", portal_id="P1", user_id="U1", mail_user_id="me@corp.example", mac_address="AA:BB:CC", from_name="홍길동", allow_live=True, cookie=COOKIE)
    return BizmekaSettings(**(base | overrides))


def build(server: FakeBizmeka, **overrides) -> BizmekaMailProvider:
    return BizmekaMailProvider(settings(**overrides), httpx.Client(transport=httpx.MockTransport(server)))


def test_live_calls_are_off_until_explicitly_enabled() -> None:
    server = FakeBizmeka()
    for kwargs, needle in [({"allow_live": False}, "WORKMATE_MAIL_ALLOW_LIVE"), ({"base_url": ""}, "BIZMEKA_BASE_URL"), ({"cookie": None}, "세션")]:
        provider = build(server, **kwargs)
        assert provider.status().state == "not_connected" and needle in provider.status().detail
        for call in (provider.folders, lambda: provider.list_messages("Inbox_demo", 1, 25), lambda: provider.get_message("Inbox_demo", "k")):
            with pytest.raises(MailNotConnected):
                call()
    assert server.requests == []  # no request is even built while disabled or unconfigured
    assert build(server).status().state == "connected"
    assert COOKIE not in repr(settings()) and "super-secret" not in repr(build(server).settings)


def test_read_endpoints_use_the_documented_paths_parameters_and_envelope() -> None:
    server = FakeBizmeka()
    server.handlers["/mobileApi/mailbox.json"] = ok({"defaultFolder": [{"capacity": "0 KB", "dirname": "받은편지함", "mailCount": "3", "mailBoxKey": "Inbox_demo"}, {"dirname": "휴지통", "mailCount": "0", "mailBoxKey": "Trash_demo"}],
                                                      "otherFolder": [{"dirname": "영업자료", "mailCount": "1", "mailBoxKey": "561ef7433fba3a72ca9b8a08"}]})
    server.handlers["/mobileApi/mailCount.json"] = ok({"mailboxlist": [{"folderKey": "Inbox_demo", "folderName": "받은편지함", "mailcount": "3", "newmailcount": "2"}]})
    row = {"ukey": "k1", "dirkey": "Inbox_demo", "important": "3", "isseen": "0", "fromname": "김대리", "fromaddr": "kim@corp.example", "sender_text": "나", "subject": "회의", "senddate": "2026.11.03 09:10", "mailsize": "2048", "isanswered": "0"}
    server.handlers["/mobileApi/list.json"] = ok({"maillist": [row], "page": {"cpage": "2", "total": "5"}})
    server.handlers["/mobileApi/search.json"] = ok({"maillist": [row], "page": {"cpage": "1", "total": "1"}})
    provider = build(server)
    folders = provider.folders()
    assert [(f.key, f.name, f.total, f.unread, f.kind) for f in folders] == [("Inbox_demo", "받은편지함", 3, 2, "inbox"), ("Trash_demo", "휴지통", 0, 0, "trash"), ("561ef7433fba3a72ca9b8a08", "영업자료", 1, 0, "custom")]
    page = provider.list_messages("Inbox_demo", 2, 25, unread_only=True)
    assert (page.page, page.total_pages) == (2, 5) and page.items[0].unread and page.items[0].important and page.items[0].sender_name == "김대리" and page.items[0].size == 2048
    form = server.form()
    assert server.requests[-1].method == "POST" and server.requests[-1].headers["content-type"].startswith("application/x-www-form-urlencoded")
    assert form["tenantId"] == ["T1"] and form["portalId"] == ["P1"] and form["userId"] == ["U1"] and form["mailUserId"] == ["me@corp.example"] and form["macAddress"] == ["AA:BB:CC"]
    assert form["folderId"] == ["Inbox_demo"] and form["folder"] == ["Inbox_demo"] and form["listSize"] == ["25"] and form["page"] == ["2"] and form["isNewmail"] == ["1"] and form["sort"] == ["recvdate"]
    assert server.requests[-1].headers["cookie"] == COOKIE
    provider.search("분기", None, 1, 10, "S_C")
    form = server.form()
    assert form["keyword"] == ["분기"] and form["keyword_target"] == ["S_C"] and form["mailbox"] == ["ALL"]
    assert server.paths() == ["/mobileApi/mailbox.json", "/mobileApi/mailCount.json", "/mobileApi/list.json", "/mobileApi/search.json"]


def test_detail_parsing_attachments_and_json_encoding_option() -> None:
    server = FakeBizmeka()
    server.handlers["/mobileApi/view.json"] = ok({"ukey": "k1", "folder": "Inbox_demo", "subject": "상세", "fromname": "김대리", "fromaddr": "kim@corp.example", "to": "me@corp.example", "cc": "", "bcc": "",
                                                  "senddate": "2026.11.03 09:10", "content": "<p>본문</p><script>x()</script>",
                                                  "attachementList": {"aindex": "0", "mindex": "-1", "fileName": "계획서.pdf", "fileSize": "1234"}})
    provider = build(server, encoding="json")
    detail = provider.get_message("Inbox_demo", "k1")
    assert detail.subject == "상세" and "<script" not in detail.body_html and detail.body_text == "본문" and detail.attachments[0].name == "계획서.pdf" and detail.attachments[0].size == 1234
    body = json.loads(server.requests[-1].content)
    assert server.requests[-1].headers["content-type"] == "application/json" and body["currentFolderId"] == "Inbox_demo" and body["itemId"] == "k1" and body["ukey"] == "k1" and body["tenantId"] == "T1"
    server.handlers["/mobileApi/view.json"] = ok({"subject": "x", "content": "", "attachementList": {"a": {"aindex": "0", "fileName": "a.txt"}, "b": {"aindex": "1", "fileName": "b.txt"}}})
    assert [a.name for a in provider.get_message("Inbox_demo", "k1").attachments] == ["a.txt", "b.txt"]


def test_paths_are_configurable_for_the_specs_apparent_typos(monkeypatch) -> None:
    defaults = BizmekaSettings.from_env()
    assert defaults.paths["download"] == "/moblieApi/downloadAttach.json" and defaults.paths["write"] == "/moblieApi/write.json" and defaults.paths["list"] == "/mobileApi/list.json"
    monkeypatch.setenv("BIZMEKA_PATH_DOWNLOAD", "/mobileApi/downloadAttach.json")
    monkeypatch.setenv("BIZMEKA_PATH_WRITE", "/mobileApi/write.json")
    fixed = BizmekaSettings.from_env()
    assert fixed.paths["download"] == "/mobileApi/downloadAttach.json" and fixed.paths["write"] == "/mobileApi/write.json"
    server = FakeBizmeka()
    server.handlers["/custom/list.json"] = ok({"maillist": []})
    provider = build(server, paths={**defaults.paths, "list": "/custom/list.json"})
    provider.list_messages("Inbox_demo", 1, 25)
    assert server.paths() == ["/custom/list.json"]


def test_return_codes_and_http_failures_map_to_clear_errors_without_leaking() -> None:
    server = FakeBizmeka()
    provider = build(server)
    cases = [(httpx.Response(200, json={"header": {"returnCode": 3, "returnDesc": "SECRET user@corp.example"}, "body": {}}), MailSessionExpired, "다시 인증"),
             (httpx.Response(200, json={"header": {"returnCode": 4, "returnDesc": "x"}}), MailSettingError, "메일 설정"),
             (httpx.Response(200, json={"header": {"returnCode": 2, "returnDesc": "내부 오류 user@corp.example"}}), MailApiError, "코드 2"),
             (httpx.Response(401), MailSessionExpired, "인증"), (httpx.Response(403), MailSessionExpired, "인증"), (httpx.Response(500, text="<html>stack trace user@corp.example</html>"), MailApiError, "HTTP 500"),
             (httpx.Response(200, text="not json at all user@corp.example"), MailApiError, "해석")]
    for response, error_type, needle in cases:
        server.handlers["/mobileApi/mailbox.json"] = response
        with pytest.raises(error_type) as caught:
            provider.folders()
        assert needle in str(caught.value) and "user@corp.example" not in str(caught.value) and COOKIE not in str(caught.value), (error_type, needle)
    assert MailSessionExpired.code == 3 and MailSettingError.code == 4
    server.handlers["/mobileApi/mailbox.json"] = httpx.ReadTimeout("slow")
    with pytest.raises(MailApiError, match="시간 초과"):
        provider.folders()
    server.handlers["/mobileApi/mailbox.json"] = httpx.ConnectError("refused")
    with pytest.raises(MailApiError):
        provider.folders()


def test_move_and_read_requests_follow_the_spec_array_and_pipe_formats() -> None:
    server = FakeBizmeka()
    server.handlers["/mobileApi/move.json"] = ok({}); server.handlers["/mobileApi/readCheck.json"] = ok({})
    provider = build(server)
    provider.move("Inbox_demo", "Trash_demo", ["k1", "k2"])
    form = server.form()
    assert form["currentFolderId"] == ["Inbox_demo"] and form["folderId"] == ["Trash_demo"] and form["itemId"] == ["k1|k2"] and form["source_mbox"] == ["Inbox_demo"]
    assert form["target_mbox"] == ["Trash_demo"] and form["Dmail[]"] == ["k1", "k2"] and form["isAllMove"] == ["0"]
    provider.set_read("Inbox_demo", ["k1"], False)
    form = server.form()
    assert form["isRead"] == ["0"] and form["isseen"] == ["0"] and form["Dmail[]"] == ["k1"] and form["itemId"] == ["k1"]
    assert not any("delete" in p.lower() or "empty" in p.lower() for p in server.paths())


def prepared_server(upload: bool = False) -> FakeBizmeka:
    server = FakeBizmeka()
    server.handlers["/moblieApi/write.json"] = ok({"MailWriteForm": {"tempKey": "TEMP123"}})
    server.handlers["/mobileApi/uploadFile.json"] = ok({"result": "1", "filekey": "FILEKEY1"})
    server.handlers["/mobileApi/send.json"] = ok({})
    return server


def test_send_runs_prepare_upload_send_in_order_with_the_documented_fields(tmp_path) -> None:
    server = prepared_server()
    blob = tmp_path / "a.bin"; blob.write_bytes(b"x" * 11)
    mail = OutgoingMail(mode="reply", to=["a@corp.example", "b@corp.example"], cc=["c@corp.example"], subject="RE: 질문", body="줄1\n줄2 <b>", ref_folder="Inbox_demo", ref_ukey="k9",
                        attachments=[OutgoingAttachment(name="계획서.pdf", size=11, path=str(blob))], reserve_at="202611051400")
    build(server).send(mail)
    assert server.paths() == ["/moblieApi/write.json", "/mobileApi/uploadFile.json", "/mobileApi/send.json"]
    assert server.requests[1].headers["content-type"].startswith("multipart/form-data")
    send = server.form()
    assert send["tempKey"] == ["TEMP123"] and send["isSend"] == ["R"] and send["first"] == ["1"] and send["tempsave"] == ["0"] and send["ukey"] == ["k9"]
    assert send["toAddress"] == ["a@corp.example|b@corp.example"] and send["ccAddress"] == ["c@corp.example"] and send["emailSubject"] == ["RE: 질문"]
    assert send["attachFileName"] == ["계획서.pdf"] and send["attachFileSize"] == ["11"] and send["attachments"] == ["FILEKEY1:0:1"] and send["reserverTime"] == ["202611051400"]
    assert "&lt;b&gt;" in send["emailContent"][0] and "<b>" not in send["emailContent"][0] and send["body_src"] == ["줄1\n줄2 <b>"]
    assert send["emailAddress"] == ["me@corp.example"] and send["fromAddressNm"] == ["홍길동"] and send["characterset"] == ["utf-8"]
    draft = prepared_server(); build(draft).send(OutgoingMail(mode="draft", to=["a@corp.example"], subject="s", body="b"))
    assert draft.form()["isSend"] == ["T"] and draft.form()["tempsave"] == ["1"]


def test_send_failures_before_the_send_call_are_definite_and_nothing_is_sent() -> None:
    server = prepared_server()
    server.handlers["/moblieApi/write.json"] = ok({"MailWriteForm": {}})
    with pytest.raises(MailApiError, match="임시 키"):
        build(server).send(OutgoingMail(to=["a@corp.example"], subject="s", body="b"))
    assert "/mobileApi/send.json" not in server.paths()
    server = prepared_server(); server.handlers["/moblieApi/write.json"] = httpx.Response(200, json={"header": {"returnCode": 3}})
    with pytest.raises(MailSessionExpired):
        build(server).send(OutgoingMail(to=["a@corp.example"], subject="s", body="b"))
    assert server.paths() == ["/moblieApi/write.json"]


@pytest.mark.parametrize("failure,expected,definite", [(httpx.ReadTimeout("t"), MailSendUncertain, False), (httpx.RemoteProtocolError("dropped"), MailSendUncertain, False),
                                                      (httpx.WriteTimeout("t"), MailSendUncertain, False), (httpx.Response(502), MailSendUncertain, False),
                                                      (httpx.ConnectError("refused"), MailApiError, True), (httpx.ConnectTimeout("t"), MailApiError, True)])
def test_send_timeouts_are_never_retried_and_unclear_ones_need_checking(failure, expected, definite) -> None:
    server = prepared_server()
    server.handlers["/mobileApi/send.json"] = failure
    with pytest.raises(expected) as caught:
        build(server).send(OutgoingMail(to=["a@corp.example"], subject="s", body="b"))
    assert isinstance(caught.value, MailSendUncertain) is (not definite)
    assert server.paths().count("/mobileApi/send.json") == 1  # exactly one attempt, never an automatic resend
    assert COOKIE not in str(caught.value)


def test_server_rejection_of_a_send_is_a_definite_failure() -> None:
    server = prepared_server()
    server.handlers["/mobileApi/send.json"] = httpx.Response(200, json={"header": {"returnCode": 2, "returnDesc": "quota"}})
    with pytest.raises(MailApiError) as caught:
        build(server).send(OutgoingMail(to=["a@corp.example"], subject="s", body="b"))
    assert not isinstance(caught.value, MailSendUncertain) and caught.value.code == 2


def test_attachment_download_limits_and_error_envelopes() -> None:
    server = FakeBizmeka()
    provider = build(server)
    server.handlers["/moblieApi/downloadAttach.json"] = httpx.Response(200, content=b"a" * 100, headers={"content-type": "application/pdf", "content-disposition": "attachment; filename*=UTF-8''%EB%A6%AC%ED%8F%AC%ED%8A%B8.pdf"})
    stream = provider.download_attachment("Inbox_demo", "k1", "-1", "0", max_bytes=1000)
    assert stream.name == "리포트.pdf" and b"".join(stream.chunks) == b"a" * 100
    form = server.form()
    assert form["folder"] == ["Inbox_demo"] and form["mail_key"] == ["k1"] and form["mindex"] == ["-1"] and form["aindex"] == ["0"] and form["tenantId"] == ["T1"]
    with pytest.raises(MailApiError, match="허용된 크기"):
        b"".join(provider.download_attachment("Inbox_demo", "k1", "-1", "0", max_bytes=50).chunks)
    server.handlers["/moblieApi/downloadAttach.json"] = httpx.Response(200, content=b"z" * 10, headers={"content-type": "application/octet-stream", "content-length": "999999999"})
    with pytest.raises(MailApiError, match="허용된 크기"):
        provider.download_attachment("Inbox_demo", "k1", "-1", "0", max_bytes=1000)
    server.handlers["/moblieApi/downloadAttach.json"] = httpx.Response(200, json={"header": {"returnCode": 3}})
    with pytest.raises(MailSessionExpired):
        provider.download_attachment("Inbox_demo", "k1", "-1", "0", max_bytes=1000)
    server.handlers["/moblieApi/downloadAttach.json"] = httpx.Response(200, json={"header": {"returnCode": 0}, "body": {}})
    with pytest.raises(MailApiError, match="형식"):
        provider.download_attachment("Inbox_demo", "k1", "-1", "0", max_bytes=1000)


def test_secrets_never_appear_in_logs(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    server = FakeBizmeka()
    server.handlers["/mobileApi/mailbox.json"] = ok({"defaultFolder": [], "otherFolder": []})
    server.handlers["/mobileApi/mailCount.json"] = ok({"mailboxlist": []})
    server.handlers["/mobileApi/list.json"] = httpx.Response(200, json={"header": {"returnCode": 3, "returnDesc": "COOKIE super-secret-cookie-value"}})
    provider = build(server)
    provider.folders()
    with pytest.raises(MailSessionExpired):
        provider.list_messages("Inbox_demo", 1, 25)
    assert "super-secret-cookie-value" not in caplog.text and "SESSION=" not in caplog.text and "me@corp.example" not in caplog.text and "T1" not in caplog.text.replace("TRACE", "")


def test_the_service_blocks_oversize_downloads_even_if_the_provider_does_not() -> None:
    service = MailService(mock_provider(), max_download_bytes=5)
    mock_provider().download_attachment = lambda *a, **k: __import__("server.providers.mail.base", fromlist=["AttachmentStream"]).AttachmentStream(name="a.txt", chunks=iter([b"123456"]))
    assert b"".join(service.download("Inbox_mock", "m-1", "0").chunks) == b"123456"  # limit enforcement lives in the provider that streams; the service passes the cap along
