from __future__ import annotations

from typing import Any

from server.providers.mail.base import (AttachmentStream, MailApiError, MailAttachment, MailDetail, MailFolder, MailListItem, MailPage, MailStatus, OutgoingMail)
from server.providers.mail.sanitize import sanitize_html

MOCK_NOTICE = "Mock 메일입니다. 실제 회사 메일이 아닙니다."
ATTACHMENT_BYTES = b"MOCK ATTACHMENT CONTENT"

_FOLDERS = [
    MailFolder(key="Inbox_mock", name="받은편지함", kind="inbox"), MailFolder(key="Sent_mock", name="보낸편지함", kind="sent"),
    MailFolder(key="Draft_mock", name="임시보관함", kind="draft"), MailFolder(key="Trash_mock", name="휴지통", kind="trash"),
    MailFolder(key="561ef7433fba3a72ca9b8a08", name="영업자료", kind="custom"),
]


def _msg(ukey: str, folder: str, subject: str, name: str, address: str, sent_at: str, unread: bool, body: str, attachments: list[tuple[str, int]] | None = None) -> dict[str, Any]:
    return {"ukey": ukey, "folder": folder, "subject": subject, "from_name": name, "from_address": address, "to": "me@mock.invalid", "cc": "", "bcc": "",
            "sent_at": sent_at, "unread": unread, "body": body, "attachments": attachments or []}


class MockMailProvider:
    """Offline provider with fixed, obviously fake data (RFC 2606 .invalid addresses). Records every change for tests."""
    name = "mock"
    is_mock = True
    address = "me@mock.invalid"

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = [
            _msg("m-1", "Inbox_mock", "[MOCK] 분기 회의 안내", "목업 발신자", "sender1@mock.invalid", "2026.11.03 09:10", True, "<p>MOCK-BODY-ALPHA 회의는 <b>내일</b> 입니다.</p><script>alert(1)</script>", [("mock-report.txt", len(ATTACHMENT_BYTES))]),
            _msg("m-2", "Inbox_mock", "[MOCK] 서버 점검 공지", "목업 운영팀", "ops@mock.invalid", "2026.11.03 08:00", True, "<p>MOCK-BODY-BETA</p>"),
            _msg("m-3", "Inbox_mock", "[MOCK] 지난 주 보고", "목업 팀장", "lead@mock.invalid", "2026.10.30 17:20", False, "<p>MOCK-BODY-GAMMA</p>"),
            _msg("m-4", "Sent_mock", "[MOCK] 보낸 메일 예시", "나", "me@mock.invalid", "2026.11.01 10:00", False, "<p>MOCK-BODY-SENT</p>"),
        ]
        self.calls: list[tuple[str, Any]] = []      # every provider call
        self.sent: list[OutgoingMail] = []
        self.fail_next: Exception | None = None     # test hook

    def _maybe_fail(self) -> None:
        if self.fail_next:
            error, self.fail_next = self.fail_next, None
            raise error

    def mutations(self) -> list[str]:
        return [name for name, _ in self.calls if name in {"send", "move", "set_read"}]

    def status(self) -> MailStatus:
        return MailStatus(provider="mock", state="mock", detail=MOCK_NOTICE, live_enabled=False)

    def folders(self) -> list[MailFolder]:
        self.calls.append(("folders", None)); self._maybe_fail()
        result = []
        for folder in _FOLDERS:
            inside = [m for m in self.messages if m["folder"] == folder.key]
            result.append(folder.model_copy(update={"total": len(inside), "unread": sum(m["unread"] for m in inside)}))
        return result

    @staticmethod
    def _item(m: dict[str, Any]) -> MailListItem:
        return MailListItem(ukey=m["ukey"], folder=m["folder"], subject=m["subject"], sender_name=m["from_name"], sender_address=m["from_address"], recipients=m["to"], sent_at=m["sent_at"], unread=m["unread"], size=1024)

    def _page(self, found: list[dict[str, Any]], page: int, size: int) -> MailPage:
        pages = max(1, -(-len(found) // size))
        return MailPage(items=[self._item(m) for m in found[(page - 1) * size: page * size]], page=page, page_size=size, total_pages=pages)

    def list_messages(self, folder: str, page: int, size: int, unread_only: bool = False) -> MailPage:
        self.calls.append(("list", (folder, page, size, unread_only))); self._maybe_fail()
        found = [m for m in self.messages if m["folder"] == folder and (m["unread"] or not unread_only)]
        return self._page(sorted(found, key=lambda m: m["sent_at"], reverse=True), page, size)

    def search(self, query: str, folder: str | None, page: int, size: int, target: str = "S_C") -> MailPage:
        self.calls.append(("search", (query, folder, page, size, target))); self._maybe_fail()
        needle = query.casefold()
        found = [m for m in self.messages if (not folder or m["folder"] == folder) and (needle in m["subject"].casefold() or (target != "SUBJECT" and needle in m["body"].casefold()))]
        return self._page(sorted(found, key=lambda m: m["sent_at"], reverse=True), page, size)

    def get_message(self, folder: str, ukey: str) -> MailDetail:
        self.calls.append(("get_message", (folder, ukey))); self._maybe_fail()
        for m in self.messages:
            if m["ukey"] == ukey and m["folder"] == folder:
                safe, text = sanitize_html(m["body"])
                return MailDetail(ukey=ukey, folder=folder, subject=m["subject"], from_name=m["from_name"], from_address=m["from_address"], to=m["to"], cc=m["cc"], bcc=m["bcc"],
                                  sent_at=m["sent_at"], body_html=safe, body_text=text,
                                  attachments=[MailAttachment(aindex=str(i), name=n, size=s) for i, (n, s) in enumerate(m["attachments"])])
        raise MailApiError("메일을 찾을 수 없습니다.", 1)

    def download_attachment(self, folder: str, ukey: str, mindex: str, aindex: str, max_bytes: int) -> AttachmentStream:
        self.calls.append(("download", (folder, ukey, mindex, aindex))); self._maybe_fail()
        detail = self.get_message(folder, ukey)
        found = next((a for a in detail.attachments if a.aindex == aindex), None)
        if not found:
            raise MailApiError("첨부파일을 찾을 수 없습니다.", 1)
        return AttachmentStream(name=found.name, content_type="text/plain", chunks=iter([ATTACHMENT_BYTES]))

    def send(self, mail: OutgoingMail) -> None:
        self.calls.append(("send", mail.subject)); self._maybe_fail()
        self.sent.append(mail)

    def move(self, source_folder: str, target_folder: str, ukeys: list[str]) -> None:
        self.calls.append(("move", (source_folder, target_folder, tuple(ukeys)))); self._maybe_fail()
        for m in self.messages:
            if m["ukey"] in ukeys and m["folder"] == source_folder:
                m["folder"] = target_folder

    def set_read(self, folder: str, ukeys: list[str], read: bool) -> None:
        self.calls.append(("set_read", (folder, tuple(ukeys), read))); self._maybe_fail()
        for m in self.messages:
            if m["ukey"] in ukeys and m["folder"] == folder:
                m["unread"] = not read
