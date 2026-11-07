import pytest

from conftest import cert_in, make_project
from fakesite import FakeWordPress
from guardian.approvals import ApprovalError, ApprovalGate
from guardian.changes import ChangeExecutor, Outcome, SetPluginStatus, UpdatePostFields
from guardian.checks.base import CheckStatus, not_checked, ok
from guardian.incidents import IncidentError, IncidentManager, IncidentStatus, Verification
from guardian.monitor import Monitor
from guardian.reports import build_report
from guardian.storage import ProjectStore

ADMIN = ("admin", "ad-pass")


def _monitor(project, store, vault, site, **kw):
    return Monitor(project, store, vault.project(project.id), site.client(),
                   notifier=None, confirm_delay_s=0, ssl_fetcher=cert_in(90),
                   sleep=lambda s: None, **kw)


# ---------------- approvals ----------------

def _action(store, site, **fields):
    return UpdatePostFields(store.project_id, "https://site.test", site.client(), ADMIN,
                            "posts", 7, fields or {"title": "New title"})


def test_change_refused_without_approval(store):
    site = FakeWordPress()
    action = _action(store, site)
    gate = ApprovalGate(store)
    req = gate.request(action.spec(), "fix title", "low")
    ex = ChangeExecutor(store, lambda: [])
    with pytest.raises(ApprovalError):
        ex.execute(action, req["id"])          # pending, not approved
    assert site.posts[7]["title"] == "Old title"


def test_approval_bound_to_exact_action_and_single_use(store):
    site = FakeWordPress()
    gate = ApprovalGate(store)
    req = gate.request(_action(store, site).spec(), "fix", "low")
    gate.decide(req["id"], approve=True)
    other = _action(store, site, title="Something else")
    with pytest.raises(ApprovalError):
        gate.consume(req["id"], other.spec())
    gate.consume(req["id"], _action(store, site).spec())
    with pytest.raises(ApprovalError):
        gate.consume(req["id"], _action(store, site).spec())


def test_approval_expires(store):
    site = FakeWordPress()
    gate = ApprovalGate(store)
    req = gate.request(_action(store, site).spec(), "fix", "low", ttl_s=-1)
    with pytest.raises(ApprovalError):
        gate.decide(req["id"], approve=True)


def test_approval_cannot_target_other_project(home):
    store = ProjectStore(home, "site-one")
    with pytest.raises(ApprovalError):
        ApprovalGate(store).request({"project": "site-two", "type": "x"}, "r", "low")


# ---------------- incidents ----------------

def test_fixed_status_requires_passing_verification(store):
    mgr = IncidentManager(store)
    from guardian.checks.base import fail
    inc, _ = mgr.record_failure(fail("uptime:/", "uptime", "down"))
    with pytest.raises(IncidentError):
        mgr.set_status(inc["id"], IncidentStatus.FIXED_VERIFIED)
    with pytest.raises(IncidentError):
        mgr.mark_fixed(inc["id"], Verification([]))
    with pytest.raises(IncidentError):  # NOT_CHECKED is not a pass
        mgr.mark_fixed(inc["id"], Verification([not_checked("x", "y", "no access")]))
    done = mgr.mark_fixed(inc["id"], Verification([ok("uptime:/", "uptime", "200")]))
    assert done["status"] == "fixed_verified"


# ---------------- change execution ----------------

def _approved(store, action):
    gate = ApprovalGate(store)
    req = gate.request(action.spec(), "fix", "low")
    gate.decide(req["id"], approve=True)
    return req["id"]


def test_change_applied_backed_up_and_verified(store, vault):
    site = FakeWordPress()
    project = make_project()
    monitor = _monitor(project, store, vault, site)
    action = _action(store, site)
    from guardian.checks.base import fail
    inc, _ = IncidentManager(store).record_failure(fail("content:title", "content", "bad title"))
    result = ChangeExecutor(store, monitor.quick_checks).execute(action, _approved(store, action), inc["id"])
    assert result.outcome is Outcome.APPLIED_VERIFIED
    assert result.verification.passed
    assert site.posts[7]["title"] == "New title"
    assert "Old title" in open(result.backup.location, encoding="utf-8").read()
    assert IncidentManager(store).get(inc["id"])["status"] == "fixed_verified"


def test_change_that_breaks_site_is_rolled_back(store, vault):
    site = FakeWordPress()
    site.break_on_save = True
    monitor = _monitor(make_project(), store, vault, site)
    action = _action(store, site)

    def restore_after_rollback(request, _orig=site.handler):
        resp = _orig(request)
        if request.method == "POST" and site.posts[7]["title"] == "Old title":
            site.home_status, site.home_html = 200, "<html><h1>Welcome</h1></html>"
        return resp
    site.handler = restore_after_rollback
    result = ChangeExecutor(store, monitor.quick_checks).execute(action, _approved(store, action))
    assert result.outcome is Outcome.FAILED_ROLLED_BACK
    assert not result.verification.passed
    assert site.posts[7]["title"] == "Old title"


def test_change_not_persisted_is_not_reported_success(store, vault):
    site = FakeWordPress()
    site.ignore_saves = True
    monitor = _monitor(make_project(), store, vault, site)
    action = _action(store, site)
    result = ChangeExecutor(store, monitor.quick_checks).execute(action, _approved(store, action))
    assert result.outcome is not Outcome.APPLIED_VERIFIED


