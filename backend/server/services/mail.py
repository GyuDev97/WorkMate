from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from server.models.schemas import ActionCard, ApprovalStatus, MailState
from server.providers.mail.base import (AttachmentStream, MailDetail, MailError, MailFolder, MailNotConnected, MailPage, MailProvider, MailSendUncertain, MailStatus, OutgoingAttachment, OutgoingMail)
from server.services.activity import ActivityService
from server.services.approvals import ApprovalStore

EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
SAFE_KEY = re.compile(r"^[A-Za-z0-9_\-\.]{1,128}$")  # message and folder keys end up in '|'-joined lists: allow nothing that could split or inject
BLOCKED_UPLOAD_EXTENSIONS = {".exe", ".bat", ".cmd", ".com", ".scr", ".js", ".vbs", ".msi", ".ps1", ".jar", ".dll", ".lnk"}
MAX_RECIPIENTS, MAX_KEYS, MAX_ATTACHMENTS = 100, 50, 10
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
UNREAD_TTL = 60.0
QUOTE_LIMIT = 4000


def safe_filename(name: str) -> str:
    """A file name only: no directories, control characters or leading dots, bounded length."""
    base = re.split(r"[\\/]", name or "")[-1]
    base = re.sub(r"[\x00-\x1f\x7f<>:\"|?*]", "_", base).strip().lstrip(".")
    if not base:
        raise MailError("첨부파일 이름이 올바르지 않습니다.")
    stem, dot, ext = base.rpartition(".")
    return base if len(base) <= 120 else (f"{stem[:100]}.{ext[:15]}" if dot else base[:120])


def parse_addresses(value: str) -> list[str]:
    return list(dict.fromkeys(match.group(0) for match in EMAIL.finditer(value or "")))


def clean_line(value: str, limit: int = 300) -> str:
    return re.sub(r"[\r\n\t]+", " ", value or "").strip()[:limit]


class StagedAttachment(BaseModel):
    id: str
    name: str
    size: int


