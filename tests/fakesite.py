"""A simulated WordPress site served through httpx.MockTransport."""

from __future__ import annotations

import base64
import json

import httpx

HOME_OK = """<html><head><title>Example Business Kochi</title>
<meta name="description" content="Example description">
<link rel="canonical" href="https://site.test/">
<!-- Search Engine Optimization by Rank Math - https://rankmath.com/ -->
</head><body><h1>Welcome</h1>Welcome to Example</body></html>"""

CRITICAL = "<html><body>There has been a critical error on this website.</body></html>"


class FakeWordPress:
    def __init__(self):
        self.home_html = HOME_OK
        self.home_status = 200
        self.fail_sequence: list[int] = []      # statuses to return for "/" before normal
        self.robots = "User-agent: *\nDisallow: /wp-admin/\nSitemap: https://site.test/sitemap_index.xml\n"
        self.users = {  # user -> (password, roles, caps)
            "editor": ("ed-pass", ["editor"], {"edit_posts": True, "edit_pages": True}),
            "admin": ("ad-pass", ["administrator"], {"edit_posts": True, "edit_pages": True,
                                                      "activate_plugins": True, "manage_options": True}),
            "monitor": ("mon-pass", ["guardian_monitor"], {"read": True, "guardian_read": True}),
        }
        self.connector = False
        self.plugins = {"akismet/akismet": {"name": "Akismet", "version": "5.0", "status": "active"},
                        "pro-thing/pro-thing": {"name": "Pro Thing", "version": "1.0", "status": "active"}}
        self.posts = {7: {"title": "Old title", "content": "Old body", "status": "publish"}}
        self.break_on_save = False      # saving a post breaks the homepage
        self.ignore_saves = False       # REST accepts but does not persist
        self.backup_unavailable = False
        self.requests: list[str] = []

    def _user(self, request: httpx.Request):
        header = request.headers.get("authorization", "")
        if not header.startswith("Basic "):
            return None
        user, _, password = base64.b64decode(header[6:]).decode().partition(":")
        info = self.users.get(user)
        if info and info[0] == password:
            return user, info[1], info[2]
        return "INVALID"

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append(f"{request.method} {path}")
        user = self._user(request)
        if user == "INVALID":
            return httpx.Response(401, json={"code": "invalid_auth"})
        caps = user[2] if user else {}

        if path == "/":
            if self.fail_sequence:
                return httpx.Response(self.fail_sequence.pop(0), text="err")
            return httpx.Response(self.home_status, text=self.home_html,
                                  headers={"strict-transport-security": "max-age=1",
                                           "x-content-type-options": "nosniff"})
        if path == "/robots.txt":
            return httpx.Response(200, text=self.robots)
        if path == "/sitemap_index.xml":
            return httpx.Response(200, text="<?xml version='1.0'?><sitemapindex/>")
        if path == "/wp-json/":
            ns = ["wp/v2"] + (["guardian/v1"] if self.connector else [])
            return httpx.Response(200, json={"namespaces": ns})
        if path == "/wp-json/wp/v2/users/me":
            if not user:
                return httpx.Response(401, json={})
            return httpx.Response(200, json={"slug": user[0], "roles": user[1], "capabilities": caps})
        if path == "/wp-json/wp/v2/users":
            return httpx.Response(200, json=[])
        if path in ("/wp-json/wp/v2/pages", "/wp-json/wp/v2/posts", "/wp-json/wp/v2/media"):
            return httpx.Response(200, json=[], headers={"x-wp-total": "3"})
        if path == "/wp-json/wp/v2/plugins":
            if not caps.get("activate_plugins"):
                return httpx.Response(403, json={"code": "rest_cannot_view_plugins"})
            return httpx.Response(200, json=[{"plugin": k, **v} for k, v in self.plugins.items()])
        if path.startswith("/wp-json/wp/v2/plugins/"):
            if not caps.get("activate_plugins"):
                return httpx.Response(403, json={})
            key = path.removeprefix("/wp-json/wp/v2/plugins/")
            if request.method == "POST":
                self.plugins[key]["status"] = json.loads(request.content)["status"]
            return httpx.Response(200, json={"plugin": key, **self.plugins[key]})
        if path in ("/wp-json/wp/v2/themes", "/wp-json/wp/v2/settings"):
            if not caps.get("manage_options"):
                return httpx.Response(403, json={})
            if path.endswith("themes"):
                return httpx.Response(200, json=[{"stylesheet": "astra", "version": "4.0", "status": "active"}])
            return httpx.Response(200, json={"title": "Example", "timezone": "Asia/Kolkata"})
        if path.startswith("/wp-json/wp/v2/posts/"):
            pid = int(path.rsplit("/", 1)[1])
            if request.method == "GET" and self.backup_unavailable:
                return httpx.Response(500, json={})
            if request.method == "POST":
                if not caps.get("edit_posts"):
                    return httpx.Response(403, json={})
                if not self.ignore_saves:
                    self.posts[pid].update(json.loads(request.content))
                if self.break_on_save:
                    self.home_status, self.home_html = 500, CRITICAL
            post = self.posts[pid]
            return httpx.Response(200, json={k: {"raw": v} if k in ("title", "content") else v
                                             for k, v in post.items()})
        if path == "/wp-json/guardian/v1/status":
            if not caps.get("guardian_read") or not self.connector:
                return httpx.Response(403, json={})
            seo = request.url.params.get("seo")
            return httpx.Response(200, json={
                "connector_version": "0.1.0",
                "core": {"version": "6.6"}, "php": {"version": "8.2.1"},
                "updates": {"core": "6.7", "plugins": [{"name": "Pro Thing", "current": "1.0", "new": "1.1"}],
                            "themes": []},
                "php_errors": {"available": True, "entries": [
                    "[01-Jan-2026] PHP Fatal error: Uncaught Error in /wp-content/plugins/pro-thing/x.php:10"]},
                "security": {"administrators": [{"user_login": "admin"}]},
                **({"seo": {"rank_math": {"general": {}}, "blog_public": True}} if seo else {}),
            })
        return httpx.Response(404, text="not found")

    def client(self) -> httpx.Client:
        # Look up self.handler at request time so tests can wrap it later.
        return httpx.Client(transport=httpx.MockTransport(lambda r: self.handler(r)),
                            base_url="https://site.test")


def wporg_client(latest: dict[str, str]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        slug = request.url.params.get("request[slug]")
        if slug in latest:
            return httpx.Response(200, json={"version": latest[slug]})
        return httpx.Response(404, json={"error": "Plugin not found."})
    return httpx.Client(transport=httpx.MockTransport(handler))
