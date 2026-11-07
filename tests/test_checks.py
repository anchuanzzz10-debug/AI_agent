from conftest import cert_in, make_project
from fakesite import CRITICAL, FakeWordPress, wporg_client
from guardian.checks.base import CheckStatus
from guardian.checks.http_checks import error_detection_checks, security_checks, uptime_checks
from guardian.checks.seo_checks import seo_checks
from guardian.checks.ssl_check import ssl_checks
from guardian.checks.wordpress import WordPressInspector


def by_name(results):
    return {r.name: r for r in results}


def test_uptime_ok_and_expected_text():
    site = FakeWordPress()
    r = by_name(uptime_checks(make_project(expected_text="Welcome to Example"), site.client()))
    assert r["uptime:/"].status is CheckStatus.OK
    assert r["uptime:expected_text"].status is CheckStatus.OK
    site.home_html = "<html>hacked</html>"
    r = by_name(uptime_checks(make_project(expected_text="Welcome to Example"), site.client()))
    assert r["uptime:expected_text"].status is CheckStatus.FAIL


def test_http_500_is_critical():
    site = FakeWordPress()
    site.home_status = 500
    r = by_name(uptime_checks(make_project(), site.client()))
    assert r["uptime:/"].status is CheckStatus.FAIL and r["uptime:/"].severity.value == "critical"


def test_critical_error_page_detected_and_php_log_honestly_not_checked():
    site = FakeWordPress()
    site.home_html = CRITICAL
    r = by_name(error_detection_checks(make_project(), site.client()))
    assert r["errors:/"].status is CheckStatus.FAIL
    assert r["errors:php_log"].status is CheckStatus.NOT_CHECKED
    assert "Connector" in r["errors:php_log"].summary


def test_ssl_thresholds():
    p = make_project()
    assert ssl_checks(p, cert_in(90))[0].status is CheckStatus.OK
    assert ssl_checks(p, cert_in(10))[0].status is CheckStatus.WARN
    assert ssl_checks(p, cert_in(2))[0].status is CheckStatus.FAIL
    assert ssl_checks(p, cert_in(-3))[0].summary.startswith("Certificate EXPIRED")


def test_exposed_env_file_detected():
    site = FakeWordPress()
    original = site.handler

    def handler(request):
        if request.url.path == "/.env":
            import httpx
            return httpx.Response(200, text="DB_PASSWORD=x\n")
        return original(request)
    site.handler = handler
    r = by_name(security_checks(make_project(), site.client()))
    assert r["security:exposed/.env"].status is CheckStatus.FAIL
    assert r["security:file_integrity"].status is CheckStatus.NOT_CHECKED


def test_seo_disabled_produces_nothing():
    assert seo_checks(make_project(seo=False), FakeWordPress().client()) == []


def test_seo_enabled_checks_and_honest_gsc_status():
    site = FakeWordPress()
    r = by_name(seo_checks(make_project(seo=True), site.client()))
    assert r["seo:noindex"].status is CheckStatus.OK
    assert r["seo:sitemap"].status is CheckStatus.OK
    assert r["seo:plugin_detect"].detail["rank_math"] is True
    assert r["seo:indexing"].status is CheckStatus.NOT_CHECKED
    assert r["seo:keywords"].status is CheckStatus.NOT_CHECKED
    site.home_html = site.home_html.replace("<title>", '<meta name="robots" content="noindex"><title>')
    site.robots = "User-agent: *\nDisallow: /\n"
    r = by_name(seo_checks(make_project(seo=True), site.client()))
    assert r["seo:noindex"].status is CheckStatus.FAIL
    assert r["seo:robots"].status is CheckStatus.FAIL


def test_wp_without_credentials_reports_not_checked(vault):
    r = by_name(WordPressInspector(make_project(), FakeWordPress().client(),
                                   vault.project("site-one")).run())
    assert r["wp:auth"].status is CheckStatus.NOT_CHECKED
    assert r["wp:core"].status is CheckStatus.NOT_CHECKED
    assert r["errors:php_log"].status is CheckStatus.NOT_CHECKED


def test_wp_editor_credential_cannot_see_plugins_and_says_so(vault):
    creds = vault.project("site-one")
    creds.set("wp_read_user", "editor")
    creds.set("wp_read_app_password", "ed-pass")
    r = by_name(WordPressInspector(make_project(), FakeWordPress().client(), creds).run())
    assert r["wp:least_privilege"].status is CheckStatus.OK
    assert r["wp:plugins"].status is CheckStatus.NOT_CHECKED
    assert "Administrator" in r["wp:plugins"].summary
    assert "including drafts" in r["wp:content"].summary


def test_wp_admin_credential_warns_overprivileged_and_finds_updates(vault):
    creds = vault.project("site-one")
    creds.set("wp_read_user", "admin")
    creds.set("wp_read_app_password", "ad-pass")
    r = by_name(WordPressInspector(make_project(), FakeWordPress().client(), creds,
                                   wporg_client({"akismet": "5.3"})).run())
    assert r["wp:least_privilege"].status is CheckStatus.WARN
    assert r["wp:plugins"].status is CheckStatus.OK
    assert r["wp:updates:plugins"].status is CheckStatus.WARN
    assert "Akismet 5.0→5.3" in r["wp:updates:plugins"].summary
    assert r["wp:updates:premium"].status is CheckStatus.NOT_CHECKED  # Pro Thing not on wp.org


def test_wp_wrong_password_fails(vault):
    creds = vault.project("site-one")
    creds.set("wp_read_user", "editor")
    creds.set("wp_read_app_password", "nope")
    r = by_name(WordPressInspector(make_project(), FakeWordPress().client(), creds).run())
    assert r["wp:auth"].status is CheckStatus.FAIL


def test_wp_connector_with_least_privilege_role(vault):
    site = FakeWordPress()
    site.connector = True
    creds = vault.project("site-one")
    creds.set("wp_read_user", "monitor")
    creds.set("wp_read_app_password", "mon-pass")
    r = by_name(WordPressInspector(make_project(seo=True), site.client(), creds).run())
    assert r["wp:least_privilege"].status is CheckStatus.OK
    assert r["wp:core"].summary.startswith("WordPress 6.6")
    assert r["wp:updates"].status is CheckStatus.WARN
    assert r["errors:php_log"].status is CheckStatus.FAIL
    assert r["seo:rank_math_settings"].status is CheckStatus.OK


def test_rank_math_line_only_for_wordpress():
    site = FakeWordPress()
    wp = by_name(seo_checks(make_project(seo=True), site.client()))
    assert "seo:plugin_detect" in wp
    other = by_name(seo_checks(make_project("plain-site", "website", seo=True), site.client()))
    assert "seo:plugin_detect" not in other
