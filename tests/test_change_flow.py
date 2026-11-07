"""Propose → approve → backup → apply → verify → rollback, end to end through
the dashboard, against the simulated WordPress site (never a real site)."""

import re

import pytest
from fastapi.testclient import TestClient

from conftest import make_project
from fakesite import CRITICAL, FakeWordPress
from guardian.approvals import ApprovalGate
from guardian.checks.base import fail
from guardian.dashboard import auth
from guardian.dashboard.app import create_app
from guardian.incidents import IncidentManager
from guardian.proposals import ProposalError, plugin_spec, post_spec, risk_of
from guardian.registry import Registry
from guardian.storage import ProjectStore

PW = "dashboard-pass-123"


@pytest.fixture
def env(home, vault, tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    Registry(config).add(make_project("wp-site", "wordpress"))
    Registry(config).add(make_project("plain-site", "website"))
    auth.set_password(home, PW)
    site = FakeWordPress()
    creds = vault.project("wp-site")
    creds.set("wp_change_user", "admin")
    creds.set("wp_change_app_password", "ad-pass")
    jobs = []
    app = create_app(home, config, vault, check_runner=lambda p: None,
                     http_client_factory=site.client, job_runner=lambda job: (jobs.append(job), job()))
    c = TestClient(app, follow_redirects=False)
    c.cookies.set("lang", "en")
    c.post("/login", data={"password": PW})
    return {"c": c, "site": site, "vault": vault, "home": home, "jobs": jobs,
            "store": ProjectStore(home, "wp-site")}


def csrf(c, path="/projects/wp-site"):
    return re.search(r'name="csrf" value="([^"]+)"', c.get(path).text).group(1)


def propose(env, **fields):
    return env["c"].post("/projects/wp-site/propose", data={"csrf": csrf(env["c"]), **fields})


def approvals(env):
    return ApprovalGate(env["store"]).list()


def decide(env, aid, decision):
    return env["c"].post(f"/approvals/wp-site/{aid}",
                         data={"csrf": csrf(env["c"], "/approvals"), "decision": decision})


# ---------------- validation ----------------

def test_spec_validation():
    p = make_project("wp-site", "wordpress")
    assert plugin_spec(p, "akismet/akismet.php", "inactive")["plugin"] == "akismet/akismet"
    for bad in ("../../etc", "a b", "", "x/y/z"):
        with pytest.raises(ProposalError):
            plugin_spec(p, bad, "inactive")
    with pytest.raises(ProposalError):
        post_spec(p, "pages", "abc", "title", "x")
    with pytest.raises(ProposalError):
        post_spec(p, "pages", 7, "content", "x")          # content edits not offered
    with pytest.raises(ProposalError):
        post_spec(p, "pages", 7, "status", "deleted")
    assert risk_of(plugin_spec(p, "a/a", "inactive")) == "medium"
    assert risk_of(post_spec(p, "pages", 7, "title", "x")) == "low"


# ---------------- proposing ----------------

def test_proposal_creates_pending_approval_and_changes_nothing(env):
    r = propose(env, kind="plugin", plugin="akismet/akismet", plugin_status="inactive", reason="crashing")
    assert r.headers["location"] == "/approvals?msg=proposed"
    rows = approvals(env)
    assert len(rows) == 1 and rows[0]["status"] == "pending" and rows[0]["data"]["risk"] == "medium"
    assert env["site"].plugins["akismet/akismet"]["status"] == "active"     # nothing changed yet
    assert not any(r.startswith("POST") for r in env["site"].requests)
    html = env["c"].get("/approvals").text
    assert "Deactivate plugin akismet/akismet" in html and "crashing" in html


def test_proposal_requires_csrf_and_change_login(env):
    env["c"].post("/projects/wp-site/propose", data={"kind": "plugin", "plugin": "a/a"})
    assert approvals(env) == []
    env["vault"].project("wp-site").delete("wp_change_user")
    r = propose(env, kind="plugin", plugin="akismet/akismet", plugin_status="inactive")
    assert r.status_code == 400 and "No change login" in r.text and approvals(env) == []


def test_no_proposals_for_non_wordpress(env):
    r = env["c"].post("/projects/plain-site/propose",
                      data={"csrf": csrf(env["c"], "/projects/plain-site"), "kind": "plugin", "plugin": "a/a"})
    assert r.status_code == 400 and ApprovalGate(ProjectStore(env["home"], "plain-site")).list() == []


# ---------------- approve → execute ----------------

def test_approve_applies_backs_up_and_verifies(env):
    propose(env, kind="plugin", plugin="akismet/akismet", plugin_status="inactive", reason="test")
    aid = approvals(env)[0]["id"]
    r = decide(env, aid, "approve")
    assert r.headers["location"] == "/approvals?msg=approved_running"
    assert env["site"].plugins["akismet/akismet"]["status"] == "inactive"
    row = approvals(env)[0]
    ex = row["data"]["execution"]
    assert row["status"] == "used" and ex["outcome"] == "applied_verified"
    assert ex["backup"]["ok"] and ex["verification"]["passed"]
    html = env["c"].get("/approvals").text
    assert "Applied and verified" in html and "change:plugin_status" in html


def test_breaking_change_is_rolled_back_and_reported(env):
    site = env["site"]
    site.break_on_save = True
    original = site.handler

    def heal_after_rollback(request):
        resp = original(request)
        if request.method == "POST" and site.posts[7]["title"] == "Old title":
            site.home_status, site.home_html = 200, "<html><h1>Welcome</h1></html>"
        return resp
    site.handler = heal_after_rollback
    propose(env, kind="post", post_type="posts", post_id="7", field="title", value="New title", reason="x")
    decide(env, approvals(env)[0]["id"], "approve")
    ex = approvals(env)[0]["data"]["execution"]
    assert ex["outcome"] == "failed_rolled_back"
    assert not ex["verification"]["passed"] and ex["rollback_verification"]["passed"]
    assert site.posts[7]["title"] == "Old title"
    assert "Verification failed, rolled back" in env["c"].get("/approvals").text


def test_backup_failure_means_no_change(env):
    env["site"].backup_unavailable = True
    propose(env, kind="post", post_type="posts", post_id="7", field="title", value="New", reason="x")
    decide(env, approvals(env)[0]["id"], "approve")
    assert approvals(env)[0]["data"]["execution"]["outcome"] == "refused_no_backup"
    assert env["site"].posts[7]["title"] == "Old title"


def test_reject_changes_nothing(env):
    propose(env, kind="plugin", plugin="akismet/akismet", plugin_status="inactive")
    decide(env, approvals(env)[0]["id"], "reject")
    assert env["jobs"] == [] and env["site"].plugins["akismet/akismet"]["status"] == "active"
    assert "Rejected" in env["c"].get("/approvals").text


def test_approval_cannot_be_used_twice(env):
    propose(env, kind="plugin", plugin="akismet/akismet", plugin_status="inactive")
    aid = approvals(env)[0]["id"]
    decide(env, aid, "approve")
    r = decide(env, aid, "approve")
    assert r.status_code == 409 and len(env["jobs"]) == 1


def test_verified_fix_closes_linked_incident(env):
    inc, _ = IncidentManager(env["store"]).record_failure(fail("content:title", "content", "Wrong page title"))
    propose(env, kind="post", post_type="posts", post_id="7", field="title", value="Right title",
            reason="fix title", incident=inc["id"])
    assert approvals(env)[0]["data"]["incident"] == inc["id"]
    decide(env, approvals(env)[0]["id"], "approve")
    assert IncidentManager(env["store"]).get(inc["id"])["status"] == "fixed_verified"


def test_cannot_link_unknown_incident(env):
    r = propose(env, kind="plugin", plugin="a/a", plugin_status="inactive", incident="INC-NOPE")
    assert r.status_code == 400 and approvals(env) == []


def test_site_already_broken_before_change_is_not_marked_success(env):
    env["site"].home_status, env["site"].home_html = 500, CRITICAL
    propose(env, kind="plugin", plugin="akismet/akismet", plugin_status="inactive")
    decide(env, approvals(env)[0]["id"], "approve")
    assert approvals(env)[0]["data"]["execution"]["outcome"] != "applied_verified"


def test_malayalam_change_texts(env):
    propose(env, kind="plugin", plugin="akismet/akismet", plugin_status="inactive")
    env["c"].cookies.set("lang", "ml")
    html = env["c"].get("/approvals").text
    assert "പ്ലഗിൻ akismet/akismet ഓഫ് ആക്കുക" in html
    assert "മാറ്റം നിർദ്ദേശിക്കുക" in env["c"].get("/projects/wp-site").text
