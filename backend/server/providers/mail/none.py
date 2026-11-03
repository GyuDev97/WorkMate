from __future__ import annotations

from typing import NoReturn

from server.providers.mail.base import MailNotConnected, MailStatus

HINT = "메일이 연결되지 않았습니다. WORKMATE_MAIL_PROVIDER를 bizmeka(운영) 또는 mock(개발용)으로 설정하세요."


class UnavailableMailProvider:
    """Used when no provider is configured: every call fails closed with a clear message; nothing is fabricated."""
    name = "none"
    is_mock = False
    address = ""

    def status(self) -> MailStatus:
        return MailStatus(provider="none", state="not_connected", detail=HINT)

    def _fail(self, *_args, **_kwargs) -> NoReturn:
        raise MailNotConnected(HINT)

    folders = list_messages = search = get_message = download_attachment = send = move = set_read = _fail
