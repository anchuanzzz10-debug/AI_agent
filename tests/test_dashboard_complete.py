"""Overview summary and monitoring freshness, incidents page, check history,
system status page (no secrets), and software-app health settings."""

import re

import pytest
from fastapi.testclient import TestClient

from conftest import make_project
from guardian import secrets_store
from guardian.checks.base import fail, ok, warn
from guardian.dashboard import auth
from guardian.dashboard.app import STALE_AFTER_MIN, create_app
from guardian.incidents import IncidentManager, Verification
from guardian.registry import Registry
from guardian.storage import ProjectStore

PW = "dashboard-pass-123"


@pytest.fixture
def env(home, vault, tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    Registry(config).add(make_project("connectdocsmedia", "website", seo=True))
    auth.set_password(home, PW)
    now = [0.0]

    def build(**kw):
        app = create_app(home, config, vault, check_runner=lambda p: None,
                         clock=lambda: now[0], **kw)
        c = TestClient(app, follow_redirects=False)
        c.cookies.set("lang", "en")
        c.post("/login", data={"password": PW})
        return c
    return {"home": home, "config": config, "vault": vault, "now": now, "build": build,
            "store": ProjectStore(home, "connectdocsmedia")}


def csrf(c, path):
    return re.search(r'name="csrf" value="([^"]+)"', c.get(path).text).group(1)


def test_new_pages_require_login(env):
    c = TestClient(create_app(env["home"], env["config"], env["vault"], check_runner=lambda p: None),
                   follow_redirects=False)
    for path in ("/incidents", "/settings"):
        assert c.get(path).headers["location"] == "/login"


def test_navigation_has_all_sections_in_both_languages(env):
    c = env["build"]()
    html = c.get("/").text
    for label in ("Overview", "Incidents", "Approvals", "Chat", "System"):
        assert label in html
    c.cookies.set("lang", "ml")
    html = c.get("/").text
    for label in ("അവലോകനം", "പ്രശ്നങ്ങൾ", "അനുമതികൾ", "ചാറ്റ്", "സിസ്റ്റം"):
        assert label in html


def test_overview_summary_and_fresh_check(env):
    env["store"].record_check("r1", warn("security:headers", "security", "Missing header"))
    env["store"].record_check("r1", ok("uptime:/", "uptime", "200"))
    import time
    env["now"][0] = time.time() + 3 * 60
    html = env["build"]().get("/").text
    assert "summary-bar" in html and "checked 3 min ago" in html
    assert "Background monitoring (auto-start) may have stopped" not in html


def test_stale_monitoring_is_flagged(env):
    env["store"].record_check("r1", ok("uptime:/", "uptime", "200"))
    import time
    env["now"][0] = time.time() + (STALE_AFTER_MIN + 45) * 60
    html = env["build"]().get("/").text
    assert "Background monitoring (auto-start) may have stopped" in html
    assert "No check for" in html


def test_incidents_page_open_and_closed(env):
    mgr = IncidentManager(env["store"])
    open_inc, _ = mgr.record_failure(fail("uptime:/", "uptime", "Site returned HTTP 500",
                                          url="https://x.test/"))
    closed, _ = mgr.record_failure(fail("ssl", "ssl", "Certificate expires in 2 days"))
    mgr.mark_recovered(closed["id"], Verification([ok("ssl", "ssl", "valid 89 days")], "renewed"))
    html = env["build"]().get("/incidents").text
    assert open_inc["id"] in html and "Site returned HTTP 500" in html and "https://x.test/" in html
    assert "Open incidents (1)" in html
    assert "Closed incidents" in html and "Recovered (verified)" in html


def test_check_history_on_project_page(env):
    env["store"].record_check("run-a", fail("uptime:/", "uptime", "down"))
    env["store"].record_check("run-b", ok("uptime:/", "uptime", "up"))
    env["store"].record_check("run-b", warn("security:headers", "security", "x"))
    rows = env["store"].run_history(10)
    assert [r["run_id"] for r in rows] == ["run-b", "run-a"]
    assert rows[0]["ok"] == 1 and rows[0]["warn"] == 1 and rows[1]["fail"] == 1
    html = env["build"]().get("/projects/connectdocsmedia").text
    assert "Check history (last 10)" in html and html.count("<tr") == 3   # header + 2 runs


def test_system_page_never_shows_secrets(env, monkeypatch):
    env["vault"].system().set("telegram_bot_token", "TOKEN-SECRET-123456")
    env["vault"].system().set("telegram_default_chat_id", "998877")
    monkeypatch.setattr(secrets_store, "get_passphrase", lambda home: "VAULT-PASS-SECRET")
    html = env["build"]().get("/settings").text
    assert "TOKEN-SECRET-123456" not in html and "998877" not in html and "VAULT-PASS-SECRET" not in html
    assert "✅ Configured" in html and "✅ Stored" in html
    assert "Off. To turn on: guardian ai on" in html                   # no AI client given
    assert "Windows only" in html                                       # Linux test machine
    assert "never shown here" in html


def test_system_page_without_secrets(env, monkeypatch):
    monkeypatch.setattr(secrets_store, "get_passphrase", lambda home: None)
    html = env["build"]().get("/settings").text
    assert "Not configured" in html and "Not stored" in html


def test_add_app_without_health_address_does_not_invent_one(env):
    c = env["build"]()
    token = csrf(c, "/projects/new")
    c.post("/projects/new", data={"csrf": token, "id": "billing", "type": "application",
                                  "url": "https://billing.test"})
    p = Registry(env["config"]).get("billing")
    assert not p.enabled("health_endpoint") and not p.enabled("seo")
    c.post("/projects/new", data={"csrf": token, "id": "billing-two", "type": "application",
                                  "url": "https://b2.test", "health_path": "/api/health"})
    p2 = Registry(env["config"]).get("billing-two")
    assert p2.enabled("health_endpoint") and p2.health_path == "/api/health"
    r = c.post("/projects/new", data={"csrf": token, "id": "bad-app", "type": "application",
                                      "url": "https://b3.test", "health_path": "health"})
    assert r.status_code == 400 and not (env["config"] / "bad-app.yaml").exists()


def test_app_settings_require_health_address_when_check_is_on(env):
    Registry(env["config"]).add(make_project("app-one", "application"))
    c = env["build"]()
    token = csrf(c, "/projects/app-one")
    base = {"csrf": token, "f_uptime": "on", "key_paths": "/", "f_health_endpoint": "on"}
    r = c.post("/projects/app-one/settings", data=base | {"health_path": ""})
    assert r.status_code == 400
    c.post("/projects/app-one/settings", data=base | {"health_path": "/status"})
    assert Registry(env["config"]).get("app-one").health_path == "/status"
    html = c.get("/projects/app-one").text
    assert 'id="health_path"' in html


def test_approvals_page_explains_what_approval_does(env):
    html = env["build"]().get("/approvals").text
    assert "A change only happens after you approve it" in html and "rolled back automatically" in html


def test_only_the_configured_project_is_shown(env):
    html = env["build"]().get("/").text
    assert html.count("project-card") == 1


def test_closed_incidents_are_not_styled_as_alarms(env):
    mgr = IncidentManager(env["store"])
    inc, _ = mgr.record_failure(fail("ssl", "ssl", "Certificate expires in 2 days"))
    mgr.mark_recovered(inc["id"], Verification([ok("ssl", "ssl", "valid")], "renewed"))
    html = env["build"]().get("/incidents").text
    assert 'class="card incident closed"' in html and "sev-high" not in html
