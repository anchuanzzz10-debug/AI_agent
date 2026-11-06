# Test report: version 0.1

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
