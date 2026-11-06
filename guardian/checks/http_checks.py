"""External HTTP checks: uptime, visible errors, application health, exposed files.

These need no credentials, so they work for every project type.
"""

from __future__ import annotations

import re
import time

import httpx

from ..models import ProjectConfig, ProjectType
from .base import CheckResult, Severity, fail, not_checked, ok, warn

# Visible symptoms of server-side errors. (pattern, label, severity)
ERROR_SIGNATURES: list[tuple[re.Pattern[str], str, Severity]] = [
    (re.compile(r"There has been a critical error on (this|your) website", re.I),
     "WordPress critical error page (PHP fatal error)", Severity.CRITICAL),
    (re.compile(r"Error establishing a database connection", re.I),
     "Database connection error", Severity.CRITICAL),
    (re.compile(r"<b>(Fatal error|Parse error)</b>:", re.I),
     "PHP fatal/parse error printed on page", Severity.CRITICAL),
    (re.compile(r"<b>Warning</b>:.+ on line <b>\d+</b>", re.I | re.S),
     "PHP warning printed on page (also leaks server paths)", Severity.MEDIUM),
    (re.compile(r"Briefly unavailable for scheduled maintenance", re.I),
     "Site stuck in maintenance mode", Severity.HIGH),
]


def fetch(client: httpx.Client, url: str) -> tuple[httpx.Response | None, float, str | None]:
    started = time.monotonic()
    try:
        response = client.get(url, follow_redirects=True)
        return response, time.monotonic() - started, None
    except httpx.HTTPError as exc:
        return None, time.monotonic() - started, f"{type(exc).__name__}: {exc}"


def uptime_checks(project: ProjectConfig, client: httpx.Client) -> list[CheckResult]:
    results: list[CheckResult] = []
    limit = project.thresholds.get("response_time_warn_s", 3.0)
    for path in project.key_paths:
        url = project.url + path
        name = f"uptime:{path}"
        response, elapsed, error = fetch(client, url)
        if response is None:
            results.append(fail(name, "uptime", f"{url} unreachable ({error})",
                                Severity.CRITICAL, url=url))
            continue
        if response.status_code >= 500:
            results.append(fail(name, "uptime", f"{url} returned HTTP {response.status_code}",
                                Severity.CRITICAL, url=url, status=response.status_code))
        elif response.status_code >= 400:
            results.append(fail(name, "uptime", f"{url} returned HTTP {response.status_code}",
                                Severity.HIGH, url=url, status=response.status_code))
        elif elapsed > limit:
            results.append(warn(name, "uptime", f"{url} slow: {elapsed:.1f}s (limit {limit}s)",
                                url=url, seconds=round(elapsed, 2)))
        else:
            results.append(ok(name, "uptime", f"{url} HTTP {response.status_code} in {elapsed:.1f}s",
                              url=url, seconds=round(elapsed, 2)))
        if path == "/" and project.expected_text and response.status_code < 400:
            if project.expected_text not in response.text:
                results.append(fail("uptime:expected_text", "uptime",
                                    f"Expected text {project.expected_text!r} missing from homepage "
                                    "(possible defacement, broken template or wrong content)",
                                    Severity.HIGH, url=url))
            else:
                results.append(ok("uptime:expected_text", "uptime", "Expected homepage text present"))
    return results


def error_detection_checks(project: ProjectConfig, client: httpx.Client) -> list[CheckResult]:
    results: list[CheckResult] = []
    for path in project.key_paths:
        url = project.url + path
        response, _, error = fetch(client, url)
        name = f"errors:{path}"
        if response is None:
            results.append(not_checked(name, "errors", f"Page not reachable ({error})"))
            continue
        found = [(label, sev) for pattern, label, sev in ERROR_SIGNATURES
                 if pattern.search(response.text)]
        if found:
            worst = min((sev for _, sev in found), key=list(Severity).index)
            results.append(fail(name, "errors", f"{url}: " + "; ".join(l for l, _ in found),
                                worst, url=url, signatures=[l for l, _ in found]))
        else:
            results.append(ok(name, "errors", f"{url}: no visible error messages"))
    if project.type is ProjectType.WORDPRESS:
        results.append(not_checked(
            "errors:php_log", "errors",
            "PHP error log not checked from outside: only visible symptoms are detected. "
            "Needs the Guardian Connector plugin or SSH log access."))
    return results


