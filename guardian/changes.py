"""Approval-gated change execution: approve → backup → apply → verify → (rollback).

The executor refuses to run without a consumed approval and refuses to apply
without a successful backup. Success is only reported when verification
passes; otherwise the change is rolled back and the rollback is verified too.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

import httpx

from .approvals import ApprovalGate
from .checks.base import CheckResult, CheckStatus, fail, ok
from .incidents import IncidentManager, IncidentStatus, Verification
from .storage import ProjectStore


class ChangeError(Exception):
    pass


@dataclass
class Backup:
    ok: bool
    kind: str                 # e.g. "item snapshot", "full database + files"
    location: str | None
    detail: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


class Outcome(str, Enum):
    APPLIED_VERIFIED = "applied_verified"
    REFUSED_NO_BACKUP = "refused_no_backup"
    FAILED_ROLLED_BACK = "failed_rolled_back"
    FAILED_ROLLBACK_FAILED = "failed_rollback_failed"


@dataclass
class ChangeResult:
    outcome: Outcome
    backup: Backup | None
    verification: Verification | None = None
    rollback_verification: Verification | None = None
    error: str | None = None


class ChangeAction:
    """Base class. `spec()` is exactly what the owner approves."""

    def spec(self) -> dict[str, Any]:
        raise NotImplementedError

    def describe(self) -> str:
        raise NotImplementedError

    def backup(self, store: ProjectStore) -> Backup:
        raise NotImplementedError

    def apply(self) -> None:
        raise NotImplementedError

    def verify(self) -> list[CheckResult]:
        raise NotImplementedError

    def rollback(self, backup: Backup) -> None:
        raise NotImplementedError


SiteChecks = Callable[[], list[CheckResult]]


class ChangeExecutor:
    def __init__(self, store: ProjectStore, site_checks: SiteChecks):
        self.store = store
        self.gate = ApprovalGate(store)
        self.incidents = IncidentManager(store)
        self.site_checks = site_checks

    def _verify(self, action_checks: list[CheckResult], note: str) -> Verification:
        site = [c for c in self.site_checks() if c.status is not CheckStatus.NOT_CHECKED]
        return Verification(action_checks + site, note=note)

    def execute(self, action: ChangeAction, approval_id: str,
                incident_id: str | None = None) -> ChangeResult:
        spec = action.spec()
        if spec.get("project") != self.store.project_id:
            raise ChangeError("Action belongs to a different project")
        self.gate.consume(approval_id, spec)  # raises if not approved / different action
        if incident_id:
            self.incidents.set_status(incident_id, IncidentStatus.FIXING, action.describe())

        backup = action.backup(self.store)
        self.store.audit("executor", "backup", ok=backup.ok, kind=backup.kind,
                         location=backup.location, reason=backup.reason)
        if not backup.ok:
            if incident_id:
                self.incidents.set_status(incident_id, IncidentStatus.OPEN,
                                          f"Change refused: backup failed ({backup.reason})")
            return ChangeResult(Outcome.REFUSED_NO_BACKUP, backup, error=backup.reason)

        error = None
        try:
            action.apply()
            self.store.audit("executor", "applied", action=spec)
            verification = self._verify(action.verify(), "post-change verification")
        except Exception as exc:  # noqa: BLE001 - any failure must trigger rollback
            error = f"{type(exc).__name__}: {exc}"
            self.store.audit("executor", "apply_error", error=error)
            verification = Verification([fail("change:apply", "change", f"Apply failed: {error}")],
                                        note="apply raised an error")

        if verification.passed:
            if incident_id:
                self.incidents.mark_fixed(incident_id, verification)
            self.store.audit("verifier", "change_verified", action=spec)
            return ChangeResult(Outcome.APPLIED_VERIFIED, backup, verification)

        # Verification failed: roll back and verify the rollback.
        try:
            action.rollback(backup)
            rb_checks = [ok("change:rollback", "change", "Rollback applied")]
        except Exception as exc:  # noqa: BLE001
            rb_checks = [fail("change:rollback", "change", f"Rollback failed: {exc}")]
        rollback_verification = self._verify(rb_checks, "post-rollback verification")
        if rollback_verification.passed:
            outcome, status = Outcome.FAILED_ROLLED_BACK, IncidentStatus.FIX_FAILED_ROLLED_BACK
        else:
            outcome, status = Outcome.FAILED_ROLLBACK_FAILED, IncidentStatus.FIX_FAILED_ROLLBACK_FAILED
        self.store.audit("verifier", outcome.value, action=spec)
        if incident_id:
            self.incidents.set_status(incident_id, status, "Verification failed after change")
        return ChangeResult(outcome, backup, verification, rollback_verification, error)


# ---------------------------------------------------------------------------
# WordPress actions over REST, using the separate CHANGE credential.
# Backups here are snapshots of the exact item being changed. Full database /
# file backups require the Guardian Connector or SSH (not implemented yet), so
# actions that need them (plugin/theme/core updates) are not offered.
# ---------------------------------------------------------------------------

class _WPAction(ChangeAction):
    def __init__(self, project_id: str, base_url: str, client: httpx.Client,
                 auth: tuple[str, str] | None):
        if not auth:
            raise ChangeError("No change credential stored (wp_change_user / wp_change_app_password)")
        self.project_id = project_id
        self.api = base_url.rstrip("/") + "/wp-json"
        self.client = client
        self.auth = auth

    def _req(self, method: str, route: str, **kw) -> httpx.Response:
        resp = self.client.request(method, self.api + route, auth=self.auth, **kw)
        if resp.status_code >= 400:
            raise ChangeError(f"{method} {route} → HTTP {resp.status_code}: {resp.text[:200]}")
        return resp

    def _write_backup(self, store: ProjectStore, name: str, payload: dict[str, Any]) -> str:
        path = store.path("backups", f"{int(time.time())}-{name}.json")
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return str(path)


class UpdatePostFields(_WPAction):
    EDITABLE = {"title", "content", "excerpt", "status", "slug"}

    def __init__(self, project_id, base_url, client, auth, post_type: str, post_id: int,
                 fields: dict[str, Any]):
        super().__init__(project_id, base_url, client, auth)
        if post_type not in {"posts", "pages"}:
            raise ChangeError("post_type must be 'posts' or 'pages'")
        bad = set(fields) - self.EDITABLE
        if bad:
            raise ChangeError(f"Fields not allowed: {sorted(bad)}")
        self.post_type, self.post_id, self.fields = post_type, int(post_id), dict(fields)

    def spec(self):
        return {"project": self.project_id, "type": "wp.update_post", "post_type": self.post_type,
                "post_id": self.post_id, "fields": self.fields}

    def describe(self):
        return f"Update {self.post_type[:-1]} #{self.post_id}: {', '.join(self.fields)}"

    def _current(self) -> dict[str, Any]:
        data = self._req("GET", f"/wp/v2/{self.post_type}/{self.post_id}", params={"context": "edit"}).json()
        out = {}
        for key in self.fields:
            value = data.get(key)
            out[key] = value.get("raw") if isinstance(value, dict) else value
        return out

    def backup(self, store):
        try:
            previous = self._current()
        except (ChangeError, httpx.HTTPError) as exc:
            return Backup(False, "item snapshot", None, reason=str(exc))
        location = self._write_backup(store, f"{self.post_type}-{self.post_id}", previous)
        return Backup(True, "item snapshot (only the fields being changed)", location, {"previous": previous})

    def apply(self):
        self._req("POST", f"/wp/v2/{self.post_type}/{self.post_id}", json=self.fields)

    def verify(self):
        now = self._current()
        mismatched = [k for k, v in self.fields.items() if now.get(k) != v]
        if mismatched:
            return [fail("change:fields", "change", f"Fields not saved as requested: {mismatched}")]
        return [ok("change:fields", "change", "All requested fields saved")]

    def rollback(self, backup):
        self._req("POST", f"/wp/v2/{self.post_type}/{self.post_id}", json=backup.detail["previous"])


class SetPluginStatus(_WPAction):
    def __init__(self, project_id, base_url, client, auth, plugin: str, status: str):
        super().__init__(project_id, base_url, client, auth)
        if status not in {"active", "inactive"}:
            raise ChangeError("status must be 'active' or 'inactive'")
        self.plugin = plugin.removesuffix(".php")
        self.status = status

    def spec(self):
        return {"project": self.project_id, "type": "wp.plugin_status", "plugin": self.plugin,
                "status": self.status}

    def describe(self):
        return f"Set plugin {self.plugin} to {self.status}"

    def _status(self) -> str:
        return self._req("GET", f"/wp/v2/plugins/{self.plugin}").json().get("status")

    def backup(self, store):
        try:
            previous = self._status()
        except (ChangeError, httpx.HTTPError) as exc:
            return Backup(False, "plugin state snapshot", None, reason=str(exc))
        location = self._write_backup(store, "plugin-" + self.plugin.replace("/", "_"),
                                      {"plugin": self.plugin, "status": previous})
        return Backup(True, "plugin state snapshot (activation status only)", location,
                      {"previous": previous})

    def apply(self):
        self._req("POST", f"/wp/v2/plugins/{self.plugin}", json={"status": self.status})

    def verify(self):
        now = self._status()
        if now != self.status:
            return [fail("change:plugin_status", "change", f"Plugin status is {now}, expected {self.status}")]
        return [ok("change:plugin_status", "change", f"Plugin is {now}")]

    def rollback(self, backup):
        self._req("POST", f"/wp/v2/plugins/{self.plugin}", json={"status": backup.detail["previous"]})
