from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from server.models.schemas import ActionCard
from server.providers.mail.base import MailError, MailNotConnected
from server.services.mail import MailActionService, MailService

ASSISTANT_PAGE = 10


@dataclass
class SplitResult:
    """One tool result, two audiences. `llm` is the only part the model ever receives (counts and status).
    `ui` goes to the screen alone: it is never added to the model input, the conversation database or a tool trace."""
    llm: Any
    ui: dict[str, Any] | None = None


def _failed(error: MailError) -> SplitResult:
    if isinstance(error, MailNotConnected):
        reason, message = "not_connected", "메일이 연결되어 있지 않습니다."
    elif getattr(error, "code", None) == 3:
        reason, message = "session_expired", "메일 세션이 만료되어 재인증이 필요합니다."
    else:
        reason, message = "error", "메일 작업에 실패했습니다."
    return SplitResult(llm={"success": False, "reason": reason, "message": message}, ui={"kind": "mail_error", "message": str(error)})


class MailTool:
    name = "mail"
    description = "Read mail counts and search for the screen; prepare outgoing mail for approval. Mail content never reaches the model."

    def __init__(self, service: MailService, actions: MailActionService) -> None:
        self.service, self.actions = service, actions

    def status(self) -> SplitResult:
        status = self.service.status()
        connected = status.state in ("mock", "connected")
        return SplitResult(llm={"success": True, "connected": connected, "mock": status.provider == "mock", "message": "메일 연결됨" if connected else "메일이 연결되어 있지 않습니다."},
                           ui={"kind": "mail_status", "state": status.state, "detail": status.detail, "mock": status.provider == "mock"})

    def unread_count(self) -> SplitResult:
        try:
            self.service.require_ready()
            count = self.service.unread_total()
        except MailError as error:
            return _failed(error)
        return SplitResult(llm={"success": True, "unread_count": count, "message": "미확인 메일 건수 조회 완료"},
                           ui={"kind": "mail_unread", "unread_count": count, "mock": self.service.provider.is_mock})

    def search(self, query: str, unread_only: bool = False) -> SplitResult:
        try:
            self.service.require_ready()
            folder = "ALL" if query.strip() else self.service.inbox_key()
            page = self.service.messages(folder, 1, ASSISTANT_PAGE, unread_only, query or None)
        except MailError as error:
            return _failed(error)
        items = [item.model_dump() for item in page.items]
        return SplitResult(llm={"success": True, "count": len(items), "more_pages": page.total_pages > 1, "message": "메일 조회 완료. 목록은 화면에 표시됩니다."},
                           ui={"kind": "mail_list", "query": query, "mock": self.service.provider.is_mock, "items": items, "more_pages": page.total_pages > 1})

    def propose_send(self, to: list[str], cc: list[str], bcc: list[str], subject: str, body: str) -> ActionCard | dict[str, Any]:
        try:
            return self.actions.propose_send(to, cc, bcc, subject, body)
        except MailError as error:
            return {"error": str(error)}
