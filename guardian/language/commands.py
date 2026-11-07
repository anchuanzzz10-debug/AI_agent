"""Deterministic command dictionary for Malayalam, Manglish and English.

Frequent commands are matched here without any AI model, so they work the same
way every time. Anything not matched is returned as intent 'unknown' (later
handed to the local LLM, which is a separate, not-yet-built component).

Safety: 'approve' is only actionable when an approval id (APR-XXXXXXXX) is
present; voice-originated approvals must still be confirmed by the caller.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from .detect import detect_language, normalize

INTENTS: dict[str, dict[str, list[str]]] = {
    "status_report": {
        "ml": ["റിപ്പോർട്ട്", "സ്ഥിതി", "എങ്ങനെയുണ്ട്", "സ്റ്റാറ്റസ്", "സമ്മറി"],
        "manglish": ["report", "status", "sthithi", "engane und", "enganeyund"],
        "en": ["report", "status", "how is", "summary"],
    },
    "run_check": {
        "ml": ["പരിശോധിക്കൂ", "പരിശോധിക്കുക", "പരിശോധിക്ക്", "ചെക്ക് ചെയ്യൂ", "ചെക്ക് ചെയ്യ്",
               "ചെക്ക്", "സ്കാൻ"],  # English loanwords as speech recognition writes them
        "manglish": ["check cheyyu", "check cheyy", "parishodhikku", "parishodhikkuka", "onnu nokku"],
        "en": ["check", "scan", "inspect", "run checks"],
    },
    "list_incidents": {
        "ml": ["പ്രശ്നങ്ങൾ", "പ്രശ്നം", "എറർ", "എററുകൾ", "തകരാർ", "പ്രോബ്ലം", "പ്രോബ്ലംസ്",
               "ഇഷ്യൂ", "ഇഷ്യൂസ്"],
        "manglish": ["prashnam", "prashnangal", "error", "errors", "thakaraar"],
        "en": ["issues", "problems", "incidents", "errors", "what is broken"],
    },
    "seo_report": {
        "ml": ["എസ്ഇഒ", "എസ് ഇ ഒ"],
        "manglish": ["seo"],
        "en": ["seo"],
    },
    "approve": {
        "ml": ["അനുമതി നൽകുന്നു", "അംഗീകരിക്കുന്നു", "ചെയ്തോളൂ", "അനുമതി", "അപ്രൂവ്"],
        "manglish": ["approve", "sammatham", "cheytho", "cheythoo", "ok cheyyu"],
        "en": ["approve", "go ahead", "yes do it"],
    },
    "reject": {
        "ml": ["വേണ്ട", "നിരസിക്കുന്നു", "റദ്ദാക്കൂ", "റിജക്ട്", "ക്യാൻസൽ"],
        "manglish": ["venda", "reject", "cancel", "raddaakku"],
        "en": ["reject", "cancel", "do not", "don't"],
    },
    "backup": {
        "ml": ["ബാക്കപ്പ്"],
        "manglish": ["backup", "back up"],
        "en": ["backup", "back up"],
    },
    "about": {
        "ml": ["കഴിവുകൾ", "കഴിവുകളും", "പരിമിതികൾ", "പരിമിതികളും", "നീ ആരാണ്", "നിങ്ങൾ ആരാണ്",
               "ആരാണ് നീ", "ആരാണ് നിങ്ങൾ"],
        "manglish": ["nigal aran", "ningal aran", "ningal aranu", "ningal aaranu", "nee aranu",
                     "nee aaranu", "kazhivukal", "parimithikal"],
        "en": ["who are you", "what are you", "your capabilities", "your limitations", "your limits"],
    },
    "help": {
        "ml": ["സഹായം", "എന്തൊക്കെ ചെയ്യാം", "ഹെൽപ്പ്", "ഹെൽപ്"],
        "manglish": ["sahayam", "help"],
        "en": ["help", "what can you do"],
    },
}

APPROVAL_ID_RE = re.compile(r"\bapr-[0-9a-f]{8}\b", re.I)
# reject/approve conflict: if both match, reject wins (safer).
PRIORITY = ["reject", "approve", "backup", "seo_report", "list_incidents", "run_check",
            "status_report", "about", "help"]


@dataclass
class Intent:
    name: str
    language: str
    confidence: float
    project: str | None = None
    approval_id: str | None = None
    needs_confirmation: bool = False
    matched: str | None = None


def _fuzzy_contains(text_tokens: list[str], phrase: str, cutoff: float) -> bool:
    words = phrase.split()
    n = len(words)
    for i in range(len(text_tokens) - n + 1):
        window = " ".join(text_tokens[i:i + n])
        if difflib.SequenceMatcher(None, window, phrase).ratio() >= cutoff:
            return True
    return False


def _find_project(text: str, projects: dict[str, str]) -> str | None:
    for pid, name in projects.items():
        if pid in text or (name and normalize(name) in text):
            return pid
    return None


def parse_command(text: str, projects: dict[str, str] | None = None) -> Intent:
    """projects: {project_id: display name}."""
    language = detect_language(text)
    norm = normalize(text)
    tokens = norm.split()
    approval = APPROVAL_ID_RE.search(text)
    project = _find_project(norm, projects or {})

    for name in PRIORITY:
        table = INTENTS[name]
        for phrase in table["ml"] + table["manglish"] + table["en"]:
            p = normalize(phrase)
            if re.search(r"(?<![\wഀ-ൿ])" + re.escape(p) + r"(?![\wഀ-ൿ])", norm) \
                    or (p.isascii() and p in tokens):
                return _finish(name, language, 1.0, project, approval, phrase)
    # Fuzzy pass for Manglish spelling variation (ASCII phrases only).
    for name in PRIORITY:
        for phrase in INTENTS[name]["manglish"]:
            if _fuzzy_contains(tokens, normalize(phrase), 0.8):
                return _finish(name, language, 0.7, project, approval, phrase)
    return Intent("unknown", language, 0.0, project,
                  approval.group(0).upper() if approval else None)


def _finish(name, language, confidence, project, approval, phrase) -> Intent:
    approval_id = approval.group(0).upper() if approval else None
    needs = False
    if name in ("approve", "reject"):
        # Never act on approve/reject without the exact approval id; always confirm.
        needs = True
        if not approval_id:
            confidence = min(confidence, 0.5)
    return Intent(name, language, confidence, project, approval_id, needs, phrase)
