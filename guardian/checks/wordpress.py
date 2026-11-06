"""WordPress inspection over the REST API (Application Passwords).

Least privilege: the agent uses a READ credential for inspection and a separate
CHANGE credential only for approved changes. If the read credential is an
Administrator, the agent warns that it is over-privileged.

Anything the REST API cannot expose (core version, update availability, PHP
error log, Rank Math settings, file integrity) is reported NOT_CHECKED unless
the Guardian Connector plugin is installed.
"""

from __future__ import annotations

from typing import Any

import httpx

from ..models import ProjectConfig
from ..vault import ProjectCredentials
from .base import CheckResult, Severity, fail, not_checked, ok, warn

READ_USER = "wp_read_user"
READ_PASSWORD = "wp_read_app_password"
CHANGE_USER = "wp_change_user"
CHANGE_PASSWORD = "wp_change_app_password"

WPORG_PLUGIN_API = "https://api.wordpress.org/plugins/info/1.2/"
CONNECTOR_NS = "guardian/v1"


def read_auth(creds: ProjectCredentials) -> tuple[str, str] | None:
    user, password = creds.get(READ_USER), creds.get(READ_PASSWORD)
    return (user, password) if user and password else None


def change_auth(creds: ProjectCredentials) -> tuple[str, str] | None:
    user, password = creds.get(CHANGE_USER), creds.get(CHANGE_PASSWORD)
    return (user, password) if user and password else None


