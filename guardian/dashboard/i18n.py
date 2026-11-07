"""Dashboard text in Malayalam (ml) and English (en).

Check findings themselves (e.g. "HTTP 200 in 0.5s") come from the checks and
are currently English; only the interface is translated.
"""

from __future__ import annotations

LANGS = ("ml", "en")
DEFAULT_LANG = "ml"

STRINGS: dict[str, dict[str, str]] = {
    "app_title": {"ml": "Guardian · AI ഏജന്റ്", "en": "Guardian · AI Agent"},
    "nav_overview": {"ml": "അവലോകനം", "en": "Overview"},
    "nav_projects": {"ml": "പ്രോജക്റ്റുകൾ", "en": "Projects"},
    "nav_approvals": {"ml": "അനുമതികൾ", "en": "Approvals"},
    "nav_chat": {"ml": "ചാറ്റ്", "en": "Chat"},
    "logout": {"ml": "ലോഗൗട്ട്", "en": "Log out"},
    "language": {"ml": "English", "en": "മലയാളം"},

    "login_title": {"ml": "ലോഗിൻ", "en": "Log in"},
    "password": {"ml": "പാസ്‌വേഡ്", "en": "Password"},
    "login_button": {"ml": "ലോഗിൻ ചെയ്യുക", "en": "Log in"},
    "login_failed": {"ml": "പാസ്‌വേഡ് തെറ്റാണ്.", "en": "Wrong password."},
    "login_locked": {"ml": "പല തവണ തെറ്റിയതിനാൽ 15 മിനിറ്റ് കഴിഞ്ഞ് വീണ്ടും ശ്രമിക്കുക.",
                     "en": "Too many failed attempts. Try again in 15 minutes."},

    "no_projects": {"ml": "ഇതുവരെ പ്രോജക്റ്റുകൾ ഇല്ല.", "en": "No projects yet."},
    "add_project": {"ml": "പ്രോജക്റ്റ് ചേർക്കുക", "en": "Add project"},
    "check_now": {"ml": "ഇപ്പോൾ പരിശോധിക്കുക", "en": "Check now"},
    "checking": {"ml": "പരിശോധിക്കുന്നു…", "en": "Checking…"},
    "check_started": {"ml": "പരിശോധന തുടങ്ങി. അൽപ്പസമയം കഴിഞ്ഞ് പേജ് പുതുക്കുക.",
                      "en": "Check started. Refresh the page in a little while."},
    "last_check": {"ml": "അവസാന പരിശോധന", "en": "Last check"},
    "never_checked": {"ml": "ഇതുവരെ പരിശോധിച്ചിട്ടില്ല", "en": "Never checked"},
    "details": {"ml": "വിശദാംശങ്ങൾ", "en": "Details"},
    "problems": {"ml": "പ്രശ്നങ്ങൾ", "en": "Problems"},
    "warnings": {"ml": "മുന്നറിയിപ്പുകൾ", "en": "Warnings"},
    "ok": {"ml": "ശരിയായി പ്രവർത്തിക്കുന്നു", "en": "Working correctly"},
    "not_checked": {"ml": "പരിശോധിക്കാൻ കഴിഞ്ഞില്ല (കാരണം സഹിതം)", "en": "Not checked (and why)"},
    "open_incidents": {"ml": "തുറന്ന പ്രശ്നങ്ങൾ", "en": "Open incidents"},
    "pending_approvals": {"ml": "തീർപ്പാകാത്ത അനുമതികൾ", "en": "Pending approvals"},
    "none": {"ml": "ഒന്നുമില്ല", "en": "None"},
    "seo_section": {"ml": "SEO റിപ്പോർട്ട്", "en": "SEO report"},

    "project_id": {"ml": "പ്രോജക്റ്റ് ID", "en": "Project ID"},
    "project_id_help": {"ml": "ചെറിയ ഇംഗ്ലീഷ് അക്ഷരങ്ങൾ, അക്കങ്ങൾ, - മാത്രം (ഉദാ: docs-media)",
                        "en": "Lowercase letters, digits and - only (e.g. docs-media)"},
    "name": {"ml": "പേര്", "en": "Name"},
    "type": {"ml": "തരം", "en": "Type"},
    "type_wordpress": {"ml": "WordPress വെബ്സൈറ്റ്", "en": "WordPress website"},
    "type_website": {"ml": "മറ്റ് വെബ്സൈറ്റ്", "en": "Other website"},
    "type_application": {"ml": "സോഫ്റ്റ്‌വെയർ ആപ്പ്", "en": "Software application"},
    "url": {"ml": "വിലാസം (URL)", "en": "Address (URL)"},
    "seo_include": {"ml": "SEO നിരീക്ഷണം ഉൾപ്പെടുത്തുക", "en": "Include SEO monitoring"},
    "save": {"ml": "സേവ് ചെയ്യുക", "en": "Save"},
    "saved": {"ml": "സേവ് ചെയ്തു.", "en": "Saved."},
    "created": {"ml": "പ്രോജക്റ്റ് ചേർത്തു.", "en": "Project added."},
    "settings": {"ml": "ക്രമീകരണങ്ങൾ", "en": "Settings"},
    "features": {"ml": "സവിശേഷതകൾ", "en": "Features"},
    "feat_uptime": {"ml": "സൈറ്റ് ലഭ്യത (Uptime)", "en": "Uptime"},
    "feat_ssl": {"ml": "SSL സർട്ടിഫിക്കറ്റ്", "en": "SSL certificate"},
    "feat_error_detection": {"ml": "പിശകുകൾ കണ്ടെത്തൽ", "en": "Error detection"},
    "feat_wordpress": {"ml": "WordPress പരിശോധന", "en": "WordPress inspection"},
    "feat_updates": {"ml": "അപ്ഡേറ്റുകൾ", "en": "Updates"},
    "feat_security": {"ml": "സുരക്ഷ", "en": "Security"},
    "feat_seo": {"ml": "SEO നിരീക്ഷണം", "en": "SEO monitoring"},
    "feat_health_endpoint": {"ml": "ആപ്പ് ഹെൽത്ത് പരിശോധന", "en": "App health check"},
    "feat_backups": {"ml": "ബാക്കപ്പുകൾ", "en": "Backups"},
    "feat_reports": {"ml": "റിപ്പോർട്ടുകൾ", "en": "Reports"},
    "feat_voice_reports": {"ml": "ശബ്ദ റിപ്പോർട്ടുകൾ", "en": "Voice reports"},
    "key_paths": {"ml": "പ്രധാന പേജുകൾ (കോമ ഇട്ട് വേർതിരിക്കുക)", "en": "Key pages (comma separated)"},
    "expected_text": {"ml": "ഹോംപേജിൽ ഉണ്ടായിരിക്കേണ്ട വാക്ക്", "en": "Text that must appear on the homepage"},

    "credentials": {"ml": "ക്രെഡൻഷ്യലുകൾ", "en": "Credentials"},
    "credentials_help": {"ml": "മൂല്യങ്ങൾ എൻക്രിപ്റ്റ് ചെയ്താണ് സൂക്ഷിക്കുന്നത്, ഒരിക്കലും തിരികെ കാണിക്കില്ല.",
                         "en": "Values are stored encrypted and are never shown again."},
    "cred_name": {"ml": "പേര്", "en": "Name"},
    "cred_value": {"ml": "മൂല്യം", "en": "Value"},
    "delete": {"ml": "നീക്കം ചെയ്യുക", "en": "Delete"},
    "deleted": {"ml": "നീക്കം ചെയ്തു.", "en": "Deleted."},

    "approve": {"ml": "അനുമതി നൽകുക", "en": "Approve"},
    "reject": {"ml": "നിരസിക്കുക", "en": "Reject"},
    "approved": {"ml": "അനുമതി നൽകി.", "en": "Approved."},
    "rejected": {"ml": "നിരസിച്ചു.", "en": "Rejected."},
    "risk": {"ml": "അപകടസാധ്യത", "en": "Risk"},
    "reason": {"ml": "കാരണം", "en": "Reason"},
    "approve_confirm": {"ml": "ഈ മാറ്റത്തിന് അനുമതി നൽകണോ?", "en": "Approve this change?"},

    "chat_placeholder": {"ml": "സന്ദേശം എഴുതുക (മലയാളം, Manglish, English)…",
                         "en": "Type a message (Malayalam, Manglish, English)…"},
    "send": {"ml": "അയയ്ക്കുക", "en": "Send"},
    "chat_intro": {"ml": "നമസ്കാരം! \"സഹായം\" എന്ന് എഴുതിയാൽ എനിക്ക് എന്തൊക്കെ ചെയ്യാമെന്ന് കാണാം.",
                   "en": "Hello! Type \"help\" to see what I can do."},
    "error": {"ml": "പിശക്", "en": "Error"},
    "csrf_failed": {"ml": "സുരക്ഷാ പരിശോധന പരാജയപ്പെട്ടു. പേജ് പുതുക്കി വീണ്ടും ശ്രമിക്കുക.",
                    "en": "Security check failed. Refresh the page and try again."},
}


def t(key: str, lang: str) -> str:
    entry = STRINGS.get(key)
    if entry is None:
        return key
    return entry.get(lang) or entry["en"]