def health_endpoint_checks(project: ProjectConfig, client: httpx.Client) -> list[CheckResult]:
    url = project.url + (project.health_path or "/health")
    response, elapsed, error = fetch(client, url)
    if response is None:
        return [fail("health", "health", f"Health endpoint unreachable: {error}",
                     Severity.CRITICAL, url=url)]
    if response.status_code != 200:
        return [fail("health", "health", f"Health endpoint HTTP {response.status_code}",
                     Severity.CRITICAL, url=url)]
    try:
        body = response.json()
    except ValueError:
        return [ok("health", "health", f"Health endpoint HTTP 200 in {elapsed:.1f}s (non-JSON body)")]
    status = str(body.get("status", "")).lower() if isinstance(body, dict) else ""
    if status and status not in {"ok", "healthy", "pass", "up"}:
        return [fail("health", "health", f"Health endpoint reports status {status!r}",
                     Severity.HIGH, url=url, body=body)]
    return [ok("health", "health", f"Health endpoint OK in {elapsed:.1f}s", body=body)]


# Files that must never be publicly downloadable. (path, marker regex or None, label)
EXPOSED_FILES = [
    ("/.env", re.compile(r"^[A-Z_]+=", re.M), "Environment file (.env) is public"),
    ("/.git/HEAD", re.compile(r"^ref: refs/", re.M), "Git repository (.git) is public"),
    ("/wp-content/debug.log", re.compile(r"PHP (Fatal|Warning|Notice|Deprecated)"),
     "WordPress debug.log is public (leaks errors and paths)"),
    ("/wp-config.php.bak", re.compile(r"DB_PASSWORD"), "Backup of wp-config.php is public"),
]


def security_checks(project: ProjectConfig, client: httpx.Client) -> list[CheckResult]:
    results: list[CheckResult] = []
    for path, marker, label in EXPOSED_FILES:
        if path.startswith("/wp-") and project.type is not ProjectType.WORDPRESS:
            continue
        response, _, error = fetch(client, project.url + path)
        name = f"security:exposed{path}"
        if response is None:
            results.append(not_checked(name, "security", f"Could not request {path}: {error}"))
        elif response.status_code == 200 and marker.search(response.text[:20000]):
            results.append(fail(name, "security", label, Severity.CRITICAL, path=path))
        else:
            results.append(ok(name, "security", f"{path} not publicly exposed"))

    response, _, error = fetch(client, project.url + "/")
    if response is None:
        results.append(not_checked("security:headers", "security", f"Homepage unreachable: {error}"))
    else:
        missing = [h for h in ("strict-transport-security", "x-content-type-options")
                   if h not in response.headers]
        if missing:
            results.append(warn("security:headers", "security",
                                "Missing security headers: " + ", ".join(missing), Severity.LOW))
        else:
            results.append(ok("security:headers", "security", "Basic security headers present"))

    if project.type is ProjectType.WORDPRESS:
        response, _, error = fetch(client, project.url + "/wp-json/wp/v2/users")
        if response is None:
            results.append(not_checked("security:user_enumeration", "security",
                                       f"Could not request the public user list: {error}"))
        elif response.status_code == 200:
            try:
                users = response.json()
            except ValueError:
                users = []
            if isinstance(users, list) and users:
                results.append(warn("security:user_enumeration", "security",
                                    f"Public REST API lists {len(users)} user account name(s); "
                                    "attackers can use these for login attempts",
                                    Severity.MEDIUM, slugs=[u.get('slug') for u in users][:10]))
            else:
                results.append(ok("security:user_enumeration", "security", "User list not public"))
        else:
            results.append(ok("security:user_enumeration", "security", "User list not public"))
        results.append(not_checked(
            "security:file_integrity", "security",
            "Core/plugin file integrity and new-admin detection need the Guardian Connector or SSH."))
    return results