class WordPressInspector:
    def __init__(self, project: ProjectConfig, client: httpx.Client, creds: ProjectCredentials,
                 wporg_client: httpx.Client | None = None):
        self.project = project
        self.client = client
        self.auth = read_auth(creds)
        self.wporg = wporg_client
        self.api = project.url + "/wp-json"

    def _get(self, route: str, params: dict[str, Any] | None = None, auth: bool = True):
        try:
            return self.client.get(self.api + route, params=params,
                                   auth=self.auth if (auth and self.auth) else None,
                                   follow_redirects=True)
        except httpx.HTTPError as exc:
            return exc

    # ------------------------------------------------------------------
    def run(self) -> list[CheckResult]:
        results: list[CheckResult] = []
        index = self._get("/", auth=False)
        if isinstance(index, Exception) or index.status_code != 200:
            reason = index if isinstance(index, Exception) else f"HTTP {index.status_code}"
            return [fail("wp:rest_api", "wordpress", f"WordPress REST API unreachable ({reason}); "
                         "no WordPress inspection possible", Severity.HIGH)]
        try:
            namespaces = index.json().get("namespaces", [])
        except ValueError:
            return [fail("wp:rest_api", "wordpress", "REST API returned non-JSON (blocked by a "
                         "security plugin or firewall?)", Severity.HIGH)]
        results.append(ok("wp:rest_api", "wordpress", "REST API reachable"))
        has_connector = CONNECTOR_NS in namespaces

        if not self.auth:
            results.append(not_checked("wp:auth", "wordpress",
                                       "No read credential stored (wp_read_user / wp_read_app_password); "
                                       "only public data was checked."))
            results.extend(self._public_content())
            results.extend(self._unavailable(has_connector=False, why="no credential"))
            return results

        me = self._get("/wp/v2/users/me", {"context": "edit"})
        if isinstance(me, Exception) or me.status_code != 200:
            code = me if isinstance(me, Exception) else f"HTTP {me.status_code}"
            results.append(fail("wp:auth", "wordpress", f"Read credential rejected ({code}). "
                                "Application Password revoked or user removed?", Severity.HIGH))
            results.extend(self._unavailable(has_connector=False, why="credential rejected"))
            return results
        user = me.json()
        roles = user.get("roles", [])
        caps = {k for k, v in (user.get("capabilities") or {}).items() if v}
        results.append(ok("wp:auth", "wordpress", f"Authenticated as {user.get('slug')} (roles: {', '.join(roles) or 'none'})",
                          roles=roles))
        if "administrator" in roles:
            results.append(warn("wp:least_privilege", "wordpress",
                                "Read credential is an Administrator: more access than monitoring needs. "
                                "Install the Guardian Connector and use its 'Guardian Monitor' role.",
                                Severity.MEDIUM))
        else:
            results.append(ok("wp:least_privilege", "wordpress", "Read credential is not an Administrator"))

        results.extend(self._content(caps))
        results.extend(self._plugins_themes(caps))
        if has_connector:
            results.extend(self._connector())
        else:
            results.extend(self._unavailable(has_connector=False, why="Guardian Connector not installed"))
        return results

    # ------------------------------------------------------------------
    def _count(self, route: str, params: dict[str, Any]) -> int | None:
        resp = self._get(route, params | {"per_page": 1})
        if isinstance(resp, Exception) or resp.status_code != 200:
            return None
        total = resp.headers.get("x-wp-total")
        return int(total) if total and total.isdigit() else None

    def _public_content(self) -> list[CheckResult]:
        counts = {kind: self._count(f"/wp/v2/{kind}", {}) for kind in ("pages", "posts", "media")}
        return [ok("wp:content", "wordpress",
                   "Published content: " + ", ".join(f"{k}={v if v is not None else '?'}" for k, v in counts.items()),
                   counts=counts, scope="public only")]

    def _content(self, caps: set[str]) -> list[CheckResult]:
        counts: dict[str, int | None] = {}
        for kind, cap in (("pages", "edit_pages"), ("posts", "edit_posts")):
            params = {"status": "publish,draft,pending,private,future"} if cap in caps else {}
            counts[kind] = self._count(f"/wp/v2/{kind}", params)
        counts["media"] = self._count("/wp/v2/media", {})
        scope = "including drafts" if {"edit_pages", "edit_posts"} <= caps else "published only"
        return [ok("wp:content", "wordpress",
                   f"Content ({scope}): " + ", ".join(f"{k}={v if v is not None else '?'}" for k, v in counts.items()),
                   counts=counts, scope=scope)]

    def _plugins_themes(self, caps: set[str]) -> list[CheckResult]:
        results: list[CheckResult] = []
        plugins = self._get("/wp/v2/plugins")
        if isinstance(plugins, Exception) or plugins.status_code != 200:
            results.append(not_checked("wp:plugins", "wordpress",
                                       "Plugin list not available to this credential (WordPress requires "
                                       "the 'activate_plugins' capability, i.e. Administrator, or the "
                                       "Guardian Connector)."))
        else:
            items = [{"plugin": p.get("plugin"), "name": p.get("name"), "version": p.get("version"),
                      "status": p.get("status")} for p in plugins.json()]
            results.append(ok("wp:plugins", "wordpress", f"{len(items)} plugins installed "
                              f"({sum(p['status'] == 'active' for p in items)} active)", plugins=items))
            if self.project.enabled("updates"):
                results.extend(self._plugin_updates_wporg(items))
        themes = self._get("/wp/v2/themes")
        if isinstance(themes, Exception) or themes.status_code != 200:
            results.append(not_checked("wp:themes", "wordpress",
                                       "Theme list not available to this credential (needs Administrator "
                                       "or the Guardian Connector)."))
        else:
            items = [{"stylesheet": t.get("stylesheet"), "version": t.get("version"),
                      "status": t.get("status")} for t in themes.json()]
            results.append(ok("wp:themes", "wordpress", f"{len(items)} themes installed", themes=items))
        settings = self._get("/wp/v2/settings")
        if isinstance(settings, Exception) or settings.status_code != 200:
            results.append(not_checked("wp:settings", "wordpress",
                                       "Settings not available to this credential (needs 'manage_options')."))
        else:
            s = settings.json()
            results.append(ok("wp:settings", "wordpress",
                              "Core settings readable (REST exposes only a subset of settings)",
                              settings={k: s.get(k) for k in ("title", "url", "timezone", "default_comment_status")}))
        return results

    def _plugin_updates_wporg(self, plugins: list[dict[str, Any]]) -> list[CheckResult]:
        if self.wporg is None:
            return [not_checked("wp:updates", "updates", "WordPress.org update lookup disabled")]
        outdated, unknown = [], []
        for p in plugins:
            slug = (p["plugin"] or "").split("/")[0]
            try:
                resp = self.wporg.get(WPORG_PLUGIN_API, params={
                    "action": "plugin_information", "request[slug]": slug,
                    "request[fields][sections]": 0})
                latest = resp.json().get("version") if resp.status_code == 200 else None
            except (httpx.HTTPError, ValueError):
                latest = None
            if not latest:
                unknown.append(p["name"])  # premium / custom plugin: not on WordPress.org
            elif _version_tuple(latest) > _version_tuple(p["version"] or "0"):
                outdated.append({"name": p["name"], "installed": p["version"], "latest": latest})
        results: list[CheckResult] = []
        if outdated:
            results.append(warn("wp:updates:plugins", "updates",
                                f"{len(outdated)} plugin update(s) available: " +
                                ", ".join(f"{o['name']} {o['installed']}→{o['latest']}" for o in outdated),
                                Severity.MEDIUM, outdated=outdated))
        else:
            results.append(ok("wp:updates:plugins", "updates", "WordPress.org plugins are up to date"))
        if unknown:
            results.append(not_checked("wp:updates:premium", "updates",
                                       "Update status unknown for plugins not on WordPress.org "
                                       f"(premium/custom): {', '.join(unknown)}. Needs the Guardian Connector."))
        return results

    def _connector(self) -> list[CheckResult]:
        resp = self._get(f"/{CONNECTOR_NS}/status",
                         {"seo": 1} if self.project.enabled("seo") else None)
        if isinstance(resp, Exception) or resp.status_code != 200:
            code = resp if isinstance(resp, Exception) else f"HTTP {resp.status_code}"
            return [fail("wp:connector", "wordpress",
                         f"Guardian Connector installed but status call failed ({code})", Severity.MEDIUM)]
        data = resp.json()
        results = [ok("wp:connector", "wordpress", f"Guardian Connector {data.get('connector_version')} responding")]
        core = data.get("core", {})
        php = data.get("php", {})
        results.append(ok("wp:core", "wordpress",
                          f"WordPress {core.get('version')} on PHP {php.get('version')}", core=core, php=php))
        if self.project.enabled("updates"):
            updates = data.get("updates", {})
            pending = (["core " + updates["core"]] if updates.get("core") else []) + \
                [f"plugin {u['name']} {u['current']}→{u['new']}" for u in updates.get("plugins", [])] + \
                [f"theme {u['name']} {u['current']}→{u['new']}" for u in updates.get("themes", [])]
            if pending:
                results.append(warn("wp:updates", "updates", f"{len(pending)} update(s) available: " +
                                    "; ".join(pending), Severity.MEDIUM, updates=updates))
            else:
                results.append(ok("wp:updates", "updates", "Core, plugins and themes up to date"))
        log = data.get("php_errors", {})
        if not log.get("available"):
            results.append(not_checked("errors:php_log", "errors",
                                       f"PHP error log not readable: {log.get('reason', 'unknown reason')}"))
        else:
            fatal = [e for e in log.get("entries", []) if "Fatal" in e or "Parse error" in e]
            if fatal:
                results.append(fail("errors:php_log", "errors", f"{len(fatal)} PHP fatal error(s) in recent log",
                                    Severity.CRITICAL, entries=fatal[-10:]))
            elif log.get("entries"):
                results.append(warn("errors:php_log", "errors", f"{len(log['entries'])} PHP warning(s)/notice(s) in recent log",
                                    Severity.LOW, entries=log["entries"][-10:]))
            else:
                results.append(ok("errors:php_log", "errors", "No recent PHP errors in log"))
        admins = data.get("security", {}).get("administrators")
        if admins is not None:
            results.append(ok("security:admins", "security", f"{len(admins)} administrator account(s)",
                              administrators=admins))
        if self.project.enabled("seo"):
            if data.get("seo", {}).get("blog_public") is False:
                results.append(fail("seo:discourage_search", "seo",
                                    "WordPress 'Discourage search engines from indexing this site' is ON",
                                    Severity.CRITICAL))
            rm = data.get("seo", {}).get("rank_math")
            if rm is None:
                results.append(not_checked("seo:rank_math_settings", "seo", "Rank Math not active or not reported"))
            else:
                results.append(ok("seo:rank_math_settings", "seo", "Rank Math settings read", rank_math=rm))
        return results

    def _unavailable(self, has_connector: bool, why: str) -> list[CheckResult]:
        items = [("wp:core", "wordpress", "WordPress core version"),
                 ("errors:php_log", "errors", "PHP error log")]
        if self.project.enabled("updates"):
            items.append(("wp:updates:core_themes", "updates", "Core and theme update availability"))
        if self.project.enabled("seo"):
            items.append(("seo:rank_math_settings", "seo", "Rank Math settings"))
        return [not_checked(n, c, f"{label}: not exposed by the WordPress REST API ({why}). "
                            "Needs the Guardian Connector plugin.") for n, c, label in items]


def _version_tuple(version: str) -> tuple[int, ...]:
    parts = []
    for piece in version.replace("-", ".").split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)
