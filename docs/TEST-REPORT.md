# Test report: versions 0.1–0.4

Environment: a cloud build sandbox (Linux, Python 3.13). **This is not your laptop**, and the sandbox can't reach most websites.

## Automated tests: 66 passed

Run with `pytest`. They use a simulated WordPress site (`tests/fakesite.py`) and cover:

- **Isolation:** project A's encrypted credentials can't be decrypted with project B's key. Storage is separate per project and protected against path tricks. Monitor, approval and change objects refuse to mix projects.
- **Feature switches:** SEO off produces no SEO checks or report section; SEO on runs them. Turning SEO off later closes old SEO incidents. Applications can't enable WordPress features.
- **Checks:** HTTP 500, WordPress critical-error page, missing homepage text, SSL expiry levels, exposed `.env`, noindex, robots.txt blocking the site, sitemap.
- **WordPress access:** with no credential, everything is reported NOT CHECKED. With an Editor credential, plugins are NOT CHECKED with the reason given. With an Administrator credential, an over-privilege warning plus plugin update detection. With the Guardian Monitor role plus connector: updates, PHP fatal errors and Rank Math settings. A wrong password is reported as a failure.
- **Honesty:** a fully unreachable site produces no false OK results. An offline monitor reports nothing as down. A proxy answering 403 isn't treated as "online".
- **Approvals:** no change without approval. An approval is bound to the exact action, single-use, and expires.
- **Changes:** a verified change marks its incident fixed. A change that breaks the site is rolled back and reported as failed. A change that WordPress accepts but doesn't save is *not* reported as success. No backup means no change.
- **Language:** Malayalam, Manglish and English detection and command parsing, including misspelt Manglish. Approve without an approval id gives low confidence; reject wins over approve.
- **Voice metrics:** CER and WER calculations. VERIFIED requires ≥30 samples and the accuracy thresholds.

## Auto-start and passphrase storage (added in version 0.2): 18 tests, total now 84 passed

Run with an in-memory credential store and a recorded fake `schtasks`, because the real Windows services can't run on the Linux build machine:

- **Storage:** store, read and delete work. Each vault location has its own entry. When no credential store exists, it's reported as unavailable, never faked.
- **The passphrase is checked before saving.** A wrong passphrase is refused and nothing is stored.
- **No plain text:** after storing the passphrase and installing auto-start, no file in the data or config folders contains it, in UTF-8 or UTF-16. `passphrase status` never prints it.
- **No prompts when stored.** Without a stored passphrase, a background run fails with a clear message instead of hanging.
- **`autostart install` refuses** when the passphrase isn't stored, when the stored one is outdated, or when a plain-text `GUARDIAN_PASSPHRASE` variable is set. In each case `schtasks` is never called.
- **Install creates the task but doesn't start it.**
- **Task definition** (the UTF-16 XML is parsed in the test): logon trigger for the current user, interactive, least privilege, keeps running on battery, no time limit, restarts on failure, never runs twice at once.
- **Launcher script:** removes any `GUARDIAN_PASSPHRASE` variable, escapes paths containing apostrophes, and writes to a log.
- **The monitoring loop survives a failed run.**

**Not verified until run on your laptop:** real Credential Manager storage, real `schtasks` acceptance of the XML, the hidden PowerShell start at login, and the Task Scheduler status output (its labels may differ on non-English Windows).

## Dashboard, voice and AI (versions 0.3–0.4): 150 tests in total

- **Dashboard:** login and lockout, CSRF protection, language switch, write-only credentials, escaping of malicious input, approvals, connecting websites, and the WordPress connection test against the simulated WordPress site.
- **Voice (real Chromium at phone size, simulated speech engines; 15 scenarios):** Malayalam and English input, spoken replies in the matching voice, no Malayalam read by an English voice, continuous listening, auto-stop after silence, stop mid-sentence, the no-speech hint, denied microphone, unsupported browser, voice can't approve, and the microphone test with Chromium's fake microphone, a silent one and a denied one.
- **AI (simulated Ollama model):** answers built from tool results, `<think>` text removed, no approve or credential tools, an invented "approve" tool is refused, invalid project IDs refused, string arguments handled, the SEO switch respected, tool rounds capped, an unavailable model reported honestly, one question at a time, the model server must be on this machine, dashboard routing and AI status, and the dashboard staying responsive while the AI thinks (tested on a real server; the test fails without the fix).
- **Not verifiable here:** real speech recognition of your voice, real Malayalam speech output, and the real AI model's quality and speed. Ollama, its model registry and Hugging Face are blocked in the build sandbox.

## Real-network tests (limited by the sandbox)
- Command-line flow run end to end: init, add projects, check, report, incidents, the SEO toggle and the `lang` command.
- The real run found and fixed 3 honesty bugs:
  1. Public user-list check reported OK when its request failed.
  2. Sitemap check reported FAIL when nothing was reachable.
  3. Offline detection accepted the proxy's 403 as "online".
- **Not possible here:** real WordPress sites, real Telegram, and Malayalam speech models. Hugging Face downloads are blocked in this environment.

## Not yet verified (must be done on your laptop or sites)
| Item | How it will be verified |
|---|---|
| Guardian Connector on a real WordPress site | Install on a staging site, run `guardian check`, compare with wp-admin |
| Monitoring your 4 real sites + app | Add them, then run `guardian check all` |
| Telegram delivery | `guardian system telegram`, then trigger a test incident |
| Malayalam speech recognition accuracy | `guardian voice eval` with ≥30 of your recordings |
| Malayalam speech output naturalness | Listening test that you score |
| Windows-specific behaviour (paths, Task Scheduler) | First run on your laptop |
