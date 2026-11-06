"""Secure passphrase storage (credential store) and Windows auto-start.

The real Windows Credential Manager and Task Scheduler can't run on the
Linux test machine, so these tests use an in-memory keyring backend and a
recorded fake `schtasks`. They check everything the agent itself controls:
what gets stored where, what files are written, the task definition, and
the refusals.
"""

import xml.etree.ElementTree as ET

import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.backends import fail
from keyring.errors import PasswordDeleteError

from conftest import PASS, make_project
from guardian import autostart, cli, secrets_store
from guardian.registry import Registry

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()
        self.items = {}

    def get_password(self, service, username):
        return self.items.get((service, username))

    def set_password(self, service, username, password):
        self.items[(service, username)] = password

    def delete_password(self, service, username):
        if (service, username) not in self.items:
            raise PasswordDeleteError("not found")
        del self.items[(service, username)]


@pytest.fixture
def kr():
    previous = keyring.get_keyring()
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(previous)


@pytest.fixture
def env(home, vault, tmp_path, monkeypatch):
    """CLI environment: vault initialized, one project, no passphrase env var."""
    config = tmp_path / "config"
    config.mkdir()
    Registry(config).add(make_project())
    monkeypatch.setenv("GUARDIAN_HOME", str(home))
    monkeypatch.setenv("GUARDIAN_CONFIG", str(config))
    monkeypatch.delenv("GUARDIAN_PASSPHRASE", raising=False)
    return {"home": home, "config": config}


@pytest.fixture
def fake_windows(monkeypatch):
    calls = []

    class Done:
        def __init__(self, code=0, out=""):
            self.returncode, self.stdout, self.stderr = code, out, ""

    def schtasks(*args):
        calls.append(args)
        return Done()
    monkeypatch.setattr(autostart, "_require_windows", lambda: None)
    monkeypatch.setattr(autostart, "_current_user", lambda: "LAPTOP\\owner")
    monkeypatch.setattr(autostart, "_schtasks", schtasks)
    return calls


def _type(monkeypatch, text):
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": text)


def _files_contain(root, secret: str) -> list[str]:
    needles = [secret.encode("utf-8"), secret.encode("utf-16-le")]
    return [str(p) for p in root.rglob("*") if p.is_file()
            and any(n in p.read_bytes() for n in needles)]


# ---------------- credential store ----------------

def test_store_get_delete_round_trip(home, kr):
    secrets_store.store_passphrase(home, PASS)
    assert secrets_store.get_passphrase(home) == PASS
    assert secrets_store.delete_passphrase(home) is True
    assert secrets_store.get_passphrase(home) is None
    assert secrets_store.delete_passphrase(home) is False


def test_separate_entry_per_vault_location(tmp_path, kr):
    secrets_store.store_passphrase(tmp_path / "a", "passphrase-for-a")
    secrets_store.store_passphrase(tmp_path / "b", "passphrase-for-b")
    assert secrets_store.get_passphrase(tmp_path / "a") == "passphrase-for-a"
    assert secrets_store.get_passphrase(tmp_path / "b") == "passphrase-for-b"


def test_no_credential_store_is_reported_not_faked(home, monkeypatch):
    previous = keyring.get_keyring()
    keyring.set_keyring(fail.Keyring())
    try:
        assert secrets_store.get_passphrase(home) is None
        with pytest.raises(secrets_store.SecretStoreUnavailable):
            secrets_store.store_passphrase(home, PASS)
        assert "unavailable" in secrets_store.backend_name()
    finally:
        keyring.set_keyring(previous)


# ---------------- CLI: passphrase ----------------

def test_cli_store_verifies_passphrase_first(env, kr, monkeypatch, capsys):
    _type(monkeypatch, "wrong passphrase!!")
    assert cli.main(["passphrase", "store"]) == 1
    assert "Wrong passphrase" in capsys.readouterr().err
    assert kr.items == {}
    _type(monkeypatch, PASS)
    assert cli.main(["passphrase", "store"]) == 0
    assert list(kr.items.values()) == [PASS]


def test_passphrase_never_written_in_plain_text(env, kr, monkeypatch, fake_windows):
    _type(monkeypatch, PASS)
    assert cli.main(["passphrase", "store"]) == 0
    assert cli.main(["autostart", "install"]) == 0
    assert cli.main(["cred", "list", "site-one"]) == 0
    assert _files_contain(env["home"], PASS) == []
    assert _files_contain(env["config"], PASS) == []


def test_status_never_prints_the_passphrase(env, kr, monkeypatch, capsys):
    _type(monkeypatch, PASS)
    cli.main(["passphrase", "store"])
    capsys.readouterr()
    cli.main(["passphrase", "status"])
    out = capsys.readouterr().out
    assert "Passphrase stored: yes" in out and PASS not in out


def test_stored_passphrase_used_without_prompt(env, kr, monkeypatch, capsys):
    kr.set_password(secrets_store.SERVICE, secrets_store._account(env["home"]), PASS)

    def no_prompt(prompt=""):
        raise AssertionError("must not prompt when a passphrase is stored")
    monkeypatch.setattr(cli.getpass, "getpass", no_prompt)
    assert cli.main(["cred", "list", "site-one"]) == 0