def test_no_backup_no_change(store, vault):
    site = FakeWordPress()
    site.backup_unavailable = True
    action = _action(store, site)
    result = ChangeExecutor(store, lambda: []).execute(action, _approved(store, action))
    assert result.outcome is Outcome.REFUSED_NO_BACKUP
    assert site.posts[7]["title"] == "Old title"


def test_plugin_deactivation_with_editor_credential_fails_safely(store, vault):
    site = FakeWordPress()
    monitor = _monitor(make_project(), store, vault, site)
    action = SetPluginStatus(store.project_id, "https://site.test", site.client(),
                             ("editor", "ed-pass"), "akismet/akismet", "inactive")
    result = ChangeExecutor(store, monitor.quick_checks).execute(action, _approved(store, action))
    assert result.outcome is Outcome.REFUSED_NO_BACKUP
    assert site.plugins["akismet/akismet"]["status"] == "active"


def test_plugin_deactivation_verified(store, vault):
    site = FakeWordPress()
    monitor = _monitor(make_project(), store, vault, site)
    action = SetPluginStatus(store.project_id, "https://site.test", site.client(), ADMIN,
                             "akismet/akismet", "inactive")
    result = ChangeExecutor(store, monitor.quick_checks).execute(action, _approved(store, action))
    assert result.outcome is Outcome.APPLIED_VERIFIED
    assert site.plugins["akismet/akismet"]["status"] == "inactive"


# ---------------- monitoring ----------------

def test_monitor_opens_incident_then_verifies_recovery(store, vault):
    site = FakeWordPress()
    project = make_project()
    site.home_status = 500
    s1 = _monitor(project, store, vault, site).run()
    assert any(i["check_name"] == "uptime:/" for i in s1.new_incidents)
    assert s1.alerts_not_sent >= 1          # no Telegram configured: reported, not faked
    s2 = _monitor(project, store, vault, site).run()
    assert not any(i["check_name"] == "uptime:/" for i in s2.new_incidents)  # deduplicated
    site.home_status = 200
    s3 = _monitor(project, store, vault, site).run()
    assert any(i["check_name"] == "uptime:/" and i["status"] == "recovered_verified" for i in s3.recovered)


def test_transient_failure_not_alerted(store, vault):
    site = FakeWordPress()
    site.fail_sequence = [503, 503]           # first pass fails (uptime + errors fetch)
    summary = _monitor(make_project(), store, vault, site).run()
    r = {x.name: x for x in summary.results}
    assert r["uptime:/"].status is CheckStatus.WARN
    assert not any(i["check_name"] == "uptime:/" for i in summary.new_incidents)


def test_offline_monitor_does_not_report_sites_down(store, vault):
    site = FakeWordPress()
    site.home_status = 500
    summary = _monitor(make_project(), store, vault, site, connectivity=lambda: False).run()
    assert [r.status for r in summary.results] == [CheckStatus.NOT_CHECKED]
    assert summary.new_incidents == []


def test_unreachable_site_never_yields_false_ok(store, vault):
    import httpx

    def boom(request):
        raise httpx.ConnectError("network unreachable")
    site = FakeWordPress()
    site.handler = boom
    summary = _monitor(make_project(seo=True), store, vault, site).run()
    oks = [r.name for r in summary.results if r.status is CheckStatus.OK]
    assert oks == ["ssl"]  # only the (separately faked) certificate check can pass
    r = {x.name: x for x in summary.results}
    assert r["seo:sitemap"].status is CheckStatus.NOT_CHECKED
    assert r["security:user_enumeration"].status is CheckStatus.NOT_CHECKED


def test_turning_seo_off_closes_seo_incidents(store, vault):
    site = FakeWordPress()
    site.robots = "User-agent: *\nDisallow: /\n"
    _monitor(make_project(seo=True), store, vault, site).run()
    assert any(i["check_name"] == "seo:robots" for i in IncidentManager(store).list())
    _monitor(make_project(seo=False), store, vault, site).run()
    assert not any(i["data"]["category"] == "seo" for i in IncidentManager(store).list())


def test_internet_check_ignores_proxy_403():
    import httpx
    from guardian.monitor import internet_available
    blocked = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403)))
    assert internet_available(blocked) is False
    fine = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(204)))
    assert internet_available(fine) is True


def test_monitor_refuses_mixed_project_objects(home, vault):
    store = ProjectStore(home, "site-two")
    with pytest.raises(ValueError):
        _monitor(make_project("site-one"), store, vault, FakeWordPress())


def test_report_has_no_seo_section_when_disabled(store, vault):
    site = FakeWordPress()
    off = make_project(seo=False)
    summary = _monitor(off, store, vault, site).run()
    text = build_report(off, store, summary.results, summary.run_id)
    assert "SEO" not in text.replace("seo=", "")
    on = make_project(seo=True)
    summary = _monitor(on, store, vault, site).run()
    text = build_report(on, store, summary.results, summary.run_id)
    assert "SEO റിപ്പോർട്ട് / SEO report" in text
    assert "Not checked" in text


def test_report_summary_counts_seo_not_checked(store, vault):
    site = FakeWordPress()
    on = make_project(seo=True)
    summary = _monitor(on, store, vault, site).run()
    text = build_report(on, store, summary.results, summary.run_id)
    seo_line = next(l for l in text.splitlines() if l.startswith("SEO: Problems"))
    # Search Console indexing + keywords + Rank Math settings (no WordPress login)
    assert "Not checked 3" in seo_line
