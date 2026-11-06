"""Incident lifecycle.

Rule enforced here: an incident can only become FIXED_VERIFIED through
`mark_fixed`, which requires a passing Verification with at least one check.
Nothing else (including any AI model) can set that status.
"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .checks.base import CheckResult, CheckStatus
from .storage import ProjectStore


class IncidentStatus(str, Enum):
    OPEN = "open"
    AWAITING_APPROVAL = "awaiting_approval"
    FIXING = "fixing"
    FIXED_VERIFIED = "fixed_verified"
    RECOVERED_VERIFIED = "recovered_verified"      # recovered on its own, confirmed by checks
    FIX_FAILED_ROLLED_BACK = "fix_failed_rolled_back"
    FIX_FAILED_ROLLBACK_FAILED = "fix_failed_rollback_failed"
    CANNOT_FIX = "cannot_fix"
    CLOSED_FEATURE_DISABLED = "closed_feature_disabled"  # e.g. SEO monitoring switched off


CLOSED = {IncidentStatus.FIXED_VERIFIED, IncidentStatus.RECOVERED_VERIFIED, IncidentStatus.CANNOT_FIX,
          IncidentStatus.CLOSED_FEATURE_DISABLED}


class IncidentError(Exception):
    pass


@dataclass
class Verification:
    checks: list[CheckResult]
    note: str = ""
    at: float = field(default_factory=time.time)

    @property
    def passed(self) -> bool:
        # Every verification check must be OK. NOT_CHECKED is NOT a pass.
        return bool(self.checks) and all(c.status is CheckStatus.OK for c in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {"at": self.at, "passed": self.passed, "note": self.note,
                "checks": [{"name": c.name, "status": c.status.value, "summary": c.summary}
                           for c in self.checks]}


class IncidentManager:
    def __init__(self, store: ProjectStore):
        self.store = store

    def _save(self, inc: dict[str, Any]) -> None:
        inc["updated"] = time.time()
        self.store.upsert("incidents", {
            "id": inc["id"], "opened": inc["opened"], "updated": inc["updated"],
            "check_name": inc["check_name"], "severity": inc["severity"], "status": inc["status"],
            "title": inc["title"], "data": json.dumps(inc["data"], default=str)})

    def _load(self, row: dict[str, Any]) -> dict[str, Any]:
        return row | {"data": json.loads(row["data"])}

    def get(self, incident_id: str) -> dict[str, Any]:
        rows = self.store.fetch("incidents", "id = ?", (incident_id,))
        if not rows:
            raise IncidentError(f"No incident {incident_id}")
        return self._load(rows[0])

    def open_for(self, check_name: str) -> dict[str, Any] | None:
        open_states = tuple(s.value for s in IncidentStatus if s not in CLOSED)
        marks = ",".join("?" for _ in open_states)
        rows = self.store.fetch("incidents", f"check_name = ? AND status IN ({marks})",
                                (check_name, *open_states))
        return self._load(rows[0]) if rows else None

    def list(self, include_closed: bool = False) -> list[dict[str, Any]]:
        rows = [self._load(r) for r in self.store.fetch("incidents")]
        if not include_closed:
            rows = [r for r in rows if IncidentStatus(r["status"]) not in CLOSED]
        return sorted(rows, key=lambda r: r["opened"])

    def record_failure(self, result: CheckResult) -> tuple[dict[str, Any], bool]:
        """Open a new incident or update the existing one. Returns (incident, is_new)."""
        existing = self.open_for(result.name)
        if existing:
            existing["data"]["last_seen"] = time.time()
            existing["data"]["occurrences"] = existing["data"].get("occurrences", 1) + 1
            self._save(existing)
            return existing, False
        inc = {"id": "INC-" + secrets.token_hex(3).upper(), "opened": time.time(),
               "check_name": result.name, "severity": result.severity.value,
               "status": IncidentStatus.OPEN.value, "title": result.summary,
               "data": {"evidence": result.detail, "category": result.category,
                        "occurrences": 1, "history": []}}
        self._history(inc, "opened", result.summary)
        self._save(inc)
        self.store.audit("monitor", "incident_opened", id=inc["id"], check=result.name)
        return inc, True

    def _history(self, inc: dict[str, Any], event: str, note: str) -> None:
        inc["data"].setdefault("history", []).append({"ts": time.time(), "event": event, "note": note})

    def set_status(self, incident_id: str, status: IncidentStatus, note: str = "") -> dict[str, Any]:
        if status in (IncidentStatus.FIXED_VERIFIED, IncidentStatus.RECOVERED_VERIFIED):
            raise IncidentError("Fixed/recovered status can only be set with a passing verification")
        inc = self.get(incident_id)
        inc["status"] = status.value
        self._history(inc, status.value, note)
        self._save(inc)
        self.store.audit("system", "incident_status", id=incident_id, status=status.value, note=note)
        return inc

    def mark_fixed(self, incident_id: str, verification: Verification) -> dict[str, Any]:
        if not verification.passed:
            raise IncidentError("Refusing to mark fixed: verification did not pass")
        inc = self.get(incident_id)
        inc["status"] = IncidentStatus.FIXED_VERIFIED.value
        inc["data"]["verification"] = verification.to_dict()
        self._history(inc, "fixed_verified", verification.note)
        self._save(inc)
        self.store.audit("verifier", "incident_fixed_verified", id=incident_id,
                         checks=[c.name for c in verification.checks])
        return inc

    def mark_recovered(self, incident_id: str, verification: Verification) -> dict[str, Any]:
        if not verification.passed:
            raise IncidentError("Refusing to mark recovered: verification did not pass")
        inc = self.get(incident_id)
        inc["status"] = IncidentStatus.RECOVERED_VERIFIED.value
        inc["data"]["verification"] = verification.to_dict()
        self._history(inc, "recovered_verified", verification.note)
        self._save(inc)
        self.store.audit("verifier", "incident_recovered_verified", id=incident_id)
        return inc