def test_background_run_without_passphrase_fails_clearly(env, kr, monkeypatch, capsys):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False, raising=False)
    assert cli.main(["cred", "list", "site-one"]) == 1
    assert "guardian passphrase store" in capsys.readouterr().err


def test_remove(env, kr, monkeypatch, capsys):
    _type(monkeypatch, PASS)
    cli.main(["passphrase", "store"])
    assert cli.main(["passphrase", "remove"]) == 0
    assert kr.items == {}


# ---------------- auto-start ----------------

def test_install_refuses_without_stored_passphrase(env, kr, fake_windows, capsys):
    assert cli.main(["autostart", "install"]) == 1
    assert "passphrase store" in capsys.readouterr().err
    assert fake_windows == []


def test_install_refuses_plain_text_env_var(env, kr, monkeypatch, fake_windows, capsys):
    _type(monkeypatch, PASS)
    cli.main(["passphrase", "store"])
    monkeypatch.setenv("GUARDIAN_PASSPHRASE", PASS)
    assert cli.main(["autostart", "install"]) == 1
    assert "plain text" in capsys.readouterr().err
    assert fake_windows == []


def test_install_refuses_stale_stored_passphrase(env, kr, fake_windows, capsys):
    kr.set_password(secrets_store.SERVICE, secrets_store._account(env["home"]), "old passphrase 123")
    assert cli.main(["autostart", "install"]) == 1
    assert fake_windows == []


def test_install_creates_task_but_does_not_start_it(env, kr, monkeypatch, fake_windows, capsys):
    _type(monkeypatch, PASS)
    cli.main(["passphrase", "store"])
    assert cli.main(["autostart", "install", "--interval", "10"]) == 0
    assert len(fake_windows) == 1
    args = fake_windows[0]
    assert args[:2] == ("/Create", "/TN") and args[2] == "Guardian Agent" and "/XML" in args
    assert not any(a == "/Run" for call in fake_windows for a in call)
    assert "NOT started" in capsys.readouterr().out


def test_task_definition(env, kr, monkeypatch, fake_windows):
    _type(monkeypatch, PASS)
    cli.main(["passphrase", "store"])
    cli.main(["autostart", "install"])
    xml_path = env["home"] / "system" / "guardian-task.xml"
    assert xml_path.read_bytes()[:2] in (b"\xff\xfe", b"\xfe\xff")  # UTF-16 as schtasks expects
    root = ET.fromstring(xml_path.read_text(encoding="utf-16"))
    get = lambda path: root.find(path, NS).text  # noqa: E731
    assert get("t:Triggers/t:LogonTrigger/t:UserId") == "LAPTOP\\owner"
    assert get("t:Principals/t:Principal/t:LogonType") == "InteractiveToken"
    assert get("t:Principals/t:Principal/t:RunLevel") == "LeastPrivilege"
    assert get("t:Settings/t:DisallowStartIfOnBatteries") == "false"
    assert get("t:Settings/t:StopIfGoingOnBatteries") == "false"
    assert get("t:Settings/t:ExecutionTimeLimit") == "PT0S"
    assert get("t:Settings/t:MultipleInstancesPolicy") == "IgnoreNew"
    assert get("t:Settings/t:RestartOnFailure/t:Count") == "999"
    assert get("t:Actions/t:Exec/t:Command") == "powershell.exe"
    assert "guardian-autostart.ps1" in get("t:Actions/t:Exec/t:Arguments")


def test_launcher_contents(tmp_path):
    home = tmp_path / "O'Brien data"            # apostrophe must be escaped for PowerShell
    text = autostart.build_launcher(home, tmp_path / "cfg", 5, r"C:\venv\Scripts\python.exe")
    assert "Remove-Item Env:GUARDIAN_PASSPHRASE" in text
    assert "O''Brien data" in text
    assert "-m guardian.cli check all --loop 5 --quiet" in text
    assert "autostart.log" in text


def test_other_commands_call_schtasks(fake_windows):
    autostart.start()
    autostart.stop()
    autostart.remove()
    flat = [call[0] for call in fake_windows]
    assert flat == ["/Run", "/End", "/End", "/Delete"]


def test_real_install_refuses_off_windows(tmp_path):
    if autostart.os.name == "nt":
        pytest.skip("running on Windows")
    with pytest.raises(autostart.AutostartError):
        autostart.install(tmp_path, tmp_path, 5)


def test_monitoring_loop_survives_a_failed_run(env, kr, monkeypatch, capsys):
    kr.set_password(secrets_store.SERVICE, secrets_store._account(env["home"]), PASS)
    runs = []

    def flaky(args, reg, vault):
        runs.append(1)
        if len(runs) == 1:
            raise RuntimeError("network glitch")
        return 0

    class StopLoop(Exception):
        pass

    def sleep(_):
        if len(runs) >= 2:
            raise StopLoop
    monkeypatch.setattr(cli, "_check_once", flaky)
    monkeypatch.setattr(cli.time, "sleep", sleep)
    with pytest.raises(StopLoop):
        cli.main(["check", "all", "--loop", "1"])
    out = capsys.readouterr().out
    assert "run FAILED: RuntimeError: network glitch" in out
    assert "run finished (exit 0)" in out
