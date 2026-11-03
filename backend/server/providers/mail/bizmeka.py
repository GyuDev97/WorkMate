from __future__ import annotations

import html
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Iterator
from pathlib import Path
from urllib.parse import unquote

import httpx

from server.providers.mail.base import (AttachmentStream, MailApiError, MailAttachment, MailDetail, MailFolder, MailListItem, MailNotConnected, MailPage, MailSendUncertain,
                                        MailSessionExpired, MailSettingError, MailStatus, OutgoingMail)
from server.providers.mail.sanitize import sanitize_html

logger = logging.getLogger("workmate.mail")

# Paths exactly as written in the interface spec (SPRO-DS-2240-04), including its apparent `moblieApi` typos.
# Every path can be overridden with BIZMEKA_PATH_<NAME> once the real route is confirmed with the Bizmeka team.
DEFAULT_PATHS = {
    "mail_count": "/mobileApi/mailCount.json", "mailbox": "/mobileApi/mailbox.json", "list": "/mobileApi/list.json", "search": "/mobileApi/search.json",
    "view": "/mobileApi/view.json", "download": "/moblieApi/downloadAttach.json", "write": "/moblieApi/write.json", "upload": "/mobileApi/uploadFile.json",
    "send": "/mobileApi/send.json", "move": "/mobileApi/move.json", "read_check": "/mobileApi/readCheck.json",
}
# Parameters the spec lists as non-null for downloadAttach but whose meaning for a download is not documented: sent empty.
_DOWNLOAD_BLANKS = ["emailAddress", "fromAddress", "fromAddressNm", "isSend", "toAddress", "toAddressNm", "ccAddress", "ccAddressNm", "bccAddress", "bccAddressNm",
                    "emailSubject", "emailContent", "attachFileName", "attachFileSize", "itemId"]
_UPLOAD_BLANKS = ["emailAddress", "fromAddress", "fromAddressNm", "isSend", "toAddress", "toAddressNm", "ccAddress", "ccAddressNm", "bccAddress", "bccAddressNm",
                  "emailSubject", "emailContent", "attachFileName", "attachFileSize", "itemId"]
_UNCERTAIN = (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout, httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError)


@dataclass
class BizmekaSettings:
    base_url: str = ""
    tenant_id: str = ""
    portal_id: str = ""
    user_id: str = ""
    mail_user_id: str = ""
    mac_address: str = ""
    from_name: str = ""
    encoding: str = "form"                      # form (x-www-form-urlencoded) | json: the spec does not say, so it is configurable
    connect_timeout: float = 5.0
    read_timeout: float = 20.0
    send_timeout: float = 60.0
    allow_live: bool = False                    # interlock: no request is ever built until an operator enables live calls
    trash_folder_key: str | None = None
    max_download_bytes: int = 25 * 1024 * 1024
    cookie: str | None = field(default=None, repr=False)
    paths: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_PATHS))

    @classmethod
    def from_env(cls, cookie: str | None = None) -> "BizmekaSettings":
        env = os.getenv
        paths = {name: env(f"BIZMEKA_PATH_{name.upper()}", default) for name, default in DEFAULT_PATHS.items()}
        return cls(
            base_url=env("BIZMEKA_BASE_URL", "").rstrip("/"), tenant_id=env("BIZMEKA_TENANT_ID", ""), portal_id=env("BIZMEKA_PORTAL_ID", ""), user_id=env("BIZMEKA_USER_ID", ""),
            mail_user_id=env("BIZMEKA_MAIL_USER_ID", ""), mac_address=env("BIZMEKA_MAC_ADDRESS", ""), from_name=env("BIZMEKA_FROM_NAME", ""),
            encoding=env("BIZMEKA_REQUEST_ENCODING", "form").lower(), read_timeout=float(env("BIZMEKA_READ_TIMEOUT", "20")), send_timeout=float(env("BIZMEKA_SEND_TIMEOUT", "60")),
            allow_live=env("WORKMATE_MAIL_ALLOW_LIVE", "0") == "1", trash_folder_key=env("BIZMEKA_TRASH_FOLDER_KEY") or None,
            max_download_bytes=int(env("BIZMEKA_MAX_DOWNLOAD_MB", "25")) * 1024 * 1024,
            cookie=cookie or env("BIZMEKA_SESSION_COOKIE") or None, paths=paths,
        )

    def missing(self) -> list[str]:
        required = {"BIZMEKA_BASE_URL": self.base_url, "BIZMEKA_TENANT_ID": self.tenant_id, "BIZMEKA_PORTAL_ID": self.portal_id, "BIZMEKA_USER_ID": self.user_id,
                    "BIZMEKA_MAIL_USER_ID": self.mail_user_id}
        return [name for name, value in required.items() if not value]


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _kind(key: str, name: str) -> str:
    lowered = key.lower()
    for prefix, kind in (("inbox", "inbox"), ("sent", "sent"), ("trash", "trash"), ("draft", "draft"), ("temp", "draft"), ("spam", "spam"), ("junk", "spam")):
        if lowered.startswith(prefix):
            return kind
    for needle, kind in (("받은", "inbox"), ("보낸", "sent"), ("휴지통", "trash"), ("임시", "draft"), ("스팸", "spam")):
        if needle in name:
            return kind
    return "custom"


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _address_list(values: list[str]) -> str:
    return "|".join(values)


