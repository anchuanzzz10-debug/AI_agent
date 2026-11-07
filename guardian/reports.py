"""Per-project reports. SEO results appear only when SEO is enabled, and every
report lists what was NOT checked and why."""

from __future__ import annotations

import time

from .checks.base import CheckResult, CheckStatus
from .incidents import IncidentManager
from .models import ProjectConfig
from .storage import ProjectStore

LABELS = {
    "summary": ("സംഗ്രഹം", "Summary"),
    "fail": ("പ്രശ്നങ്ങൾ", "Problems"),
    "warn": ("മുന്നറിയിപ്പുകൾ", "Warnings"),
    "ok": ("ശരിയായി പ്രവർത്തിക്കുന്നു", "Working correctly"),
    "not_checked": ("പരിശോധിക്കാൻ കഴിഞ്ഞില്ല", "Not checked (and why)"),
    "incidents": ("തുറന്ന പ്രശ്നങ്ങൾ", "Open incidents"),
    "seo": ("SEO റിപ്പോർട്ട്", "SEO report"),
}


def _label(key: str, language: str) -> str:
    ml, en = LABELS[key]
    if language == "ml":
        return ml
    if language == "en":
        return en
    return f"{ml} / {en}"


def _rows(results: list[CheckResult], status: CheckStatus) -> list[str]:
    return [f"- **{r.name}** ({r.severity.value}): {r.summary}" if status in (CheckStatus.FAIL, CheckStatus.WARN)
            else f"- {r.name}: {r.summary}" for r in results if r.status is status]


def build_report(project: ProjectConfig, store: ProjectStore, results: list[CheckResult],
                 run_id: str) -> str:
    lang = project.language
    seo_on = project.enabled("seo")
    if not seo_on:
        results = [r for r in results if r.category != "seo"]
    general = [r for r in results if r.category != "seo"]
    seo = [r for r in results if r.category == "seo"]
    count = {s: sum(r.status is s for r in general) for s in CheckStatus}
    seo_count = {s: sum(r.status is s for r in seo) for s in CheckStatus}

    def counts_line(c, prefix=""):
        return (f"{prefix}Problems {c[CheckStatus.FAIL]} · Warnings {c[CheckStatus.WARN]} · "
                f"OK {c[CheckStatus.OK]} · Not checked {c[CheckStatus.NOT_CHECKED]}")

    out = [f"# {project.name} ({project.id})",
           f"Run {run_id} · {time.strftime('%Y-%m-%d %H:%M')} · {project.url}",
           "",
           f"## {_label('summary', lang)}",
           counts_line(count)]
    if seo_on:
        # SEO findings are counted separately so no "not checked" item is hidden.
        out.append(counts_line(seo_count, "SEO: "))
    out.append(f"Enabled features: {', '.join(k for k, v in project.features.items() if v)}")
    for key, status in (("fail", CheckStatus.FAIL), ("warn", CheckStatus.WARN),
                        ("ok", CheckStatus.OK), ("not_checked", CheckStatus.NOT_CHECKED)):
        rows = _rows(general, status)
        if rows:
            out += ["", f"## {_label(key, lang)}", *rows]

    open_incidents = IncidentManager(store).list()
    if open_incidents:
        out += ["", f"## {_label('incidents', lang)}"]
        out += [f"- {i['id']} [{i['severity']}] {i['status']}: {i['title']}" for i in open_incidents]

    if seo_on:
        out += ["", f"## {_label('seo', lang)}"]
        for key, status in (("fail", CheckStatus.FAIL), ("warn", CheckStatus.WARN),
                            ("ok", CheckStatus.OK), ("not_checked", CheckStatus.NOT_CHECKED)):
            rows = _rows(seo, status)
            if rows:
                out += [f"### {_label(key, lang)}", *rows]
    text = "\n".join(out) + "\n"
    path = store.path("reports", f"report-{run_id}.md")
    path.write_text(text, encoding="utf-8")
    return text
