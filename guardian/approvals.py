"""Approval gate for changes.

* An approval request describes one exact action; its SHA-256 digest is stored.
* Only the owner can approve (CLI or, later, Telegram button).
* An approval is single-use, expires, and only unlocks the exact action whose
  digest it was issued for. Any change to the action invalidates it.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from enum import Enum
from typing import Any

from .storage import ProjectStore

DEFAULT_TTL_S = 24 * 3600


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    USED = "used"
    EXPIRED = "expired"


class ApprovalError(Exception):
    pass


def action_digest(action: dict[str, Any]) -> str:
    canonical = json.dumps(action, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


class ApprovalGate:
    def __init__(self, store: ProjectStore):
        self.store = store

    def _row(self, approval_id: str) -> dict[str, Any]:
        rows = self.store.fetch("approvals", "id = ?", (approval_id,))
        if not rows:
            raise ApprovalError(f"No approval {approval_id}")
        row = rows[0] | {"data": json.loads(rows[0]["data"])}
        if row["status"] in (ApprovalStatus.PENDING.value, ApprovalStatus.APPROVED.value) \
                and time.time() > row["expires"]:
            row["status"] = ApprovalStatus.EXPIRED.value
            self._save(row)
        return row

    def _save(self, row: dict[str, Any]) -> None:
        self.store.upsert("approvals", row | {"data": json.dumps(row["data"], default=str)})

    def request(self, action: dict[str, Any], reason: str, risk: str,
                incident_id: str | None = None, ttl_s: float = DEFAULT_TTL_S) -> dict[str, Any]:
        if action.get("project") != self.store.project_id:
            raise ApprovalError("Action targets a different project than this approval gate")
        row = {"id": "APR-" + secrets.token_hex(4).upper(), "created": time.time(),
               "expires": time.time() + ttl_s, "status": ApprovalStatus.PENDING.value,
               "action": action.get("type", "unknown"), "digest": action_digest(action),
               "data": {"action": action, "reason": reason, "risk": risk, "incident": incident_id}}
        self._save(row)
        self.store.audit("agent", "approval_requested", id=row["id"], action=action, reason=reason)
        return row

    def decide(self, approval_id: str, approve: bool, actor: str = "owner") -> dict[str, Any]:
        row = self._row(approval_id)
        if row["status"] != ApprovalStatus.PENDING.value:
            raise ApprovalError(f"Approval {approval_id} is {row['status']}, not pending")
        row["status"] = (ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED).value
        row["data"]["decided_by"] = actor
        row["data"]["decided_at"] = time.time()
        self._save(row)
        self.store.audit(actor, "approval_" + row["status"], id=approval_id)
        return row

    def consume(self, approval_id: str, action: dict[str, Any]) -> dict[str, Any]:
        """Use an approval to unlock exactly this action. Single use."""
        row = self._row(approval_id)
        if row["status"] != ApprovalStatus.APPROVED.value:
            raise ApprovalError(f"Approval {approval_id} is {row['status']}; change refused")
        if action_digest(action) != row["digest"]:
            raise ApprovalError("Action differs from what was approved; change refused")
        row["status"] = ApprovalStatus.USED.value
        row["data"]["used_at"] = time.time()
        self._save(row)
        self.store.audit("executor", "approval_used", id=approval_id)
        return row

    def record_result(self, approval_id: str, execution: dict[str, Any]) -> dict[str, Any]:
        """Store what happened when an approved change was executed."""
        row = self._row(approval_id)
        row["data"]["execution"] = execution
        self._save(row)
        self.store.audit("executor", "execution_recorded", id=approval_id,
                         state=execution.get("state"), outcome=execution.get("outcome"))
        return row

    def list(self, status: ApprovalStatus | None = None) -> list[dict[str, Any]]:
        rows = [self._row(r["id"]) for r in self.store.fetch("approvals")]
        if status:
            rows = [r for r in rows if r["status"] == status.value]
        return sorted(rows, key=lambda r: r["created"])
