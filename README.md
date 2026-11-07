# Guardian: Personal AI Agent (core)

The foundation of a personal agent that monitors several websites and applications, detects problems early, alerts you, and makes changes **only with your approval**: it takes a backup first, verifies the result afterwards, and rolls back if verification fails.

It is built to manage **4 WordPress websites + 1 custom application** to start with, and to scale by adding one config file per project.

> **Honesty rules enforced in code**
> - A check that could not run is reported as **NOT CHECKED, with the reason**. It is never reported as OK.
> - Only the verifier can mark an incident **fixed**. A "not checked" result does not count as a pass.
> - No change without an approval that is single-use, expires, and is bound to that exact change.
> - No change without a successful backup.
> - If the monitoring machine is offline, sites are **not** reported as down.
> - Malayalam voice is reported **NOT TESTED** until it has been measured on your own recordings.

## What works now (version 0.1, tested)

| Area | Status |
|---|---|
| Multiple projects: separate config, encrypted credentials (one key per project), database, logs, reports, backups, alert chat | ✅ Built; isolation covered by tests |
| Per-project feature switches, including **SEO on/off** chosen at connect time and changeable later | ✅ |
| Uptime, response time, expected homepage text, visible PHP/database errors, maintenance mode | ✅ |
| Failure confirmation (re-check before alerting) and offline detection for the monitoring machine | ✅ |
| SSL expiry, exposed files (`.env`, `.git`, `debug.log`), security headers, public user listing | ✅ |
| Application health endpoint | ✅ |
| WordPress REST inspection: pages, posts, media, plugins, themes, settings, plus a least-privilege warning | ✅ (what each WordPress role can see is listed below) |
| Plugin update detection for WordPress.org plugins | ✅ |
| Guardian Connector plugin: core version, all updates including premium, PHP error log, administrator list, Rank Math settings | ✅ Written and syntax-checked. ⚠️ Not yet tested on a real WordPress install. |
| Technical SEO: robots.txt, noindex, title, meta description, canonical, H1, sitemap, Rank Math detection | ✅ |
| Approved changes from the dashboard (WordPress): propose a plugin on/off or a page/post title, excerpt or status change → Approve → backup → change → verify (change + key pages) → automatic rollback if anything fails; result shown on the Approvals page; linked incident closed only when verified | ✅ Tested end to end against the simulated WordPress site (13 tests plus a browser run). ⚠️ Not yet used on a real site |
| Reports in Malayalam and English, with a "Not checked (and why)" section | ✅ |
| Telegram alerts (sending) | ✅ Code works. ⚠️ Not yet tested with a real bot. |
| Dashboard (laptop + phone, Malayalam/English): overview with totals and a monitoring-freshness warning, Incidents (evidence + history), check history, System status (no secrets shown), connect websites/apps (health-check address for apps), WordPress connection test, settings, approvals, chat | ✅ 173 tests, plus a real-browser check of 7 pages × 2 languages × laptop/phone. ⚠️ Newest pages not yet opened on your laptop |
| Voice chat (browser speech, ml-IN / en-IN, spoken replies, microphone test) | ✅ Page logic tested in a real browser with simulated speech engines. ⚠️ Recognition of your own voice not yet tested. |
| AI brain: local model (Ollama) answers free-form questions in Malayalam/English using read-only tools | ✅ Tool handling and safety rules tested with a simulated model. ⚠️ The real model's answer quality, speed and Malayalam not yet tested (needs your laptop). |
| Auto-start: passphrase in Windows Credential Manager + Task Scheduler task | ✅ Logic tested with a simulated credential store and a simulated `schtasks`. ⚠️ Not yet run on real Windows. |
| Malayalam, Manglish and English command parsing (fixed command dictionary) | ✅ |
| Malayalam voice: speech recognition and speech output providers, plus the evaluation tool | ⚠️ **Code only, NOT TESTED** (see "Malayalam voice" below) |

