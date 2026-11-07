"""Change proposals: turn an owner's request into an exact, approvable action,
and run approved actions through the change engine.

Flow: propose (validated spec) → approval request (pending) → owner approves →
execute: consume approval → backup → apply → verify → rollback if needed →
result recorded on the approval (and linked incident closed only if verified).
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable

import httpx

from .approvals import ApprovalGate
from .changes import ChangeAction, ChangeError, ChangeExecutor, SetPluginStatus, UpdatePostFields
from .checks.wordpress import change_auth
from .models import ProjectConfig, ProjectType
from .monitor import Monitor
from .storage import ProjectStore
from .vault import ProjectCredentials

PLUGIN_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,80}(/[a-z0-9][a-z0-9._-]{0,80})?$")
POST_FIELDS = {"title", "excerpt", "status"}          # deliberately small, reviewable set
POST_STATUSES = {"publish", "draft", "pending", "private"}


class ProposalError(ValueError):
    pass


def plugin_spec(project: ProjectConfig, plugin: str, status: str) -> dict[str, Any]:
    plugin = plugin.strip().removesuffix(".php")
    if not PLUGIN_RE.match(plugin):
        raise ProposalError("Plugin must look like 'folder/file' (e.g. akismet/akismet)")
    if status not in ("active", "inactive"):
        raise ProposalError("Status must be active or inactive")
    return {"project": project.id, "type": "wp.plugin_status", "plugin": plugin, "status": status}


def post_spec(project: ProjectConfig, post_type: str, post_id: str | int, field: str,
              value: str) -> dict[str, Any]:
    if post_type not in ("posts", "pages"):
        raise ProposalError("Choose page or post")
    try:
        pid = int(post_id)
    except (TypeError, ValueError) as exc:
        raise ProposalError("Page/post ID must be a number") from exc
    if pid <= 0:
        raise ProposalError("Page/post ID must be a positive number")
    if field not in POST_FIELDS:
        raise ProposalError(f"Field must be one of {sorted(POST_FIELDS)}")
    value = value.strip()
    if not value or len(value) > 2000:
        raise ProposalError("New value must be 1-2000 characters")
    if field == "status" and value not in POST_STATUSES:
        raise ProposalError(f"Status must be one of {sorted(POST_STATUSES)}")
    return {"project": project.id, "type": "wp.update_post", "post_type": post_type, "post_id": pid,
            "fields": {field: value}}


def risk_of(spec: dict[str, Any]) -> str:
    if spec["type"] == "wp.plugin_status":
        return "medium"
    if "status" in spec.get("fields", {}):
        return "medium"          # can hide or publish a page
    return "low"


def describe(spec: dict[str, Any], lang: str = "en") -> str:
    if spec.get("type") == "wp.plugin_status":
        on = spec["status"] == "active"
        if lang == "ml":
            return f"പ്ലഗിൻ {spec['plugin']} " + ("ഓൺ ആക്കുക (activate)" if on else "ഓഫ് ആക്കുക (deactivate)")
        return f"{'Activate' if on else 'Deactivate'} plugin {spec['plugin']}"
    if spec.get("type") == "wp.update_post":
        kind = {"posts": ("പോസ്റ്റ്", "post"), "pages": ("പേജ്", "page")}[spec["post_type"]]
        (field, value), = spec["fields"].items()
        if lang == "ml":
            return f"{kind[0]} #{spec['post_id']}: {field} → \"{value}\""
        return f"Set {kind[1]} #{spec['post_id']} {field} to \"{value}\""
    return str(spec.get("type"))


def propose(project: ProjectConfig, store: ProjectStore, creds: ProjectCredentials,
            spec: dict[str, Any], reason: str, incident_id: str | None = None) -> dict[str, Any]:
    if project.type is not ProjectType.WORDPRESS:
        raise ProposalError("Changes can only be proposed for WordPress sites")
    if spec.get("project") != project.id:
        raise ProposalError("Change belongs to a different project")
    if not change_auth(creds):
        raise ProposalError("No change login saved for this site (WordPress connection → "
                            "Allow approved changes)")
    reason = reason.strip()[:500] or "-"
    return ApprovalGate(store).request(spec, reason=reason, risk=risk_of(spec), incident_id=incident_id)


def build_action(spec: dict[str, Any], project: ProjectConfig, client: httpx.Client,
                 creds: ProjectCredentials) -> ChangeAction:
    auth = change_auth(creds)
    if spec.get("type") == "wp.plugin_status":
        return SetPluginStatus(project.id, project.url, client, auth, spec["plugin"], spec["status"])
    if spec.get("type") == "wp.update_post":
        return UpdatePostFields(project.id, project.url, client, auth, spec["post_type"],
                                spec["post_id"], spec["fields"])
    raise ChangeError(f"Unknown change type {spec.get('type')!r}")


def execute_approved(approval_id: str, project: ProjectConfig, store: ProjectStore,
                     creds: ProjectCredentials, client_factory: Callable[[], httpx.Client]) -> dict[str, Any]:
    """Run one approved change end to end and record the result on the approval."""
    gate = ApprovalGate(store)
    row = next((r for r in gate.list() if r["id"] == approval_id), None)
    if row is None:
        raise ProposalError(f"No approval {approval_id}")
    spec = row["data"]["action"]
    incident = row["data"].get("incident")
    gate.record_result(approval_id, {"state": "running", "started": time.time()})
    execution: dict[str, Any] = {"state": "done", "started": time.time()}
    try:
        with client_factory() as client:
            action = build_action(spec, project, client, creds)
            monitor = Monitor(project, store, creds, client, notifier=None, confirm_delay_s=0)
            result = ChangeExecutor(store, monitor.quick_checks).execute(action, approval_id, incident)
        execution.update(
            outcome=result.outcome.value,
            error=result.error,
            backup={"ok": result.backup.ok, "kind": result.backup.kind,
                    "location": result.backup.location, "reason": result.backup.reason}
            if result.backup else None,
            verification=result.verification.to_dict() if result.verification else None,
            rollback_verification=result.rollback_verification.to_dict()
            if result.rollback_verification else None)
    except Exception as exc:  # noqa: BLE001 - always record what happened
        execution.update(outcome="error", error=f"{type(exc).__name__}: {exc}")
    execution["finished"] = time.time()
    gate.record_result(approval_id, execution)
    return execution
