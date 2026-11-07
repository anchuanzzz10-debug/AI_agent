"""Dashboard chat: Malayalam / Manglish / English.

This phase uses the deterministic command dictionary (no AI model). Answers
come only from real stored results. Approvals are never given through chat:
the user is sent to the Approvals page, where an explicit button is required.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable
from urllib.parse import urlparse

from ..incidents import IncidentManager
from ..language import parse_command
from ..language.detect import normalize
from ..models import ProjectConfig
from ..storage import ProjectStore

if TYPE_CHECKING:
    from ..ai.agent import Agent


@dataclass
class ChatContext:
    projects: list[ProjectConfig]
    store_for: Callable[[str], ProjectStore]
    start_check: Callable[[str], bool]      # returns False if already running
    ai: Agent | None = None                  # local AI model for free-form questions


def _reply_lang(detected: str) -> str:
    return "ml" if detected in ("ml", "manglish") else "en"


def _match_project(text: str, projects: list[ProjectConfig], parsed: str | None) -> ProjectConfig | None:
    if parsed:
        return next((p for p in projects if p.id == parsed), None)
    norm = normalize(text)
    for p in projects:
        host = (urlparse(p.url).hostname or "").removeprefix("www.")
        aliases = {p.id, host, host.split(".")[0], normalize(p.name.split("(")[0])}
        if any(a and a in norm for a in aliases):
            return p
    return projects[0] if len(projects) == 1 else None


def _counts(results: list[dict]) -> dict[str, int]:
    out = {"fail": 0, "warn": 0, "ok": 0, "not_checked": 0}
    for r in results:
        out[r["status"]] = out.get(r["status"], 0) + 1
    return out


def _status_line(p: ProjectConfig, store: ProjectStore, lang: str) -> str:
    run_id, _, results = store.latest_run()
    if not run_id:
        return (f"{p.name}: ഇതുവരെ പരിശോധിച്ചിട്ടില്ല." if lang == "ml"
                else f"{p.name}: not checked yet.")
    results = [r for r in results if p.enabled("seo") or r["category"] != "seo"]
    c = _counts(results)
    open_inc = len(IncidentManager(store).list())
    if lang == "ml":
        return (f"{p.name}: പ്രശ്നങ്ങൾ {c['fail']} · മുന്നറിയിപ്പുകൾ {c['warn']} · ശരി {c['ok']} · "
                f"പരിശോധിക്കാൻ കഴിഞ്ഞില്ല {c['not_checked']} · തുറന്ന പ്രശ്നങ്ങൾ {open_inc}")
    return (f"{p.name}: problems {c['fail']} · warnings {c['warn']} · ok {c['ok']} · "
            f"not checked {c['not_checked']} · open incidents {open_inc}")


HELP = {
    "ml": ("എനിക്ക് ഇപ്പോൾ ചെയ്യാൻ കഴിയുന്നത്:\n"
           "• \"റിപ്പോർട്ട് തരൂ\" / \"report tharu\": എല്ലാ പ്രോജക്റ്റുകളുടെയും നില\n"
           "• \"connectdocsmedia പരിശോധിക്കൂ\" / \"check cheyyu\": ഉടൻ പരിശോധന\n"
           "• \"പ്രശ്നങ്ങൾ ഉണ്ടോ\" / \"prashnangal undo\": തുറന്ന പ്രശ്നങ്ങൾ\n"
           "• \"SEO റിപ്പോർട്ട്\": SEO ഫലങ്ങൾ\n"
           "അനുമതി നൽകാൻ \"അനുമതികൾ\" പേജ് ഉപയോഗിക്കുക. സ്വതന്ത്രമായ ചോദ്യങ്ങൾക്കുള്ള AI മറുപടി അടുത്ത ഘട്ടത്തിൽ വരും."),
    "en": ("What I can do now:\n"
           "• \"report\": status of all projects\n"
           "• \"check connectdocsmedia\": run a check now\n"
           "• \"any problems?\": open incidents\n"
           "• \"seo report\": SEO results\n"
           "Use the Approvals page to approve changes. Free-form AI answers come in the next phase."),
}


def respond(message: str, ctx: ChatContext) -> dict:
    message = (message or "").strip()[:500]
    if not message:
        return {"reply": "", "lang": "en", "intent": "empty"}
    projects_map = {p.id: p.name for p in ctx.projects}
    intent = parse_command(message, projects_map)
    lang = _reply_lang(intent.language)
    project = _match_project(message, ctx.projects, intent.project)
    if intent.name == "unknown" and ctx.ai is not None:
        answer = ctx.ai.answer(message, lang)
        return {"reply": answer.text, "lang": lang, "intent": "ai", "source": "ai",
                "tools": answer.tools_used, "ai_ok": answer.ok, "project": None}
    reply = _dispatch(intent.name, project, ctx, lang)
    return {"reply": reply, "lang": lang, "intent": intent.name, "source": "commands",
            "project": project.id if project else None}


def _dispatch(name: str, project: ProjectConfig | None, ctx: ChatContext, lang: str) -> str:
    ml = lang == "ml"
    if not ctx.projects and name not in ("help", "unknown"):
        return "ഇതുവരെ പ്രോജക്റ്റുകൾ ഇല്ല. ആദ്യം ഒന്ന് ചേർക്കുക." if ml else "No projects yet. Add one first."
    if name == "help":
        return HELP[lang]
    if name == "status_report":
        targets = [project] if project and len(ctx.projects) > 1 else ctx.projects
        return "\n".join(_status_line(p, ctx.store_for(p.id), lang) for p in targets)
    if name == "run_check":
        if project is None:
            return ("ഏത് പ്രോജക്റ്റ് ആണ് പരിശോധിക്കേണ്ടത്? പേരോ ID-യോ ചേർത്ത് വീണ്ടും എഴുതുക."
                    if ml else "Which project? Include its name or ID.")
        started = ctx.start_check(project.id)
        if not started:
            return (f"{project.name}: പരിശോധന ഇപ്പോൾ നടന്നുകൊണ്ടിരിക്കുന്നു." if ml
                    else f"{project.name}: a check is already running.")
        return (f"{project.name}: പരിശോധന തുടങ്ങി. ഏകദേശം ഒരു മിനിറ്റ് കഴിഞ്ഞ് \"റിപ്പോർട്ട്\" എന്ന് ചോദിക്കുക."
                if ml else f"{project.name}: check started. Ask for the \"report\" in about a minute.")
    if name == "list_incidents":
        lines = []
        for p in ([project] if project and len(ctx.projects) > 1 else ctx.projects):
            for inc in IncidentManager(ctx.store_for(p.id)).list():
                lines.append(f"• {p.name} · {inc['id']} [{inc['severity']}] {inc['title']}")
        if not lines:
            return "തുറന്ന പ്രശ്നങ്ങൾ ഒന്നുമില്ല. ✅" if ml else "No open incidents. ✅"
        return ("തുറന്ന പ്രശ്നങ്ങൾ:\n" if ml else "Open incidents:\n") + "\n".join(lines)
    if name == "seo_report":
        p = project if project else (ctx.projects[0] if len(ctx.projects) == 1 else None)
        if p is None:
            return "ഏത് പ്രോജക്റ്റിന്റെ SEO?" if ml else "SEO for which project?"
        if not p.enabled("seo"):
            return (f"{p.name}: SEO നിരീക്ഷണം ഓഫ് ആണ്. പ്രോജക്റ്റ് ക്രമീകരണങ്ങളിൽ ഓൺ ആക്കാം."
                    if ml else f"{p.name}: SEO monitoring is off. Turn it on in the project settings.")
        _, _, results = ctx.store_for(p.id).latest_run()
        seo = [r for r in results if r["category"] == "seo"]
        if not seo:
            return f"{p.name}: SEO ഫലങ്ങൾ ഇല്ല. ആദ്യം പരിശോധിക്കുക." if ml else f"{p.name}: no SEO results yet. Run a check first."
        icon = {"ok": "✅", "warn": "⚠️", "fail": "❌", "not_checked": "⏸️"}
        return f"{p.name} · SEO\n" + "\n".join(f"{icon.get(r['status'], '•')} {r['summary']}" for r in seo)
    if name in ("approve", "reject"):
        return ("സുരക്ഷയ്ക്കായി ചാറ്റ് വഴി അനുമതി നൽകാനോ നിരസിക്കാനോ കഴിയില്ല. \"അനുമതികൾ\" പേജിലെ ബട്ടൺ ഉപയോഗിക്കുക."
                if ml else "For safety, approvals can't be given in chat. Use the button on the Approvals page.")
    if name == "backup":
        return ("പൂർണ്ണ ബാക്കപ്പ് സൗകര്യം ഇതുവരെ നിർമ്മിച്ചിട്ടില്ല. ഓരോ മാറ്റത്തിനും മുൻപ് മാറ്റുന്ന ഭാഗത്തിന്റെ ബാക്കപ്പ് മാത്രമേ എടുക്കുന്നുള്ളൂ."
                if ml else "Full backups are not built yet. Only the item being changed is backed up before each change.")
    return ("ക്ഷമിക്കണം, അത് എനിക്ക് മനസ്സിലായില്ല. \"സഹായം\" എന്ന് എഴുതിയാൽ ഉദാഹരണങ്ങൾ കാണാം. സ്വതന്ത്രമായ ചോദ്യങ്ങൾക്ക് AI മോഡൽ ഓൺ ആക്കുക (README: AI brain)."
            if ml else "Sorry, I didn't understand that. Type \"help\" for examples. For free-form questions, turn on the AI model (README: AI brain).")
