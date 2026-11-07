"""Voice chat in a real browser (Chromium via Node Playwright).

Speech engines are faked (see tests/browser/voice_chat.js), so this verifies
the page logic: language selection, transcript sent as a message, replies
read with a matching voice, no Malayalam read by an English voice, error
messages, unsupported browsers, and that voice cannot approve. It does NOT
measure real recognition accuracy; that needs a person speaking.

Skipped automatically where Node.js + Playwright are not installed.
"""

import json
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from conftest import make_project
from guardian.checks.base import ok, warn
from guardian.dashboard import auth
from guardian.dashboard.app import create_app
from guardian.registry import Registry
from guardian.storage import ProjectStore

SCRIPT = Path(__file__).parent / "browser" / "voice_chat.js"
DASH_PW = "dashboard-pass-123"


def _node_env():
    node = shutil.which("node")
    if not node:
        return None
    root = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True).stdout.strip()
    env = {**__import__("os").environ, "NODE_PATH": root}   # keep browser location settings
    probe = subprocess.run([node, "-e", "require('playwright')"], env=env, capture_output=True)
    return env if probe.returncode == 0 else None


NODE_ENV = _node_env()
pytestmark = pytest.mark.skipif(NODE_ENV is None, reason="Node.js + Playwright not installed")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(home, vault, tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    Registry(config).add(make_project("connectdocsmedia", "website", seo=True))
    store = ProjectStore(home, "connectdocsmedia")
    store.record_check("r1", ok("uptime:/", "uptime", "HTTP 200"))
    store.record_check("r1", warn("security:headers", "security", "Missing x-content-type-options"))
    auth.set_password(home, DASH_PW)
    app = create_app(home, config, vault, check_runner=lambda p: None)
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(5)


def test_voice_chat_in_browser(server):
    proc = subprocess.run(["node", str(SCRIPT), server, DASH_PW], env=NODE_ENV,
                          capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stderr
    results = {r["name"]: r for r in json.loads(proc.stdout.strip().splitlines()[-1])}
    for r in results.values():
        assert r["errors"] == [], (r["name"], r["errors"])

    ml = results["malayalam_voice_round_trip"]
    assert ml["recLangs"] == ["ml-IN"]
    assert ml["mine"] == ["റിപ്പോർട്ട് തരൂ"]                 # transcript shown as the user's message
    assert "പ്രശ്നങ്ങൾ 0" in ml["reply"] and "മുന്നറിയിപ്പുകൾ 1" in ml["reply"]
    assert len(ml["spoken"]) == 1 and ml["spoken"][0]["lang"] == "ml-IN"
    assert ml["privacyVisible"] is True

    en = results["english_voice"]
    assert en["recLangs"] == ["en-IN"] and "problems 0" in en["reply"]
    assert en["spoken"][0]["lang"] == "en-IN"

    assert "പരിശോധന തുടങ്ങി" in results["loanword_check"]["reply"]   # "ചെക്ക്" understood

    no_voice = results["no_malayalam_voice"]
    assert no_voice["spoken"] == []                                   # not read by an English voice
    assert "മലയാളം ശബ്ദം" in no_voice["note"]

    assert results["speaking_off"]["spoken"] == []

    denied = results["mic_permission_denied"]
    assert "അനുമതി" in denied["note"] and denied["userMessages"] == 0

    unsupported = results["unsupported_browser"]
    assert unsupported["micHidden"] is True and "Chrome" in unsupported["note"]

    assert "അനുമതികൾ" in results["voice_cannot_approve"]["reply"]    # sent to the Approvals page
