"""Telegram alerts. Each project can route to its own chat.

If Telegram is not configured, alerts are written to the project log and the
caller is told they were NOT sent (never silently dropped or faked).
"""

from __future__ import annotations

import httpx

from ..models import ProjectConfig
from ..storage import ProjectStore
from ..vault import ProjectCredentials

SEVERITY_ICON = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵"}


class Notifier:
    def __init__(self, system_creds: ProjectCredentials | None, client: httpx.Client | None = None):
        self.token = system_creds.get("telegram_bot_token") if system_creds else None
        self.default_chat = system_creds.get("telegram_default_chat_id") if system_creds else None
        self.client = client or httpx.Client(timeout=15)

    def send(self, project: ProjectConfig, store: ProjectStore, text: str) -> bool:
        chat = project.alert_chat_id or self.default_chat
        if not self.token or not chat:
            store.audit("notifier", "alert_not_sent", reason="Telegram not configured", text=text)
            return False
        try:
            resp = self.client.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                                    json={"chat_id": chat, "text": text[:4000]})
            sent = resp.status_code == 200 and resp.json().get("ok") is True
        except (httpx.HTTPError, ValueError) as exc:
            store.audit("notifier", "alert_send_error", error=str(exc))
            return False
        store.audit("notifier", "alert_sent" if sent else "alert_send_failed", chat=chat)
        return sent


def incident_alert(project: ProjectConfig, incident: dict) -> str:
    icon = SEVERITY_ICON.get(incident["severity"], "⚪")
    lines = [f"{icon} {incident['severity'].upper()}: {project.name}",
             f"{incident['title']}",
             f"Incident: {incident['id']} · check: {incident['check_name']}"]
    if "ml" in project.language:
        lines.insert(1, "പ്രശ്നം കണ്ടെത്തി (Problem detected)")
    return "\n".join(lines)


def recovery_alert(project: ProjectConfig, incident: dict) -> str:
    text = (f"✅ RECOVERED (verified): {project.name}\n{incident['title']}\n"
            f"Incident {incident['id']} closed: check {incident['check_name']} passed again.")
    if "ml" in project.language:
        text = "✅ പ്രശ്നം പരിഹരിക്കപ്പെട്ടു (പരിശോധിച്ച് ഉറപ്പാക്കി)\n" + text
    return text
