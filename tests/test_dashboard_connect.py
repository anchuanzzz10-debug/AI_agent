"""Connecting websites from the dashboard, including the guided WordPress
connection test (against the simulated WordPress site)."""

import re

import pytest
from fastapi.testclient import TestClient

from conftest import make_project
from fakesite import FakeWordPress
from guardian.dashboard import auth
from guardian.dashboard.app import create_app
from guardian.registry import Registry
from guardian.storage import ProjectStore

DASH_PW = "dashboard-pass-123"


@pytest.fixture
def env(home, vault, tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    reg = Registry(config)
    reg.add(make_project("wp-site", "wordpress"))
    reg.add(make_project("plain-site", "website"))
    auth.set_password(home, DASH_PW)
    site = FakeWordPress()
    app = create_app(home, config, vault, check_runner=lambda p: None,
                     http_client_factory=site.client)
    c = TestClient(app, follow_redirects=False)
    c.cookies.set("lang", "en")
    c.post("/login", data={"password": DASH_PW})
    return {"c": c, "site": site, "vault": vault, "home": home, "config": config}


def csrf(c, path="/projects/wp-site"):
    return re.search(r'name="csrf" value="([^"]+)"', c.get(path).text).group(1)


def connect(env, **fields):
    data = {"csrf": csrf(env["c"]), "op": "save_test", **fields}
    return env["c"].post("/projects/wp-site/wordpress", data=data)


def test_connect_button_is_obvious(env):
    html = env["c"].get("/").text
    assert "Connect website / app" in html and "Websites &amp; apps" in html


def test_new_wordpress_project_lands_on_connection_form(env):
    c = env["c"]
    r = c.post("/projects/new", data={"csrf": csrf(c, "/projects/new"), "id": "shop", "type": "wordpress",
                                      "url": "https://shop.test"})
    assert r.headers["location"] == "/projects/shop?msg=created_wp#wordpress"


def test_wordpress_card_vs_plain_website(env):
    wp = env["c"].get("/projects/wp-site").text
    assert "WordPress connection" in wp and "Read login: not set" in wp
    assert "/wp-admin/profile.php#application-passwords-section" in wp
    plain = env["c"].get("/projects/plain-site").text
    assert "No login is needed" in plain and "WordPress connection" not in plain


def test_editor_login_connects_and_reports_limits(env):
    r = connect(env, read_user="editor", read_password="ed-pass")
    assert r.status_code == 200
    assert "Connected (non-admin user)" in r.text
    assert "Plugins" in r.text and "Administrator" in r.text        # plugins not visible, says why
    assert env["vault"].project("wp-site").get("wp_read_user") == "editor"
    assert "ed-pass" not in r.text
    audit = str(ProjectStore(env["home"], "wp-site").audit_entries())
    assert "ed-pass" not in audit and "wordpress_connection_tested" in audit
    assert "Read login: saved" in env["c"].get("/projects/wp-site").text


def test_wrong_password_reported(env):
    r = connect(env, read_user="editor", read_password="nope")
    assert "Login failed" in r.text


def test_admin_login_warns(env):
    r = connect(env, read_user="admin", read_password="ad-pass")
    assert "more access than monitoring needs" in r.text


def test_connector_full_connection(env):
    env["site"].connector = True
    r = connect(env, read_user="monitor", read_password="mon-pass")
    assert "Fully connected through the Guardian Connector" in r.text
    assert "WordPress 6.6" in r.text


def test_unreachable_site(env):
    env["site"].handler = lambda request: __import__("httpx").Response(503)
    r = connect(env, read_user="editor", read_password="ed-pass")
    assert "Could not reach the WordPress REST API" in r.text


def test_blank_fields_keep_saved_login(env):
    connect(env, read_user="editor", read_password="ed-pass")
    r = connect(env)                                                  # test again, nothing typed
    assert "Connected (non-admin user)" in r.text
    assert env["vault"].project("wp-site").get("wp_read_app_password") == "ed-pass"


def test_change_login_separate_and_removable(env):
    connect(env, read_user="editor", read_password="ed-pass", change_user="admin", change_password="ad-pass")
    creds = env["vault"].project("wp-site")
    assert creds.get("wp_change_user") == "admin"
    assert "Change login: saved" in env["c"].get("/projects/wp-site").text
    env["c"].post("/projects/wp-site/wordpress", data={"csrf": csrf(env["c"]), "op": "remove_change"})
    assert creds.get("wp_change_user") is None and creds.get("wp_read_user") == "editor"


def test_connection_requires_csrf(env):
    env["c"].post("/projects/wp-site/wordpress", data={"read_user": "editor", "read_password": "ed-pass"})
    assert env["vault"].project("wp-site").names() == []


def test_non_wordpress_project_rejects_wordpress_login(env):
    env["c"].post("/projects/plain-site/wordpress",
                  data={"csrf": csrf(env["c"]), "read_user": "x", "read_password": "y"})
    assert env["vault"].project("plain-site").names() == []


def test_http_site_warns_about_https(env):
    p = make_project("old-site", "wordpress")
    p.url = "http://old.test"
    Registry(env["config"]).add(p)
    assert "only work over HTTPS" in env["c"].get("/projects/old-site").text


def test_malayalam_connection_texts(env):
    env["c"].cookies.set("lang", "ml")
    assert "WordPress ബന്ധം" in env["c"].get("/projects/wp-site").text