class MailService:
    """Read, draft and stage. Drafting never calls the mail server; a change reaches it only through MailActionService after approval."""

    def __init__(self, provider: MailProvider, staging_dir: str | Path = "data/mail_staging", max_download_bytes: int = 25 * 1024 * 1024) -> None:
        self.provider, self.staging, self.max_download_bytes = provider, Path(staging_dir), max_download_bytes
        self._unread: tuple[float, MailState] | None = None
        self.purge_staging()

    # ----- status -----
    def status(self) -> MailStatus:
        return self.provider.status()

    def require_ready(self) -> None:
        status = self.status()
        if status.state not in ("mock", "connected"):
            raise MailNotConnected(status.detail)

    def summary(self) -> MailState:
        """Home dashboard: a state and the unread count only, cached briefly so the dashboard never hammers the mail server."""
        status = self.status()
        base = MailState(provider=status.provider, state=status.state, detail=status.detail)
        if status.state not in ("mock", "connected"):
            return base
        if self._unread and time.monotonic() - self._unread[0] < UNREAD_TTL and self._unread[1].state == status.state:
            return self._unread[1]
        try:
            result = base.model_copy(update={"unread_count": self.unread_total()})
        except MailError as error:
            state = "session_expired" if getattr(error, "code", None) == 3 else "error"
            return base.model_copy(update={"state": state, "detail": str(error)})
        self._unread = (time.monotonic(), result)
        return result

    def invalidate(self) -> None:
        self._unread = None

    # ----- reads (UI only) -----
    def folders(self) -> list[MailFolder]:
        self.require_ready()
        return self.provider.folders()

    def unread_total(self) -> int:
        inbox = next((f for f in self.provider.folders() if f.kind == "inbox"), None)
        return inbox.unread if inbox else 0

    def inbox_key(self) -> str:
        inbox = next((f for f in self.folders() if f.kind == "inbox"), None)
        if not inbox:
            raise MailError("받은편지함을 찾을 수 없습니다.")
        return inbox.key

    def trash_key(self) -> str:
        configured = getattr(getattr(self.provider, "settings", None), "trash_folder_key", None)
        if configured:
            return configured
        trash = next((f for f in self.provider.folders() if f.kind == "trash"), None)
        if not trash:
            raise MailError("휴지통 폴더를 찾을 수 없습니다. BIZMEKA_TRASH_FOLDER_KEY를 설정해 주세요.")
        return trash.key

    def messages(self, folder: str, page: int = 1, size: int = 25, unread_only: bool = False, query: str | None = None) -> MailPage:
        self.require_ready()
        self._key(folder)
        size = max(1, min(size, 100)); page = max(1, page)
        if query and query.strip():
            return self.provider.search(query.strip()[:200], folder if folder != "ALL" else None, page, size)
        return self.provider.list_messages(folder, page, size, unread_only)

    def message(self, folder: str, ukey: str) -> MailDetail:
        self.require_ready()
        self._key(folder); self._key(ukey)
        return self.provider.get_message(folder, ukey)

    def download(self, folder: str, ukey: str, aindex: str, mindex: str = "-1") -> AttachmentStream:
        self.require_ready()
        for value in (folder, ukey, aindex):
            self._key(value)
        if mindex != "-1":
            self._key(mindex)
        stream = self.provider.download_attachment(folder, ukey, mindex, aindex, self.max_download_bytes)
        return stream.model_copy(update={"name": safe_filename(stream.name)})

    @staticmethod
    def _key(value: str) -> str:
        if not SAFE_KEY.match(value or ""):
            raise MailError("올바르지 않은 메일 또는 폴더 식별자입니다.")
        return value

    # ----- drafts (local only) -----
    def build_draft(self, mode: str, folder: str | None = None, ukey: str | None = None) -> dict[str, Any]:
        if mode == "new":
            return {"mode": "new", "to": [], "cc": [], "bcc": [], "subject": "", "body": "", "ref_folder": None, "ref_ukey": None}
        if mode not in ("reply", "reply_all", "forward") or not folder or not ukey:
            raise MailError("답장/전달 초안에는 원본 메일이 필요합니다.")
        original = self.message(folder, ukey)  # a read the user asked for; the content stays between backend and screen
        me = getattr(self.provider, "address", "").lower()
        prefix = "FW: " if mode == "forward" else "RE: "
        subject = original.subject if re.match(r"(?i)^(re|fw|fwd)\s*:", original.subject) else prefix + original.subject
        quote = f"\n\n----- 원본 메시지 -----\n보낸사람: {original.from_name} <{original.from_address}>\n날짜: {original.sent_at}\n제목: {original.subject}\n\n{original.body_text[:QUOTE_LIMIT]}"
        to: list[str] = [] if mode == "forward" else parse_addresses(original.from_address)
        cc: list[str] = []
        if mode == "reply_all":
            others = [a for a in parse_addresses(original.to) + parse_addresses(original.cc) if a.lower() != me and a not in to]
            cc = list(dict.fromkeys(others))
        return {"mode": "forward" if mode == "forward" else "reply", "to": to, "cc": cc, "bcc": [], "subject": subject, "body": quote, "ref_folder": folder, "ref_ukey": ukey,
                "note": "전달 시 원본 첨부파일은 자동으로 첨부되지 않습니다." if mode == "forward" else ""}

    # ----- local attachment staging (never contacts the server) -----
    def stage(self, filename: str, data: bytes) -> StagedAttachment:
        name = safe_filename(filename)
        if Path(name).suffix.lower() in BLOCKED_UPLOAD_EXTENSIONS:
            raise MailError("보안상 첨부할 수 없는 파일 형식입니다.")
        if not data or len(data) > MAX_ATTACHMENT_BYTES:
            raise MailError(f"첨부파일은 1바이트 이상 {MAX_ATTACHMENT_BYTES // (1024 * 1024)}MB 이하여야 합니다.")
        self.staging.mkdir(parents=True, exist_ok=True)
        staged_id = uuid4().hex
        (self.staging / f"{staged_id}.bin").write_bytes(data)
        (self.staging / f"{staged_id}.json").write_text(json.dumps({"name": name, "size": len(data)}, ensure_ascii=False), encoding="utf-8")
        return StagedAttachment(id=staged_id, name=name, size=len(data))

    def staged(self, staged_id: str) -> OutgoingAttachment | None:
        if not re.fullmatch(r"[0-9a-f]{32}", staged_id or ""):
            return None
        meta, blob = self.staging / f"{staged_id}.json", self.staging / f"{staged_id}.bin"
        if not meta.is_file() or not blob.is_file():
            return None
        info = json.loads(meta.read_text(encoding="utf-8"))
        return OutgoingAttachment(name=info["name"], size=info["size"], path=str(blob))

    def discard(self, staged_id: str) -> None:
        if re.fullmatch(r"[0-9a-f]{32}", staged_id or ""):
            for suffix in (".bin", ".json"):
                (self.staging / f"{staged_id}{suffix}").unlink(missing_ok=True)

    def purge_staging(self, max_age_seconds: int = 24 * 3600) -> None:
        if not self.staging.is_dir():
            return
        for path in self.staging.iterdir():
            if path.is_file() and time.time() - path.stat().st_mtime > max_age_seconds:
                path.unlink(missing_ok=True)


