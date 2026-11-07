import re
import threading

import pytest
from fastapi.testclient import TestClient

from conftest import PASS, make_project
from guardian import cli
from guardian.approvals import ApprovalGate
from guardian.checks.base import ok, warn
from guardian.dashboard import auth
from guardian.dashboard.app import create_app
from guardian.registry import Registry
from guardian.storage import ProjectStore

DASH_PW = "dashboard-pass-123"


@pytest.fixture
def setup(home, vault, tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    Registry(config).add(make_project("connectdocsmedia", "website", seo=True))
    auth.set_password(home, DASH_PW)
    calls, gate = [], threading.Event()

    def runner(project):
        calls.append(project.id)
        gate.wait(5)

    app = create_app(home, config, vault, check_runner=runner)
    return {"home": home, "config": config, "vault": vault, "app": app, "calls": calls, "gate": gate}


def client(setup, login=True, lang=None):
    c = TestClient(setup["app"], follow_redirects=False)
    if lang:
        c.cookies.set("lang", lang)
    if login:
        r = c.post("/login", data={"password": DASH_PW})
        assert r.status_code == 303
    return c


def csrf_of(html: str) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


# ---------------- authentication ----------------

def test_pages_require_login(setup):
    c = client(setup, login=False)
    for path in ("/", "/approvals", "/chat", "/projects/new", "/projects/connectdocsmedia"):
        r = c.get(path)
        assert r.status_code == 303 and r.headers["location"] == "/login"
    assert c.post("/api/chat", json={"message": "report"}).status_code == 401


def test_wrong_password_and_lockout(setup):
    c = client(setup, login=False)
    for _ in range(5):
        assert c.post("/login", data={"password": "wrong-password"}).status_code == 401
    # locked out even with the right password
    assert c.post("/login", data={"password": DASH_PW}).status_code == 429


def test_login_cookie_and_security_headers(setup):
    c = client(setup, login=False)
    r = c.post("/login", data={"password": DASH_PW})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    r = c.get("/")
    assert r.status_code == 200
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"


def test_logout_ends_session(setup):
    c = client(setup)
    html = c.get("/").text
    c.post("/logout", data={"csrf": csrf_of(html)})
    assert c.get("/").status_code == 303


def test_password_rules_and_hash_only(home):
    with pytest.raises(auth.AuthError):
        auth.set_password(home, "short")
    auth.set_password(home, DASH_PW)
    stored = (home / "system" / auth.PASSWORD_FILE).read_text()
    assert DASH_PW not in stored
    assert auth.check_password(home, DASH_PW) and not auth.check_password(home, DASH_PW + "x")


def test_session_idle_expiry():
    now = [1000.0]
    store = auth.SessionStore(clock=lambda: now[0])
    token, _ = store.create()
    assert store.get(token)
    now[0] += auth.IDLE_TIMEOUT_S + 1
    assert store.get(token) is None


# ---------------- language ----------------

def test_malayalam_default_and_english_switch(setup):
    c = client(setup)
    assert "വെബ്സൈറ്റുകളും ആപ്പുകളും" in c.get("/").text
    r = c.get("/lang/en?next=/")
    assert r.headers["location"] == "/"
    c.cookies.set("lang", "en")
    assert "Websites &amp; apps" in c.get("/").text


def test_language_switch_blocks_open_redirect(setup):
    c = client(setup)
    assert c.get("/lang/en?next=//evil.example").headers["location"] == "/"
    assert c.get("/lang/en?next=https://evil.example").headers["location"] == "/"


def test_every_ui_string_has_both_languages():
    from guardian.dashboard.i18n import STRINGS
    from guardian.models import FEATURES
    assert all(set(v) == {"ml", "en"} and all(v.values()) for v in STRINGS.values())
    assert all(f"feat_{f}" in STRINGS for f in FEATURES)


def test_mobile_viewport(setup):
    html = client(setup).get("/").text
    assert 'name="viewport"' in html and "width=device-width" in html


# ---------------- projects ----------------

def test_add_project_requires_csrf(setup):
    c = client(setup)
    form = {"id": "site-two", "name": "Site Two", "type": "wordpress", "url": "https://two.test"}
    c.post("/projects/new", data=form)
    assert not (setup["config"] / "site-two.yaml").exists()
    form["csrf"] = csrf_of(c.get("/projects/new").text)
    r = c.post("/projects/new", data=form)
    assert r.status_code == 303 and (setup["config"] / "site-two.yaml").exists()
    assert not Registry(setup["config"]).get("site-two").enabled("seo")   # SEO only when ticked


def test_add_project_with_seo_and_invalid_id(setup):
    c = client(setup)
    token = csrf_of(c.get("/projects/new").text)
    r = c.post("/projects/new", data={"csrf": token, "id": "Bad ID", "type": "website", "url": "https://x.test"})
    assert r.status_code == 400 and not list(setup["config"].glob("Bad*"))
    c.post("/projects/new", data={"csrf": token, "id": "seo-site", "type": "website",
                                  "url": "https://s.test", "seo": "on"})
    assert Registry(setup["config"]).get("seo-site").enabled("seo")


def test_settings_toggle_seo_and_validate_paths(setup):
    c = client(setup)
    page = c.get("/projects/connectdocsmedia").text
    token = csrf_of(page)
    base = {"csrf": token, "f_uptime": "on", "f_ssl": "on", "f_error_detection": "on",
            "f_security": "on", "f_reports": "on", "key_paths": "/, /contact", "expected_text": "DOCS MEDIA"}
    c.post("/projects/connectdocsmedia/settings", data=base)            # seo not ticked → off
    p = Registry(setup["config"]).get("connectdocsmedia")
    assert not p.enabled("seo") and p.key_paths == ["/", "/contact"]
    r = c.post("/projects/connectdocsmedia/settings", data=base | {"key_paths": "contact"})
    assert r.status_code == 400
    assert Registry(setup["config"]).get("connectdocsmedia").key_paths == ["/", "/contact"]
    assert "f_wordpress" not in page                                     # incompatible for websites


def test_project_names_are_escaped(setup):
    Registry(setup["config"]).add(_named("xss-site", "<script>alert(1)</script>"))
    html = client(setup).get("/").text
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html


def _named(pid, name):
    p = make_project(pid, "website")
    p.name = name
    return p


# ---------------- credentials ----------------

def test_credential_values_are_write_only(setup):
    c = client(setup)
    token = csrf_of(c.get("/projects/connectdocsmedia").text)
    secret = "app-pass-SECRET-9876"
    c.post("/projects/connectdocsmedia/credentials", data={"csrf": token, "name": "wp_read_user", "value": secret})
    page = c.get("/projects/connectdocsmedia").text
    assert "wp_read_user" in page and secret not in page
    assert setup["vault"].project("connectdocsmedia").get("wp_read_user") == secret
    leaks = [str(f) for f in setup["home"].rglob("*") if f.is_file() and secret.encode() in f.read_bytes()]
    assert leaks == []                                                   # encrypted, not in logs/db
    c.post("/projects/connectdocsmedia/credentials/delete", data={"csrf": token, "name": "wp_read_user"})
    assert setup["vault"].project("connectdocsmedia").names() == []


def test_credential_without_csrf_ignored(setup):
    c = client(setup)
    c.post("/projects/connectdocsmedia/credentials", data={"name": "x_key", "value": "v"})
    assert setup["vault"].project("connectdocsmedia").names() == []


# ---------------- checks ----------------

def test_check_now_runs_once_while_running(setup):
    c = client(setup)
    token = csrf_of(c.get("/projects/connectdocsmedia").text)
    c.post("/projects/connectdocsmedia/check", data={"csrf": token})
    c.post("/projects/connectdocsmedia/check", data={"csrf": token})
    assert setup["calls"] == ["connectdocsmedia"]
    assert "പരിശോധിക്കുന്നു" in c.get("/").text                          # shows "checking…"
    setup["gate"].set()


def test_results_shown_with_seo_section(setup):
    store = ProjectStore(setup["home"], "connectdocsmedia")
    store.record_check("run-1", warn("security:headers", "security", "Missing security headers: x-content-type-options"))
    store.record_check("run-1", ok("seo:title", "seo", "Title present (47 chars)"))
    html = client(setup).get("/projects/connectdocsmedia").text
    assert "x-content-type-options" in html and "SEO റിപ്പോർട്ട്" in html


# ---------------- approvals ----------------

def test_approve_needs_csrf_and_records_decision(setup):
    store = ProjectStore(setup["home"], "connectdocsmedia")
    req = ApprovalGate(store).request({"project": "connectdocsmedia", "type": "test.action"}, "test", "low")
    c = client(setup)
    page = c.get("/approvals").text
    assert req["id"] in page
    c.post(f"/approvals/connectdocsmedia/{req['id']}", data={"decision": "approve"})
    assert ApprovalGate(store).list()[0]["status"] == "pending"
    c.post(f"/approvals/connectdocsmedia/{req['id']}", data={"decision": "approve", "csrf": csrf_of(page)})
    row = ApprovalGate(store).list()[0]
    assert row["status"] == "approved" and row["data"]["decided_by"] == "owner:dashboard"


# ---------------- chat ----------------

def _chat(c, message, csrf):
    return c.post("/api/chat", json={"message": message}, headers={"X-CSRF-Token": csrf})


def test_chat_page_has_voice_controls(setup):
    html = client(setup).get("/chat").text
    assert 'id="mic"' in html and 'value="ml-IN"' in html and 'value="en-IN"' in html
    assert 'id="speak-replies"' in html and "data-voice-no-ml-voice=" in html
    assert "<script>" not in html                                     # no inline scripts (CSP)


def test_chat_requires_csrf_header(setup):
    c = client(setup)
    assert c.post("/api/chat", json={"message": "help"}).status_code == 403


def test_chat_malayalam_manglish_english(setup):
    c = client(setup)
    token = csrf_of(c.get("/").text)
    ProjectStore(setup["home"], "connectdocsmedia").record_check("r", ok("uptime:/", "uptime", "200"))
    ml = _chat(c, "റിപ്പോർട്ട് തരൂ", token).json()
    assert ml["lang"] == "ml" and "പ്രശ്നങ്ങൾ 0" in ml["reply"]
    mg = _chat(c, "innathe report tharu", token).json()
    assert mg["lang"] == "ml"
    en = _chat(c, "report", token).json()
    assert en["lang"] == "en" and "problems 0" in en["reply"]
    assert "ഒന്നുമില്ല" in _chat(c, "prashnangal undo", token).json()["reply"]


def test_chat_never_approves(setup):
    store = ProjectStore(setup["home"], "connectdocsmedia")
    req = ApprovalGate(store).request({"project": "connectdocsmedia", "type": "t"}, "r", "low")
    c = client(setup)
    token = csrf_of(c.get("/").text)
    reply = _chat(c, f"approve {req['id']}", token).json()["reply"]
    assert "Approvals" in reply
    assert ApprovalGate(store).list()[0]["status"] == "pending"


def test_chat_starts_check_and_reports_seo(setup):
    c = client(setup)
    token = csrf_of(c.get("/").text)
    reply = _chat(c, "connectdocsmedia check cheyyu", token).json()
    assert reply["intent"] == "run_check" and setup["calls"] == ["connectdocsmedia"]
    setup["gate"].set()
    assert "SEO" in _chat(c, "seo report", token).json()["reply"]


def test_chat_unknown_is_honest(setup):
    c = client(setup)
    token = csrf_of(c.get("/").text)
    reply = _chat(c, "what's the weather", token).json()
    assert reply["intent"] == "unknown" and "next phase" in reply["reply"]


# ---------------- serving ----------------

def test_serve_refuses_without_password_and_remote_host(home, vault, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GUARDIAN_HOME", str(home))
    monkeypatch.setenv("GUARDIAN_CONFIG", str(tmp_path / "cfg"))
    monkeypatch.setenv("GUARDIAN_PASSPHRASE", PASS)
    assert cli.main(["dashboard", "serve"]) == 1
    assert "dashboard password" in capsys.readouterr().err
    auth.set_password(home, DASH_PW)
    assert cli.main(["dashboard", "serve", "--host", "0.0.0.0"]) == 1
    assert "Refusing" in capsys.readouterr().err
