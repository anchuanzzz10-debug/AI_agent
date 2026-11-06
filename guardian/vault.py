"""Encrypted credential vault with one key per project.

* A master secret is derived from your passphrase with scrypt (salt stored in
  data/system/). The passphrase itself is never stored.
* Each project's key is derived from the master secret with HKDF using the
  project id, so project A's key cannot decrypt project B's file.
* Each project's credentials live in their own file inside that project's
  directory. A ProjectCredentials object only knows its own key and file.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .storage import _safe_project_dir

_VERIFIER_PLAINTEXT = b"guardian-vault-v1"


class VaultError(Exception):
    pass


def _derive(master: bytes, info: str) -> bytes:
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
               info=info.encode()).derive(master)
    return base64.urlsafe_b64encode(key)


class _EncryptedFile:
    def __init__(self, path: Path, fernet_key: bytes):
        self._path = path
        self._fernet = Fernet(fernet_key)

    def load(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._fernet.decrypt(self._path.read_bytes()))
        except InvalidToken as exc:
            raise VaultError(f"Cannot decrypt {self._path.name}: wrong key or corrupted file") from exc

    def save(self, data: dict[str, str]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_bytes(self._fernet.encrypt(json.dumps(data).encode()))
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass  # Windows: rely on NTFS permissions + BitLocker
        tmp.replace(self._path)


class ProjectCredentials:
    """Credentials of exactly one project."""

    def __init__(self, project_id: str, path: Path, fernet_key: bytes):
        self.project_id = project_id
        self._file = _EncryptedFile(path, fernet_key)

    def get(self, name: str, default: str | None = None) -> str | None:
        return self._file.load().get(name, default)

    def require(self, name: str) -> str:
        value = self.get(name)
        if value is None:
            raise VaultError(f"Project {self.project_id}: credential {name!r} is not set")
        return value

    def set(self, name: str, value: str) -> None:
        data = self._file.load()
        data[name] = value
        self._file.save(data)

    def delete(self, name: str) -> bool:
        data = self._file.load()
        existed = data.pop(name, None) is not None
        self._file.save(data)
        return existed

    def names(self) -> list[str]:
        """Credential names only; values are never listed."""
        return sorted(self._file.load())

    def __repr__(self) -> str:  # never leak values in logs
        return f"<ProjectCredentials {self.project_id} names={self.names()}>"


class Vault:
    def __init__(self, home: Path, master: bytes):
        self._home = home
        self._master = master

    # -- lifecycle --------------------------------------------------------
    @staticmethod
    def _kdf(salt: bytes) -> Scrypt:
        return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1)

    @classmethod
    def initialize(cls, home: Path, passphrase: str) -> "Vault":
        if len(passphrase) < 12:
            raise VaultError("Passphrase must be at least 12 characters")
        system = home / "system"
        system.mkdir(parents=True, exist_ok=True)
        salt_file = system / "vault.salt"
        if salt_file.exists():
            raise VaultError("Vault already initialized")
        salt = os.urandom(16)
        master = cls._kdf(salt).derive(passphrase.encode())
        salt_file.write_bytes(salt)
        verifier = Fernet(_derive(master, "guardian/verifier")).encrypt(_VERIFIER_PLAINTEXT)
        (system / "vault.verifier").write_bytes(verifier)
        return cls(home, master)

    @classmethod
    def open(cls, home: Path, passphrase: str) -> "Vault":
        system = home / "system"
        try:
            salt = (system / "vault.salt").read_bytes()
            verifier = (system / "vault.verifier").read_bytes()
        except FileNotFoundError as exc:
            raise VaultError("Vault not initialized; run `guardian init`") from exc
        master = cls._kdf(salt).derive(passphrase.encode())
        try:
            Fernet(_derive(master, "guardian/verifier")).decrypt(verifier)
        except InvalidToken as exc:
            raise VaultError("Wrong passphrase") from exc
        return cls(home, master)

    # -- access -------------------------------------------------------------
    def project(self, project_id: str) -> ProjectCredentials:
        directory = _safe_project_dir(self._home, project_id)
        return ProjectCredentials(project_id, directory / "credentials.enc",
                                  _derive(self._master, f"guardian/project/{project_id}"))

    def system(self) -> ProjectCredentials:
        """System-wide secrets (e.g. Telegram bot token); not tied to any project."""
        return ProjectCredentials("_system", self._home / "system" / "system.enc",
                                  _derive(self._master, "guardian/system"))