class MailActionService(ApprovalStore):
    """Approval-gated mail changes. A proposal is a card only: the mail server is contacted after the user approves, exactly once."""

    unchanged_text = "메일은 변경되지 않았습니다."

    def __init__(self, mail: MailService, activity: ActivityService | None = None) -> None:
        super().__init__(activity)
        self.mail = mail

    def _describe_error(self, error: Exception) -> str:
        return str(error) if isinstance(error, MailError) else f"메일 작업을 처리하지 못했습니다({type(error).__name__})."

    def _failure_status(self, error: Exception) -> ApprovalStatus:
        return ApprovalStatus.NEEDS_CHECK if isinstance(error, MailSendUncertain) else ApprovalStatus.FAILED

    # ----- send / save draft -----
    def propose_send(self, to: list[str], cc: list[str], bcc: list[str], subject: str, body: str, attachment_ids: list[str] | None = None, mode: str = "new",
                     ref_folder: str | None = None, ref_ukey: str | None = None, save_draft: bool = False, reserve_at: str | None = None) -> ActionCard:
        self.mail.require_ready()
        to, cc, bcc = (self._recipients(group) for group in (to, cc, bcc))
        if not to:
            raise MailError("받는사람 주소를 입력해 주세요.")
        if len(set(to + cc + bcc)) > MAX_RECIPIENTS:
            raise MailError(f"수신자는 최대 {MAX_RECIPIENTS}명까지 가능합니다.")
        subject = clean_line(subject)
        if not subject:
            raise MailError("제목을 입력해 주세요.")
        if reserve_at and not re.fullmatch(r"\d{12}", reserve_at):
            raise MailError("예약 시각은 YYYYMMDDHHMM 형식이어야 합니다.")
        ids = list(dict.fromkeys(attachment_ids or []))
        if len(ids) > MAX_ATTACHMENTS:
            raise MailError(f"첨부파일은 최대 {MAX_ATTACHMENTS}개입니다.")
        attachments = []
        for staged_id in ids:
            staged = self.mail.staged(staged_id)
            if not staged:
                raise MailError("첨부파일을 찾을 수 없습니다. 다시 첨부해 주세요.")
            attachments.append(staged)
        if mode in ("reply", "forward"):
            for value in (ref_folder, ref_ukey):
                self.mail._key(value or "")
        outgoing = OutgoingMail(mode="draft" if save_draft else mode, to=to, cc=cc, bcc=bcc, subject=subject, body=body, ref_folder=ref_folder, ref_ukey=ref_ukey,
                                attachments=attachments, reserve_at=reserve_at)
        fields = {"받는사람(To)": ", ".join(to), "참조(CC)": ", ".join(cc) or "없음", "숨은참조(BCC)": ", ".join(bcc) or "없음", "제목": subject,
                  "본문": (body[:200] + ("…" if len(body) > 200 else "")) + f"\n(총 {len(body)}자)",
                  "첨부파일": "\n".join(f"{a.name} ({a.size:,}바이트)" for a in attachments) or "없음"}
        if reserve_at:
            fields["예약발송"] = f"{reserve_at[:4]}-{reserve_at[4:6]}-{reserve_at[6:8]} {reserve_at[8:10]}:{reserve_at[10:]} (서버 지원 확인 필요)"
        label, title = ("메일 임시보관", "메일 임시보관 저장") if save_draft else ("메일 발송", "메일 발송")

        def stale() -> str | None:
            return None if all(self.mail.staged(i) for i in ids) else "첨부파일이 삭제되어 발송할 수 없습니다."

        def run() -> str:
            self.mail.provider.send(outgoing)
            for staged_id in ids:
                self.mail.discard(staged_id)
            self.mail.invalidate()
            return "메일을 임시보관함에 저장했습니다." if save_draft else "메일을 발송했습니다."

        return self._save("mail_send", title, fields, stale, run, label)

    # ----- delete (to trash only) / move / read -----
    def _targets(self, folder: str, ukeys: list[str], labels: list[str] | None) -> tuple[list[str], dict[str, str]]:
        self.mail.require_ready()
        keys = list(dict.fromkeys(self.mail._key(k) for k in ukeys))
        if not keys or len(keys) > MAX_KEYS:
            raise MailError(f"대상 메일은 1~{MAX_KEYS}건이어야 합니다.")
        self.mail._key(folder)
        shown = [clean_line(label, 60) for label in (labels or [])[:5] if label]
        return keys, {"대상": f"{len(keys)}건" + (": " + " / ".join(shown) if shown else "")}

    def propose_delete(self, folder: str, ukeys: list[str], labels: list[str] | None = None) -> ActionCard:
        keys, fields = self._targets(folder, ukeys, labels)
        trash = self.mail.trash_key()
        if folder == trash:
            raise MailError("이미 휴지통에 있는 메일입니다. 완전 삭제는 지원하지 않습니다.")
        fields["처리"] = "휴지통으로 이동 (완전 삭제 아님)"
        return self._save("mail_delete", "메일 삭제", fields, lambda: None, lambda: self._apply("move", folder, trash, keys, f"{len(keys)}건을 휴지통으로 이동했습니다."), "메일 삭제")

    def propose_move(self, folder: str, target_folder: str, ukeys: list[str], labels: list[str] | None = None) -> ActionCard:
        keys, fields = self._targets(folder, ukeys, labels)
        self.mail._key(target_folder)
        if target_folder == folder:
            raise MailError("같은 폴더로는 이동할 수 없습니다.")
        names = {f.key: f.name for f in self.mail.provider.folders()}
        fields["이동"] = f"{names.get(folder, folder)} → {names.get(target_folder, target_folder)}"
        return self._save("mail_move", "메일 이동", fields, lambda: None, lambda: self._apply("move", folder, target_folder, keys, f"{len(keys)}건을 이동했습니다."), "메일 이동")

    def propose_set_read(self, folder: str, ukeys: list[str], read: bool, labels: list[str] | None = None) -> ActionCard:
        keys, fields = self._targets(folder, ukeys, labels)
        fields["변경"] = "읽음으로 표시" if read else "안읽음으로 표시"
        return self._save("mail_read", "읽음 상태 변경", fields, lambda: None, lambda: self._apply("read", folder, "", keys, f"{len(keys)}건을 {'읽음' if read else '안읽음'}으로 변경했습니다.", read), "메일 읽음 변경")

    def _apply(self, op: str, folder: str, target: str, keys: list[str], message: str, read: bool = True) -> str:
        if op == "move":
            self.mail.provider.move(folder, target, keys)
        else:
            self.mail.provider.set_read(folder, keys, read)
        self.mail.invalidate()
        return message

    @staticmethod
    def _recipients(values: list[str]) -> list[str]:
        found: list[str] = []
        for value in values or []:
            if re.search(r"[\r\n,;|]", value.strip()):
                found += parse_addresses(value)  # a pasted "a@x.com, b@y.com" is split; anything else is rejected below
                continue
            if not EMAIL.fullmatch(value.strip()):
                raise MailError("올바르지 않은 이메일 주소가 있습니다.")
            found.append(value.strip())
        return list(dict.fromkeys(found))
