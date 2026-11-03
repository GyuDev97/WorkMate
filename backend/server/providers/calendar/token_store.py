from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Protocol

logger = logging.getLogger("workmate.calendar")
KEYRING_SERVICE = "workMate"
KEYRING_ACCOUNT = "google-calendar-oauth"


class OAuthTokenStore(Protocol):
    label: str

    def read(self) -> str | None: ...
    def write(self, value: str) -> None: ...
    def delete(self) -> None: ...


class FileTokenStore:
    """A replaceable local store; the file is private to the current user (0600)."""
    label = "로컬 파일(0600)"

    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> str | None:
        try:
            return self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None

    def write(self, value: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(value, encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)
        os.chmod(self.path, 0o600)

    def delete(self) -> None:
        self.path.unlink(missing_ok=True)


class KeyringTokenStore:
    """OS credential store (macOS Keychain, Windows Credential Locker, Secret Service). The token never touches disk."""
    label = "OS 보안 저장소"

    def __init__(self, keyring_module: object | None = None, service: str = KEYRING_SERVICE, account: str = KEYRING_ACCOUNT) -> None:
        if keyring_module is None:
            import keyring as keyring_module  # type: ignore[no-redef]
        self._keyring, self.service, self.account = keyring_module, service, account

    def read(self) -> str | None:
        return self._keyring.get_password(self.service, self.account)  # type: ignore[attr-defined]

    def write(self, value: str) -> None:
        self._keyring.set_password(self.service, self.account, value)  # type: ignore[attr-defined]

    def delete(self) -> None:
        try:
            self._keyring.delete_password(self.service, self.account)  # type: ignore[attr-defined]
        except Exception as error:  # an absent entry is already "deleted"
            if type(error).__name__ != "PasswordDeleteError":
                raise


def default_token_store(file_path: Path) -> OAuthTokenStore:
    """WORKMATE_TOKEN_STORE=auto (default) prefers the OS keychain and falls back to the 0600 file; keyring/file force one."""
    mode = os.getenv("WORKMATE_TOKEN_STORE", "auto").lower()
    if mode != "file":
        try:
            import keyring
            backend = keyring.get_keyring()
            if type(backend).__module__.rsplit(".", 1)[-1] in {"fail", "null", "chainer_empty"}:
                raise RuntimeError("no usable keyring backend")
            return KeyringTokenStore(keyring)
        except Exception as error:
            if mode == "keyring":
                raise
            logger.warning("keyring_unavailable type=%s; falling back to file token store", type(error).__name__)
    return FileTokenStore(file_path)
