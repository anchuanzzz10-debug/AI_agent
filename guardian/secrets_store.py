"""Vault passphrase storage in the operating system's credential store.

On Windows this is Windows Credential Manager (protected by DPAPI for your
Windows user account). It lets the agent start automatically at login without
the passphrase being typed or written in a plain-text file.

Security trade-off: any program running under your Windows account can read
this entry. Protect the account with a strong password/PIN and keep BitLocker
on. Remove the entry with `guardian passphrase remove` to go back to typing it.

Passphrase lookup order: GUARDIAN_PASSPHRASE env var → credential store → prompt.
"""

from __future__ import annotations

from pathlib import Path

SERVICE = "guardian-agent"


class SecretStoreUnavailable(RuntimeError):
    pass


def _keyring():
    try:
        import keyring  # type: ignore
        from keyring.backends import fail  # type: ignore
    except ImportError as exc:
        raise SecretStoreUnavailable("The 'keyring' package is not installed") from exc
    backend = keyring.get_keyring()
    if isinstance(backend, fail.Keyring):
        raise SecretStoreUnavailable(
            "No operating-system credential store is available on this machine")
    return keyring


def _account(home: Path) -> str:
    # One entry per vault location, so several installations never collide.
    return f"vault-passphrase:{Path(home).resolve()}"


def backend_name() -> str:
    try:
        return type(_keyring().get_keyring()).__name__
    except SecretStoreUnavailable as exc:
        return f"unavailable ({exc})"


def get_passphrase(home: Path) -> str | None:
    try:
        return _keyring().get_password(SERVICE, _account(home))
    except SecretStoreUnavailable:
        return None


def store_passphrase(home: Path, passphrase: str) -> None:
    _keyring().set_password(SERVICE, _account(home), passphrase)


def delete_passphrase(home: Path) -> bool:
    kr = _keyring()
    try:
        kr.delete_password(SERVICE, _account(home))
        return True
    except kr.errors.PasswordDeleteError:
        return False
