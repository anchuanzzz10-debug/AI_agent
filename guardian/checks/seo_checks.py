"""Technical SEO checks. Only run when the project's `seo` feature is enabled."""

from __future__ import annotations

from html.parser import HTMLParser

import httpx

from ..models import ProjectConfig, ProjectType
from .base import CheckResult, Severity, fail, not_checked, ok, warn
from .http_checks import fetch


class _HeadParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title: str | None = None
        self._in_title = False
        self.meta: dict[str, str] = {}
        self.canonical: str | None = None
        self.h1_count = 0
        self.comments: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "title":
            self._in_title = True
            self.title = ""
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or "").lower()
            if key:
                self.meta[key] = a.get("content", "")
        elif tag == "link" and "canonical" in a.get("rel", "").lower().split():
            self.canonical = a.get("href")
        elif tag == "h1":
            self.h1_count += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title and self.title is not None:
            self.title += data

    def handle_comment(self, data):
        self.comments.append(data)


def _robots_blocks_all(robots: str) -> bool:
    agent_all = False
    for raw in robots.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = (p.strip() for p in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            agent_all = value == "*"
        elif key == "disallow" and agent_all and value == "/":
            return True
    return False


def seo_checks(project: ProjectConfig, client: httpx.Client) -> list[CheckResult]:
    if not project.enabled("seo"):
        return []  # SEO switched off: produce nothing at all
    results: list[CheckResult] = []

    # robots.txt
    robots_resp, _, err = fetch(client, project.url + "/robots.txt")
    sitemaps: list[str] = []
    if robots_resp is None:
        results.append(not_checked("seo:robots", "seo", f"robots.txt unreachable: {err}"))
    elif robots_resp.status_code == 200:
        if _robots_blocks_all(robots_resp.text):
            results.append(fail("seo:robots", "seo",
                                "robots.txt blocks ALL search engines (Disallow: /)", Severity.CRITICAL))
        else:
            results.append(ok("seo:robots", "seo", "robots.txt does not block the site"))
        sitemaps = [l.split(":", 1)[1].strip() for l in robots_resp.text.splitlines()
                    if l.lower().startswith("sitemap:")]
    else:
        results.append(warn("seo:robots", "seo", f"robots.txt returned HTTP {robots_resp.status_code}",
                            Severity.LOW))

    # homepage meta
    home, _, err = fetch(client, project.url + "/")
    if home is None or home.status_code >= 400:
        results.append(not_checked("seo:homepage", "seo", f"Homepage not available for SEO checks ({err or home.status_code})"))
    else:
        parser = _HeadParser()
        parser.feed(home.text)
        robots_meta = parser.meta.get("robots", "").lower()
        header_robots = home.headers.get("x-robots-tag", "").lower()
        if "noindex" in robots_meta or "noindex" in header_robots:
            results.append(fail("seo:noindex", "seo",
                                "Homepage is set to NOINDEX: it will drop out of Google",
                                Severity.CRITICAL, meta=robots_meta, header=header_robots))
        else:
            results.append(ok("seo:noindex", "seo", "Homepage is indexable"))
        title = (parser.title or "").strip()
        if not title:
            results.append(fail("seo:title", "seo", "Homepage has no <title>", Severity.HIGH))
        elif not 10 <= len(title) <= 65:
            results.append(warn("seo:title", "seo", f"Title length {len(title)} chars (aim 10-65)",
                                Severity.LOW, title=title))
        else:
            results.append(ok("seo:title", "seo", f"Title present ({len(title)} chars)", title=title))
        desc = parser.meta.get("description", "").strip()
        if not desc:
            results.append(warn("seo:meta_description", "seo", "Homepage has no meta description",
                                Severity.MEDIUM))
        else:
            results.append(ok("seo:meta_description", "seo", f"Meta description present ({len(desc)} chars)"))
        if parser.canonical:
            results.append(ok("seo:canonical", "seo", f"Canonical: {parser.canonical}"))
        else:
            results.append(warn("seo:canonical", "seo", "No canonical link on homepage", Severity.LOW))
        if parser.h1_count != 1:
            results.append(warn("seo:h1", "seo", f"Homepage has {parser.h1_count} <h1> headings (aim 1)",
                                Severity.LOW))
        else:
            results.append(ok("seo:h1", "seo", "Exactly one <h1>"))
        if project.type is ProjectType.WORDPRESS:   # Rank Math only exists on WordPress
            rank_math = any("rank math" in c.lower() for c in parser.comments) or "rank-math" in home.text[:50000]
            results.append(ok("seo:plugin_detect", "seo",
                              "Rank Math output detected on homepage" if rank_math
                              else "Rank Math output not detected on homepage", rank_math=rank_math))

    # sitemap
    candidates = sitemaps or [project.url + "/sitemap_index.xml", project.url + "/sitemap.xml",
                              project.url + "/wp-sitemap.xml"]
    found = None
    any_response = False
    for url in candidates:
        resp, _, _ = fetch(client, url)
        any_response = any_response or resp is not None
        if resp is not None and resp.status_code == 200 and "<" in resp.text[:200]:
            found = url
            break
    if found:
        results.append(ok("seo:sitemap", "seo", f"Sitemap available: {found}"))
    elif not any_response:
        results.append(not_checked("seo:sitemap", "seo", "Sitemap not checked: no request reached the site"))
    else:
        results.append(fail("seo:sitemap", "seo", "No reachable XML sitemap", Severity.MEDIUM,
                            tried=candidates))

    # Search Console based features are honest about not being implemented yet.
    for name, label in (("seo:indexing", "Indexing status (Search Console)"),
                        ("seo:keywords", "Keyword positions/clicks (Search Console)")):
        results.append(not_checked(name, "seo",
                                   f"{label}: Search Console integration is not implemented yet "
                                   "(planned). Nothing was checked."))
    return results