class BizmekaMailProvider:
    """Bizmeka mobile mail API adapter. It never logs request bodies, cookies or responses, and never retries a send."""
    name = "bizmeka"
    is_mock = False

    def __init__(self, settings: BizmekaSettings | None = None, client: httpx.Client | None = None) -> None:
        self.settings = settings or BizmekaSettings.from_env()
        self._client = client

    @property
    def address(self) -> str:
        return self.settings.mail_user_id

    # ----- status / guards -----
    def status(self) -> MailStatus:
        s = self.settings
        if s.missing():
            return MailStatus(provider="bizmeka", state="not_connected", detail=f"Bizmeka 설정이 필요합니다: {', '.join(s.missing())}")
        if not s.allow_live:
            return MailStatus(provider="bizmeka", state="not_connected", detail="설정은 준비됐지만 운영 서버 호출이 꺼져 있습니다(사내 IT 검증 후 WORKMATE_MAIL_ALLOW_LIVE=1).")
        if not s.cookie:
            return MailStatus(provider="bizmeka", state="not_connected", detail="세션 정보가 없습니다. 인증 방식을 사내 IT와 확인한 뒤 BIZMEKA_SESSION_COOKIE(또는 OS 보안 저장소)를 설정하세요.", live_enabled=True)
        return MailStatus(provider="bizmeka", state="connected", detail="Bizmeka 메일 연결 설정 완료(세션 유효성은 호출 시 확인)", live_enabled=True)

    def _guard(self) -> None:
        status = self.status()
        if status.state != "connected":
            raise MailNotConnected(status.detail)

    # ----- transport -----
    def _identity(self) -> dict[str, Any]:
        s = self.settings
        return {"tenantId": s.tenant_id, "macAddress": s.mac_address, "portalId": s.portal_id, "userId": s.user_id, "mailUserId": s.mail_user_id}

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=httpx.Timeout(self.settings.read_timeout, connect=self.settings.connect_timeout))
        return self._client

    def _headers(self) -> dict[str, str]:
        return {"Cookie": self.settings.cookie} if self.settings.cookie else {}

    def _request(self, key: str, params: dict[str, Any], timeout: float | None = None, files: dict[str, Any] | None = None, stream: bool = False) -> httpx.Response:
        self._guard()
        url = self.settings.base_url + self.settings.paths[key]
        body: dict[str, Any] = {**self._identity(), **params}
        kwargs: dict[str, Any] = {"headers": self._headers()}
        if files is not None:
            kwargs.update(data=body, files=files)  # uploads are multipart by definition
        elif self.settings.encoding == "json":
            kwargs["json"] = body
        else:
            kwargs["data"] = body
        client = self._http()
        request = client.build_request("POST", url, timeout=httpx.Timeout(timeout or self.settings.read_timeout, connect=self.settings.connect_timeout), **kwargs)
        response = client.send(request, stream=stream)
        logger.info("mail_api op=%s status=%s", key, response.status_code)
        return response

    @staticmethod
    def _check_http(response: httpx.Response) -> None:
        if response.status_code in (401, 403):
            raise MailSessionExpired("메일 인증이 필요합니다. 세션이 만료되었거나 권한이 없습니다.")
        if response.status_code >= 400:
            raise MailApiError(f"메일 서버가 오류를 반환했습니다(HTTP {response.status_code}).", response.status_code)

    @staticmethod
    def _unwrap(response: httpx.Response) -> dict[str, Any]:
        """Spec envelope: header{returnCode, returnDesc} + body{...}. 0 = success, 3 = session timeout, 4 = mail setup failure."""
        BizmekaMailProvider._check_http(response)
        try:
            data = response.json()
        except ValueError as error:
            raise MailApiError("메일 서버 응답을 해석하지 못했습니다.") from error
        header = data.get("header") if isinstance(data.get("header"), dict) else data
        code = _int(header.get("returnCode"), -1)
        if code == 3:
            raise MailSessionExpired("메일 세션이 만료되었습니다. 다시 인증해 주세요.")
        if code == 4:
            raise MailSettingError("메일 서버의 메일 설정을 확인할 수 없습니다(returnCode 4).")
        if code != 0:
            raise MailApiError(f"메일 서버 오류(코드 {code}).", code)
        body = data.get("body")
        return body if isinstance(body, dict) else {}

    def _call(self, key: str, params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        try:
            response = self._request(key, params, **kwargs)
        except (httpx.TimeoutException, httpx.TransportError) as error:
            raise MailApiError("메일 서버에 연결하지 못했습니다(시간 초과 또는 네트워크 오류).") from error
        return self._unwrap(response)

    # ----- reads -----
    def folders(self) -> list[MailFolder]:
        boxes = self._call("mailbox", {})
        counts = self._call("mail_count", {"folder": "", "folderBounds": ""})
        unread = {_text(item.get("folderKey")): _int(item.get("newmailcount")) for item in counts.get("mailboxlist", []) or []}
        result: list[MailFolder] = []
        for group in ("defaultFolder", "otherFolder"):
            for item in boxes.get(group, []) or []:
                key, name = _text(item.get("mailBoxKey")), _text(item.get("dirname"))
                result.append(MailFolder(key=key, name=name, total=_int(item.get("mailCount")), unread=unread.get(key, 0), kind=_kind(key, name) if group == "defaultFolder" else "custom"))
        return result

    @staticmethod
    def _rows(body: dict[str, Any], folder: str, page: int, size: int) -> MailPage:
        items = []
        for row in body.get("maillist", []) or []:
            items.append(MailListItem(
                ukey=_text(row.get("ukey")), folder=_text(row.get("dirkey")) or folder, subject=_text(row.get("subject")) or "(제목 없음)",
                sender_name=_text(row.get("fromname")), sender_address=_text(row.get("fromaddr")), recipients=_text(row.get("sender_text") or row.get("sender")),
                sent_at=_text(row.get("senddate")), unread=_text(row.get("isseen")) == "0", important=_text(row.get("important")) == "3",
                answered=_text(row.get("isanswered")) not in ("", "0"), size=_int(row.get("mailsize"), 0) or None))
        info = body.get("page") if isinstance(body.get("page"), dict) else {}
        return MailPage(items=items, page=_int(info.get("cpage"), page), page_size=size, total_pages=max(1, _int(info.get("total"), 1)))

    def list_messages(self, folder: str, page: int, size: int, unread_only: bool = False) -> MailPage:
        params: dict[str, Any] = {"folderId": folder, "folder": folder, "listSize": size, "page": page, "cpage": page, "sort": "recvdate", "order": "desc"}
        if unread_only:
            params["isNewmail"] = "1"
        return self._rows(self._call("list", params), folder, page, size)

    def search(self, query: str, folder: str | None, page: int, size: int, target: str = "S_C") -> MailPage:
        params = {"folderId": folder or "", "folder": folder or "", "keyword": query, "keyword_target": target, "mailbox": folder or "ALL",
                  "listSize": size, "page": page, "cpage": page, "sort": "recvdate", "order": "desc"}
        return self._rows(self._call("search", params), folder or "ALL", page, size)

    def get_message(self, folder: str, ukey: str) -> MailDetail:
        body = self._call("view", {"currentFolderId": folder, "itemId": ukey, "folder": folder, "ukey": ukey})
        raw_attachments = body.get("attachementList") or {}
        if isinstance(raw_attachments, dict):
            raw_attachments = [raw_attachments] if raw_attachments.get("fileName") or raw_attachments.get("aindex") is not None else list(raw_attachments.values())
        attachments = [MailAttachment(aindex=_text(a.get("aindex")), mindex=_text(a.get("mindex")) or "-1", name=_text(a.get("fileName")) or "attachment", size=_int(a.get("fileSize"), 0) or None)
                       for a in raw_attachments if isinstance(a, dict)]
        safe, text = sanitize_html(_text(body.get("content")))
        return MailDetail(ukey=_text(body.get("ukey")) or ukey, folder=_text(body.get("folder") or body.get("dirkey")) or folder, subject=_text(body.get("subject")) or "(제목 없음)",
                          from_name=_text(body.get("fromname")), from_address=_text(body.get("fromaddr") or body.get("from")), to=_text(body.get("to")), cc=_text(body.get("cc")),
                          bcc=_text(body.get("bcc")), sent_at=_text(body.get("senddate")), body_html=safe, body_text=text, attachments=attachments)

    def download_attachment(self, folder: str, ukey: str, mindex: str, aindex: str, max_bytes: int) -> AttachmentStream:
        params = {name: "" for name in _DOWNLOAD_BLANKS} | {"folder": folder, "mail_key": ukey, "mindex": mindex, "aindex": aindex, "itemId": ukey}
        try:
            response = self._request("download", params, stream=True)
        except (httpx.TimeoutException, httpx.TransportError) as error:
            raise MailApiError("메일 서버에 연결하지 못했습니다(시간 초과 또는 네트워크 오류).") from error
        try:
            self._check_http(response)
            kind = response.headers.get("content-type", "")
            if "json" in kind:  # the spec does not describe the download body; a JSON body is treated as the error envelope
                response.read()
                self._unwrap(response)
                raise MailApiError("첨부파일 응답 형식을 확인할 수 없습니다.")
            declared = _int(response.headers.get("content-length"), 0)
            if declared and declared > max_bytes:
                raise MailApiError("첨부파일이 허용된 크기를 초과합니다.")
        except Exception:
            response.close()
            raise
        name = "attachment"
        match = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", response.headers.get("content-disposition", ""), re.I)
        if match:
            name = unquote(match.group(1))  # RFC 5987 percent-encoding; the service sanitizes the result again

        def chunks() -> Iterator[bytes]:
            total = 0
            try:
                for chunk in response.iter_bytes(64 * 1024):
                    total += len(chunk)
                    if total > max_bytes:
                        raise MailApiError("첨부파일이 허용된 크기를 초과합니다.")
                    yield chunk
            except httpx.HTTPError as error:
                raise MailApiError("첨부파일을 받는 중 연결이 끊어졌습니다.") from error
            finally:
                response.close()

        return AttachmentStream(name=name, content_type=kind.split(";")[0] or "application/octet-stream", chunks=chunks())

    # ----- writes: reached only after the user approves an Action Card -----
    def _write_params(self, mail: OutgoingMail, send_flag: str) -> dict[str, Any]:
        s = self.settings
        html_body = "".join(f"<p>{html.escape(line) or '<br>'}</p>" for line in mail.body.split("\n"))
        first = {"new": "0", "reply": "1", "forward": "2", "draft": "0"}[mail.mode]
        return {
            "emailAddress": s.mail_user_id, "fromAddress": s.mail_user_id, "fromAddressNm": s.from_name, "isSend": send_flag,
            "toAddress": _address_list(mail.to), "toAddressNm": "", "ccAddress": _address_list(mail.cc), "ccAddressNm": "", "bccAddress": _address_list(mail.bcc), "bccAddressNm": "",
            "emailSubject": mail.subject, "emailContent": html_body, "itemId": mail.ref_ukey or "", "ukey": mail.ref_ukey or "", "first": first,
            "body": html_body, "body_src": mail.body, "fromname": s.from_name, "fromaddr": s.mail_user_id, "to": ",".join(mail.to), "cc": ",".join(mail.cc), "bcc": ",".join(mail.bcc),
            "subject": mail.subject, "characterset": "utf-8",
        }

    def send(self, mail: OutgoingMail) -> None:
        """prepare (write.json) -> upload attachments -> send.json. Nothing is retried; an unclear send result raises MailSendUncertain."""
        send_flag = {"new": "S", "reply": "R", "forward": "F", "draft": "T"}[mail.mode]
        base = self._write_params(mail, send_flag)
        prepared = self._call("write", {"isSend": send_flag, "first": {"new": "", "reply": "1", "forward": "2", "draft": "3"}[mail.mode], "ukey": mail.ref_ukey or "", "itemId": mail.ref_ukey or ""})
        temp_key = _text((prepared.get("MailWriteForm") or {}).get("tempKey"))
        if not temp_key:
            raise MailApiError("메일 작성 준비 응답에 임시 키가 없습니다.")
        keys: list[str] = []
        for attachment in mail.attachments:
            with Path(attachment.path).open("rb") as handle:
                result = self._call("upload", {name: "" for name in _UPLOAD_BLANKS} | {"isSend": send_flag}, files={"file": (attachment.name, handle)})
            if _text(result.get("result")) != "1" or not result.get("filekey"):
                raise MailApiError("첨부파일 업로드에 실패했습니다.")
            keys.append(_text(result["filekey"]))
        params = base | {
            "tempKey": temp_key, "tempsave": "1" if mail.mode == "draft" else "0",
            "attachFileName": "|".join(a.name for a in mail.attachments), "attachFileSize": "|".join(str(a.size) for a in mail.attachments),
            "attachments": ";".join(f"{key}:0:1" for key in keys),  # "<filekey>:0:1" copied from the spec sample; the meaning of 0 and 1 needs confirmation
        }
        if mail.reserve_at:
            params["reserverTime"] = mail.reserve_at  # spelled as in the spec
        try:
            response = self._request("send", params, timeout=self.settings.send_timeout)
        except httpx.ConnectError as error:
            raise MailApiError("메일 서버에 연결하지 못해 발송하지 않았습니다.") from error
        except httpx.ConnectTimeout as error:
            raise MailApiError("메일 서버에 연결하지 못해 발송하지 않았습니다.") from error
        except _UNCERTAIN as error:
            raise MailSendUncertain("발송 요청 후 응답을 받지 못했습니다. 메일이 발송되었는지 알 수 없습니다.") from error
        except httpx.TransportError as error:
            raise MailSendUncertain("발송 요청 중 통신 오류가 발생했습니다. 메일이 발송되었는지 알 수 없습니다.") from error
        if response.status_code >= 500:
            raise MailSendUncertain(f"메일 서버가 오류(HTTP {response.status_code})를 반환했습니다. 발송 여부를 알 수 없습니다.")
        self._unwrap(response)

    def move(self, source_folder: str, target_folder: str, ukeys: list[str]) -> None:
        self._call("move", {"currentFolderId": source_folder, "folderId": target_folder, "itemId": "|".join(ukeys), "isAllMove": "0", "isContinue": "0",
                            "source_mbox": source_folder, "target_mbox": target_folder, "Dmail[]": ukeys})

    def set_read(self, folder: str, ukeys: list[str], read: bool) -> None:
        flag = "1" if read else "0"
        self._call("read_check", {"currentFolderId": folder, "itemId": "|".join(ukeys), "isRead": flag, "isseen": flag, "Dmail[]": ukeys})