## Not built yet (planned)
- Search Console integration (indexing status, keyword clicks and positions). Reported as NOT CHECKED until built.
- More change types: plugin/theme/core updates (need full backups), content edits, and fixes for non-WordPress sites. Changes the agent proposes by itself from incidents (today you propose them).
- Telegram bot that *receives* messages (voice notes, Approve/Reject buttons).
- Full database and file backups. Without them the agent refuses plugin, theme and core updates.
- AI diagnosis of unfamiliar problems, and AI-proposed fixes (the AI can currently only read).
- Screenshot comparison, form and checkout tests, the custom-app connector (depends on the app's stack).

## Install (Windows 11)

```powershell
winget install Python.Python.3.12
```

```powershell
git clone https://github.com/anchuanzzz10-debug/AI_agent.git
cd AI_agent
py -m venv .venv
.venv\Scripts\activate
pip install -e .[dev]
```

```powershell
pytest
```

```powershell
guardian init
```
`guardian init` asks for a passphrase of at least 12 characters. **It cannot be recovered.** Turn on **BitLocker** for the drive; it's included with Windows 11 Pro.

## Connect your projects

```powershell
guardian project add --id website-1 --name "Website 1" --type wordpress --url https://site1.com --seo
```
```powershell
guardian project add --id website-3 --name "Website 3" --type wordpress --url https://site3.com
```
```powershell
guardian project add --id app-1 --name "My App" --type application --url https://app.example.com
```
```powershell
guardian project set website-3 seo on
```
```powershell
guardian project list
```

Leave out `--seo` and SEO monitoring and SEO reports are off. Applications start with SEO off. Further options (key pages, expected text, alert chat, language) are in `config/projects/*.yaml`; see the `*.example.yaml` files.

### WordPress access (least privilege)

1. **Recommended:** install the plugin in `wordpress-plugin/guardian-connector/` (zip that folder and upload it under Plugins → Add New → Upload).
2. Create a WordPress user with the **Guardian Monitor** role. It can read status data only; it cannot edit anything.
3. As that user, go to Profile → **Application Passwords** and create one called "Guardian".
4. Store it:
   ```powershell
   guardian cred set website-1 wp_read_user
   ```
   ```powershell
   guardian cred set website-1 wp_read_app_password
   ```
5. **Only if you want approved changes:** create a separate Application Password for an administrator and store it as `wp_change_user` / `wp_change_app_password`. It is used only after you approve a specific change, and you can revoke it at any time to make the agent read-only.
6. **PHP error log:** in `wp-config.php`, log to a **private** path outside the web root, for example `define('WP_DEBUG_LOG', '/home/USER/logs/wp-errors.log'); define('WP_DEBUG_DISPLAY', false);`. The default `wp-content/debug.log` is downloadable by anyone, and the agent flags it.

**What each credential can see:**

| Data | Editor | Administrator | Guardian Monitor + Connector |
|---|---|---|---|
| Pages, posts (including drafts), media | ✅ | ✅ | via Editor credential |
| Plugin / theme list | ❌ | ✅ | ✅ (versions and updates) |
| Core version, all updates (including premium) | ❌ | ❌ | ✅ |
| PHP error log | ❌ | ❌ | ✅ (if logging is enabled) |
| Rank Math settings | ❌ | ❌ | ✅ |
| Can change the site | some | everything | **nothing** |

When a WordPress site has a **fatal error**, its REST API is usually down too. The agent will detect and report it, but fixing it then needs SSH/SFTP or hosting-panel access.

## Run checks

```powershell
guardian check all
```
```powershell
guardian check website-1
```
```powershell
guardian check all --loop 5 --quiet
```
`--loop 5` keeps monitoring every 5 minutes. A failed run is logged and the loop carries on.

## Auto-start at Windows login

The vault passphrase is stored in **Windows Credential Manager** (encrypted for your Windows user account), never in a file or environment variable. A Task Scheduler task then starts monitoring 1 minute after you log in.

```powershell
guardian passphrase store
```
```powershell
guardian autostart install --interval 5
```
```powershell
guardian autostart start
```
```powershell
guardian autostart status
```

- `install` refuses if the passphrase isn't stored or is wrong, if no projects exist, or if a plain-text `GUARDIAN_PASSPHRASE` environment variable is set. It creates the task but **doesn't start it**.
- The task runs as you, without admin rights, and only while you're logged in. It keeps running on battery, has no time limit, restarts every minute if it crashes, and never runs twice at once.
- Output goes to `data\logs\autostart.log`.
- To turn it off: `guardian autostart remove`, then `guardian passphrase remove`.
- **Security trade-off:** any program running under your Windows account can read Credential Manager entries. Use a strong Windows password or PIN and keep BitLocker on.

## Dashboard (laptop and phone, Malayalam and English)

A web control panel with an overview of all projects, project details with grouped results (including SEO only when it's on), settings and feature switches, write-only credentials, Approve/Reject buttons, and a chat box that understands Malayalam, Manglish and English commands.

```powershell
guardian dashboard password
```
```powershell
guardian dashboard serve
```
Then open **http://127.0.0.1:8765** in your browser. Use the button at the top to switch between Malayalam and English.

**On your phone (private HTTPS over Tailscale; free; needed for the phone's microphone):**
1. Install Tailscale on the laptop and the phone, signed in to the same account.
2. In the Tailscale admin console, under **DNS**, turn on **MagicDNS** and **HTTPS Certificates**.
3. Keep the dashboard on the laptop only (`guardian dashboard serve`), then in a second PowerShell window run:
   ```powershell
   tailscale serve --bg 8765
   ```
   It prints an address like `https://your-laptop.tailnet-name.ts.net`. Open that address on your phone.
4. To stop sharing it: `tailscale serve reset`.

The dashboard never listens outside the laptop itself: Tailscale passes requests in over an encrypted connection that only your own devices can use. Never use `--host 0.0.0.0`, which exposes the dashboard to everyone on your Wi-Fi network.

### Voice chat (Malayalam and English)
On the **Chat** page, choose the speaking language (**മലയാളം** = ml-IN or **English** = en-IN), press 🎤 and speak. What you said appears as your message and is sent. Tick **🔊 Read replies aloud** to hear the answers.

- It uses the browser's built-in speech service, so **your voice is sent to Google (Chrome) or Microsoft (Edge)**. It's free, but needs the internet.
- **Browsers:** Chrome or Edge on the laptop, Chrome on Android. On other browsers the 🎤 button is hidden and the page says why.
- **The microphone needs HTTPS:** `http://127.0.0.1` on the laptop is fine; on a phone, use the Tailscale `https://` address above.
- **Malayalam spoken replies** need a Malayalam voice on the device. On Android: Settings → Text-to-speech → Google → install Malayalam. On Windows, Edge's online voices usually include Malayalam. If the device has none, the page says so and doesn't read Malayalam with an English voice.
- Voice understands the same commands as typed chat (report, check, problems, SEO, help), including English words as Malayalam speech recognition writes them (ചെക്ക്, പ്രോബ്ലംസ്). It **can't approve changes**.
- **How well it recognises your Malayalam isn't known until you try it.** Please report what it gets wrong.

**Security:** a separate dashboard password (stored as a scrypt hash), a lockout after 5 wrong attempts (15 minutes), sessions that expire after 12 idle hours, CSRF protection on every change, strict security headers, and credential values that are never displayed. Restarting the dashboard logs everyone out.

**Chat (this phase):** a fixed set of commands with no AI model: report, check, problems, SEO report and help. It never approves anything; use the Approvals page. Free-form AI answers come in the next phase.

## AI brain (free, local, private)

The chat understands free-form questions such as "ഇന്ന് എന്തെങ്കിലും ശ്രദ്ധിക്കേണ്ടതുണ്ടോ?" or "why is there a warning on my site?" through a free AI model running on your laptop with **Ollama**. Nothing is sent to the internet.

```powershell
winget install Ollama.Ollama
```
Close PowerShell and open it again, then download the model (about 5 GB, once):
```powershell
ollama pull qwen3:8b
```
```powershell
guardian ai status
```
Expected: `Status:  Ready: qwen3:8b`. Then try it from the command line:
```powershell
guardian ai ask "is my website ok?"
```
or restart `guardian dashboard serve` and ask in the **Chat** page, by typing or with 🎤.

- **How it decides:** simple commands (report, check, problems, SEO, help) are still answered instantly without AI. Anything else goes to the AI, and the reply is labelled 🤖 along with the tools it used.
- **What the AI can do:** list projects, read check results and incidents, read SEO results, list pending approvals, and start a check (which only reads websites).
- **What it can't do:** approve, change a website, or see passwords. No such tool exists. It's told to answer only from those results and to say "not checked" honestly.
- **Speed on your laptop:** about 10–60 seconds per answer, running on the CPU. The first answer after starting is slower while the model loads. Only one question is answered at a time.
- **Smaller and faster model** (lower quality): `ollama pull qwen3:4b`, then `guardian ai model qwen3:4b`.
- **Turn it off:** `guardian ai off`. Simple commands keep working.
- **Malayalam quality:** a free 8B model's Malayalam may sound unnatural, and it can make mistakes. Check important answers against the project page.

## Alerts (Telegram)

1. Create a bot with @BotFather and copy its token.
2. Send your bot a message, then find your chat id.
3. Store both:
   ```powershell
   guardian system telegram
   ```
   For alerts from one project to a separate chat, set `alert_chat_id` in that project's YAML.

If Telegram isn't configured, the agent says that the alerts were **not sent**. It never pretends they were.

**Also set up an external watchdog** (free UptimeRobot or Healthchecks.io). This agent runs on your laptop and can't alert you while the laptop is off.

## Approvals and changes

```powershell
guardian incidents website-1
```
```powershell
guardian approvals list website-1
```
```powershell
guardian approvals approve website-1 APR-1A2B3C4D
```
```powershell
guardian approvals reject website-1 APR-1A2B3C4D
```
Each change runs: approval → backup (refused if it fails) → apply → verify the change and the key pages → if anything fails, roll back and verify the rollback → report.

## Malayalam, Manglish and English

```powershell
guardian lang "website-1 ലെ പ്രശ്നങ്ങൾ പറയൂ"
```
```powershell
guardian lang "innathe report tharu"
```

## Malayalam voice: how it gets tested (required before any claim)

```powershell
guardian voice status
```
Until you run an evaluation, this reports `NOT TESTED`.

1. Record **30 or more** real instructions in your normal voice, for example as WhatsApp or Telegram voice notes exported to `voice-tests/` (that folder is git-ignored).
2. Create `voice-tests/manifest.csv`:
   ```
   audio,reference,expected_intent
   001.ogg,ഇന്നത്തെ റിപ്പോർട്ട് തരൂ,status_report
   002.ogg,website-1 check cheyyu,run_check
   ```
3. Install a speech model and evaluate:
   ```powershell
   pip install -e .[voice]
   ```
   ```powershell
   guardian voice eval --manifest voice-tests/manifest.csv --model medium
   ```
4. The result is **VERIFIED** only with ≥30 samples, ≥90% correct commands and character error rate ≤25%. Otherwise it reports the measured numbers as *below threshold*. Try other models, for example a Malayalam Whisper fine-tune through `--provider hf --model <huggingface-id>`.
5. Speech output (MMS-TTS Malayalam and others) gets a listening test that you score. This is not built into the CLI yet.

Voice commands that would change a site will always show the transcript and ask for confirmation.

## Project layout

```
guardian/          core package
  models.py        project config + feature switches
  registry.py      one YAML per project
  vault.py         encrypted per-project credentials
  storage.py       per-project database, logs, reports, backups
  checks/          uptime, errors, ssl, security, SEO, WordPress
  monitor.py       runs checks, confirms failures, incidents, alerts
  incidents.py     lifecycle; only the verifier can mark "fixed"
  approvals.py     single-use, action-bound approvals
  changes.py       backup → apply → verify → rollback
  reports.py       Malayalam/English reports
  language/        Malayalam / Manglish / English command parsing
  voice/           STT/TTS providers + evaluation
wordpress-plugin/guardian-connector/   read-only least-privilege WordPress plugin
config/projects/   *.example.yaml templates (real configs are git-ignored)
tests/             pytest suite with a simulated WordPress site
```
