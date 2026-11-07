"""The AI agent: answers questions with read-only Guardian tools."""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from ..approvals import ApprovalGate, ApprovalStatus
from ..incidents import IncidentManager
from ..models import ProjectConfig
from ..storage import ProjectStore
from .ollama import OllamaClient, OllamaError

THINK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)
MAX_TOOL_OUTPUT = 4000
MAX_ITEMS = 25


def _fn(name: str, description: str, properties: dict | None = None, required: list | None = None):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties or {}, "required": required or []}}}


PROJECT_ARG = {"project_id": {"type": "string", "description": "Project ID, e.g. connectdocsmedia"}}

TOOLS = [
    _fn("list_projects", "List all connected websites and apps with their type, address and whether SEO is on."),
    _fn("get_project_status", "Latest check results for one project: problems, warnings, OK items, items that "
        "could not be checked (with reasons), last check time, open incidents.", PROJECT_ARG, ["project_id"]),
    _fn("list_incidents", "Open incidents (problems still unresolved). Omit project_id for all projects.",
        PROJECT_ARG),
    _fn("get_seo_results", "Latest SEO check results for one project (only if SEO monitoring is on).",
        PROJECT_ARG, ["project_id"]),
    _fn("list_pending_approvals", "Changes waiting for the owner's approval. You cannot approve them."),
    _fn("start_check", "Start a new check of one project now. It only reads the website; results take "
        "about a minute.", PROJECT_ARG, ["project_id"]),
]
TOOL_NAMES = {t["function"]["name"] for t in TOOLS}

SYSTEM_PROMPT = """You are Guardian, the owner's website monitoring assistant.

Rules you must follow:
- Answer ONLY from tool results. If you have no data, call a tool or say you don't know.
- Never invent results. Never say something was fixed or is fine unless a tool result says so.
- Items marked "not_checked" were NOT checked: say so and give the reason.
- You cannot approve changes, change websites, or see passwords. For approvals, tell the owner to
  use the Approvals page in the dashboard.
- Keep answers short and clear (at most 6 sentences or a short list).
- {language_rule}

Connected projects: {projects}"""

LANGUAGE_RULES = {
    "ml": "Reply in Malayalam (Malayalam script). Keep technical terms such as SEO, SSL, plugin, "
          "URL, HTTP and project IDs in English.",
    "en": "Reply in English.",
}


@dataclass
class AgentReply:
    text: str
    tools_used: list[str] = field(default_factory=list)
    ok: bool = True


