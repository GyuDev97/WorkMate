from __future__ import annotations

from typing import Iterator, Literal, Protocol

from pydantic import BaseModel, Field


class MailError(RuntimeError):
    """A safe, user-displayable mail failure. The message never contains addresses, subjects, cookies or server payloads."""
    code: int | None = None


class MailNotConnected(MailError):
    """Mail is not configured, or live calls are not enabled."""


class MailSessionExpired(MailError):
    """Bizmeka returnCode 3: the session timed out and the user must authenticate again."""
    code = 3


class MailSettingError(MailError):
    """Bizmeka returnCode 4: mail settings failed on the server side."""
    code = 4


class MailApiError(MailError):
    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class MailSendUncertain(MailError):
    """A send request may or may not have been delivered (timeout / dropped connection). Never retried automatically."""
    uncertain = True


class MailFolder(BaseModel):
    key: str
    name: str
    total: int = 0
    unread: int = 0
    kind: Literal["inbox", "sent", "trash", "draft", "spam", "other", "custom"] = "other"


class MailListItem(BaseModel):
    ukey: str
    folder: str
    subject: str
    sender_name: str = ""
    sender_address: str = ""
    recipients: str = ""
    sent_at: str = ""
    unread: bool = False
    important: bool = False
    answered: bool = False
    size: int | None = None


class MailPage(BaseModel):
    items: list[MailListItem] = []
    page: int = 1
    page_size: int = 25
    total_pages: int = 1


class MailAttachment(BaseModel):
    aindex: str
    mindex: str = "-1"
    name: str
    size: int | None = None


class MailDetail(BaseModel):
    ukey: str
    folder: str
    subject: str
    from_name: str = ""
    from_address: str = ""
    to: str = ""
    cc: str = ""
    bcc: str = ""
    sent_at: str = ""
    body_html: str = ""   # already sanitized; render only inside a sandboxed frame
    body_text: str = ""
    attachments: list[MailAttachment] = []


class MailStatus(BaseModel):
    provider: Literal["none", "mock", "bizmeka"]
    state: Literal["mock", "connected", "not_connected", "session_expired", "error"]
    detail: str
    live_enabled: bool = False


class OutgoingAttachment(BaseModel):
    name: str
    size: int
    path: str  # a file in the local staging directory


class OutgoingMail(BaseModel):
    mode: Literal["new", "reply", "forward", "draft"] = "new"
    to: list[str] = Field(min_length=1)
    cc: list[str] = []
    bcc: list[str] = []
    subject: str
    body: str
    ref_folder: str | None = None
    ref_ukey: str | None = None
    attachments: list[OutgoingAttachment] = []
    reserve_at: str | None = None   # YYYYMMDDHHMM, only when the server confirms support


class AttachmentStream(BaseModel):
    model_config = {"arbitrary_types_allowed": True}
    name: str
    content_type: str = "application/octet-stream"
    chunks: Iterator[bytes]


class MailProvider(Protocol):
    name: str
    is_mock: bool
    address: str

    def status(self) -> MailStatus: ...
    def folders(self) -> list[MailFolder]: ...
    def list_messages(self, folder: str, page: int, size: int, unread_only: bool = False) -> MailPage: ...
    def search(self, query: str, folder: str | None, page: int, size: int, target: str = "S_C") -> MailPage: ...
    def get_message(self, folder: str, ukey: str) -> MailDetail: ...
    def download_attachment(self, folder: str, ukey: str, mindex: str, aindex: str, max_bytes: int) -> AttachmentStream: ...
    def send(self, mail: OutgoingMail) -> None: ...
    def move(self, source_folder: str, target_folder: str, ukeys: list[str]) -> None: ...
    def set_read(self, folder: str, ukeys: list[str], read: bool) -> None: ...
