"""Runs all enabled checks for one project, confirms failures, manages
incidents and sends alerts. Each run touches exactly one project's storage."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Callable

import httpx

from .checks.base import CheckResult, CheckStatus, not_checked, warn
from .checks.http_checks import (error_detection_checks, health_endpoint_checks,
                                 security_checks, uptime_checks)
from .checks.seo_checks import seo_checks
from .checks.ssl_check import CertFetcher, fetch_cert_expiry, ssl_checks
from .checks.wordpress import WordPressInspector
from .incidents import IncidentManager, IncidentStatus, Verification
from .models import ProjectConfig
from .notify.telegram import Notifier, incident_alert, recovery_alert
from .storage import ProjectStore
from .vault import ProjectCredentials

CONFIRM_CATEGORIES = {"uptime", "errors", "health"}

# Independent, highly available endpoints used to tell "site down" apart from
# "the monitor (your laptop) is offline".
CONTROL_URLS = ("https://www.google.com/generate_204", "https://1.1.1.1/cdn-cgi/trace",
                "https://www.microsoft.com/")


def internet_available(client: httpx.Client, urls: tuple[str, ...] = CONTROL_URLS) -> bool:
    for url in urls:
        try:
            # Only a real success counts: proxies and captive portals often
            # answer blocked requests with 403/407/511.
            if 200 <= client.get(url, timeout=8).status_code < 300:
                return True
        except httpx.HTTPError:
            continue
    return False


@dataclass
class RunSummary:
    run_id: str
    results: list[CheckResult]
    new_incidents: list[dict] = field(default_factory=list)
    recovered: list[dict] = field(default_factory=list)
    alerts_sent: int = 0
    alerts_not_sent: int = 0


class Monitor:
    def __init__(self, project: ProjectConfig, store: ProjectStore, creds: ProjectCredentials,
                 client: httpx.Client, notifier: Notifier | None = None,
                 confirm_delay_s: float = 45.0, ssl_fetcher: CertFetcher = fetch_cert_expiry,
                 wporg_client: httpx.Client | None = None, sleep: Callable[[float], None] = time.sleep,
                 connectivity: Callable[[], bool] | None = None):
        if store.project_id != project.id or creds.project_id != project.id:
            raise ValueError("Store/credentials belong to a different project")
        self.project, self.store, self.creds, self.client = project, store, creds, client
        self.notifier = notifier
        self.confirm_delay_s = confirm_delay_s
        self.ssl_fetcher = ssl_fetcher
        self.wporg_client = wporg_client
        self.sleep = sleep
        self.connectivity = connectivity
        self.incidents = IncidentManager(store)

    # ------------------------------------------------------------------
    def quick_checks(self) -> list[CheckResult]:
        """Uptime + visible errors (+ health). Also used to verify changes."""
        p, c = self.project, self.client
        results: list[CheckResult] = []
        if p.enabled("uptime"):
            results += uptime_checks(p, c)
        if p.enabled("error_detection"):
            results += error_detection_checks(p, c)
        if p.enabled("health_endpoint"):
            results += health_endpoint_checks(p, c)
        return results

    def collect(self) -> list[CheckResult]:
        p, c = self.project, self.client
        results = self.quick_checks()
        if p.enabled("ssl"):
            results += ssl_checks(p, self.ssl_fetcher)
        if p.enabled("security"):
            results += security_checks(p, c)
        if p.enabled("wordpress"):
            results += WordPressInspector(p, c, self.creds, self.wporg_client).run()
        if p.enabled("seo"):
            results += seo_checks(p, c)
        # De-duplicate by name: a real result replaces an earlier NOT_CHECKED.
        merged: dict[str, CheckResult] = {}
        for r in results:
            prev = merged.get(r.name)
            if prev is None or prev.status is CheckStatus.NOT_CHECKED:
                merged[r.name] = r
        return list(merged.values())

    def _confirm(self, results: list[CheckResult]) -> list[CheckResult]:
        suspects = {r.name for r in results
                    if r.status is CheckStatus.FAIL and r.category in CONFIRM_CATEGORIES}
        if not suspects:
            return results
        self.sleep(self.confirm_delay_s)
        recheck = {r.name: r for r in self.quick_checks()}
        confirmed: list[CheckResult] = []
        for r in results:
            if r.name in suspects:
                again = recheck.get(r.name)
                if again is not None and again.status is not CheckStatus.FAIL:
                    confirmed.append(warn(r.name, r.category,
                                          f"Transient failure not confirmed on re-check: {r.summary}",
                                          detail_first=r.summary))
                    continue
            confirmed.append(r)
        return confirmed

    def run(self) -> RunSummary:
        run_id = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
        if not self.project.enabled("seo"):
            for inc in self.incidents.list():
                if inc["data"].get("category") == "seo":
                    self.incidents.set_status(inc["id"], IncidentStatus.CLOSED_FEATURE_DISABLED,
                                              "SEO monitoring was turned off for this project")
        if self.connectivity is not None and not self.connectivity():
            # The monitor itself is offline: do not report sites as down.
            result = not_checked("monitor:connectivity", "monitor",
                                 "Monitor has no internet connection; no checks were run. "
                                 "Sites were NOT reported down.")
            self.store.record_check(run_id, result)
            self.store.audit("monitor", "skipped_offline", run_id=run_id)
            return RunSummary(run_id, [result])
        results = self._confirm(self.collect())
        summary = RunSummary(run_id, results)
        for r in results:
            self.store.record_check(run_id, r)

        by_name = {r.name: r for r in results}
        for r in results:
            if r.status is CheckStatus.FAIL:
                inc, is_new = self.incidents.record_failure(r)
                if is_new:
                    summary.new_incidents.append(inc)
                    self._alert(summary, incident_alert(self.project, inc))
        for inc in self.incidents.list():
            if inc["status"] != IncidentStatus.OPEN.value:
                continue  # fixes in progress are closed only by the change executor
            current = by_name.get(inc["check_name"])
            if current is not None and current.status is CheckStatus.OK:
                closed = self.incidents.mark_recovered(
                    inc["id"], Verification([current], note="check passed again"))
                summary.recovered.append(closed)
                self._alert(summary, recovery_alert(self.project, closed))
        self.store.audit("monitor", "run_complete", run_id=run_id,
                         counts={s.value: sum(r.status is s for r in results) for s in CheckStatus})
        return summary

    def _alert(self, summary: RunSummary, text: str) -> None:
        if self.notifier and self.notifier.send(self.project, self.store, text):
            summary.alerts_sent += 1
        else:
            summary.alerts_not_sent += 1
            if not self.notifier:
                self.store.audit("notifier", "alert_not_sent", reason="no notifier", text=text)