class Agent:
    def __init__(self, client: OllamaClient, projects: Callable[[], list[ProjectConfig]],
                 store_for: Callable[[str], ProjectStore], start_check: Callable[[str], bool]):
        self.client = client
        self.projects = projects
        self.store_for = store_for
        self.start_check = start_check
        self._busy = threading.Lock()

    # ------------------------------------------------------------- tools
    def _project(self, args: dict[str, Any]) -> ProjectConfig:
        pid = str(args.get("project_id", "")).strip()
        for p in self.projects():
            if p.id == pid:
                return p
        raise ValueError(f"Unknown project_id {pid!r}. Use list_projects to see valid IDs.")

    def _latest(self, project: ProjectConfig, category: str | None = None) -> dict[str, Any]:
        run_id, ts, results = self.store_for(project.id).latest_run()
        if not run_id:
            return {"project_id": project.id, "checked": False, "note": "Never checked yet."}
        if category == "seo":
            results = [r for r in results if r["category"] == "seo"]
        elif not project.enabled("seo"):
            results = [r for r in results if r["category"] != "seo"]
        grouped: dict[str, list[str]] = {"fail": [], "warn": [], "ok": [], "not_checked": []}
        for r in results:
            grouped.setdefault(r["status"], []).append(f"{r['name']}: {r['summary'][:200]}")
        return {"project_id": project.id, "name": project.name, "checked_at_unix": int(ts),
                "problems": grouped["fail"][:MAX_ITEMS], "warnings": grouped["warn"][:MAX_ITEMS],
                "ok_count": len(grouped["ok"]), "not_checked": grouped["not_checked"][:MAX_ITEMS]}

    def run_tool(self, name: str, args: dict[str, Any]) -> Any:
        if name not in TOOL_NAMES:
            raise ValueError(f"Tool {name!r} does not exist. Available: {sorted(TOOL_NAMES)}")
        if name == "list_projects":
            return [{"project_id": p.id, "name": p.name, "type": p.type.value, "url": p.url,
                     "seo": p.enabled("seo")} for p in self.projects()]
        if name == "get_project_status":
            p = self._project(args)
            data = self._latest(p)
            data["open_incidents"] = [f"{i['id']} [{i['severity']}] {i['title']}"
                                      for i in IncidentManager(self.store_for(p.id)).list()][:MAX_ITEMS]
            return data
        if name == "list_incidents":
            targets = [self._project(args)] if args.get("project_id") else self.projects()
            return [{"project_id": p.id, "incident": i["id"], "severity": i["severity"],
                     "status": i["status"], "title": i["title"]}
                    for p in targets for i in IncidentManager(self.store_for(p.id)).list()][:MAX_ITEMS]
        if name == "get_seo_results":
            p = self._project(args)
            if not p.enabled("seo"):
                return {"project_id": p.id, "seo": "SEO monitoring is turned off for this project."}
            return self._latest(p, "seo")
        if name == "list_pending_approvals":
            return [{"project_id": p.id, "approval": a["id"], "action": a["action"],
                     "reason": a["data"].get("reason"), "risk": a["data"].get("risk")}
                    for p in self.projects()
                    for a in ApprovalGate(self.store_for(p.id)).list(ApprovalStatus.PENDING)][:MAX_ITEMS]
        if name == "start_check":
            p = self._project(args)
            started = self.start_check(p.id)
            return {"project_id": p.id, "started": started,
                    "note": "Check started; results in about a minute." if started
                    else "A check is already running for this project."}
        raise AssertionError("unreachable")

    # -------------------------------------------------------------- loop
    def answer(self, question: str, lang: str = "en") -> AgentReply:
        if not self._busy.acquire(blocking=False):
            return AgentReply(BUSY[lang], ok=False)
        try:
            return self._answer(question, lang)
        finally:
            self._busy.release()

    def _answer(self, question: str, lang: str) -> AgentReply:
        projects = ", ".join(f"{p.id} ({p.name}, {p.type.value})" for p in self.projects()) or "none yet"
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT.format(
                language_rule=LANGUAGE_RULES.get(lang, LANGUAGE_RULES["en"]), projects=projects)},
            {"role": "user", "content": question[:1000]},
        ]
        used: list[str] = []
        for _ in range(self.client.cfg.max_tool_rounds + 1):
            try:
                resp = self.client.chat(messages, TOOLS)
            except OllamaError as exc:
                return AgentReply(UNAVAILABLE[lang].format(error=exc), used, ok=False)
            msg = resp.get("message") or {}
            calls = msg.get("tool_calls") or []
            if not calls:
                text = THINK_RE.sub("", msg.get("content") or "").strip()
                return AgentReply(text or EMPTY[lang], used, ok=bool(text))
            messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
            for call in calls[:5]:
                fn = call.get("function") or {}
                name = str(fn.get("name", ""))
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                try:
                    result: Any = self.run_tool(name, args if isinstance(args, dict) else {})
                    used.append(name)
                except ValueError as exc:
                    result = {"error": str(exc)}
                content = json.dumps(result, ensure_ascii=False, default=str)[:MAX_TOOL_OUTPUT]
                messages.append({"role": "tool", "tool_name": name, "content": content})
        return AgentReply(TOO_LONG[lang], used, ok=False)


BUSY = {"ml": "AI ഇപ്പോൾ മറ്റൊരു ചോദ്യത്തിന് ഉത്തരം തയ്യാറാക്കുകയാണ്. അൽപ്പസമയം കഴിഞ്ഞ് വീണ്ടും ചോദിക്കുക.",
        "en": "The AI is still answering another question. Please ask again in a moment."}
UNAVAILABLE = {"ml": "AI മോഡൽ ലഭ്യമല്ല ({error}). ലളിതമായ commands (റിപ്പോർട്ട്, പരിശോധിക്കൂ, പ്രശ്നങ്ങൾ, SEO, സഹായം) ഇപ്പോഴും പ്രവർത്തിക്കും.",
               "en": "The AI model is not available ({error}). Simple commands (report, check, problems, SEO, help) still work."}
EMPTY = {"ml": "AI ഒരു ഉത്തരവും നൽകിയില്ല. ചോദ്യം വേറൊരു രീതിയിൽ ചോദിക്കുക.",
         "en": "The AI gave no answer. Try asking in a different way."}
TOO_LONG = {"ml": "ഈ ചോദ്യത്തിന് ഉത്തരം കണ്ടെത്താൻ AI-ക്ക് കഴിഞ്ഞില്ല. കൂടുതൽ ലളിതമായി ചോദിക്കുക.",
            "en": "The AI couldn't work out an answer. Try a simpler question."}
