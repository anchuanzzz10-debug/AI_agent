"""Command-line interface.

    guardian init
    guardian project add --id site-one --name "Site One" --type wordpress --url https://... [--seo]
    guardian project list | show ID | set ID FEATURE on|off
    guardian cred set ID NAME          (value is prompted, hidden)
    guardian cred list ID              (names only)
    guardian system telegram           (bot token + default chat id)
    guardian check ID|all [--no-confirm]
    guardian incidents ID [--all]
    guardian approvals ID | approve ID APPROVAL | reject ID APPROVAL
    guardian lang "text"
    guardian voice status | voice eval --manifest FILE [--provider faster-whisper --model medium]
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
from pathlib import Path

import httpx

from .approvals import ApprovalGate, ApprovalStatus
from .incidents import IncidentManager
from .language import parse_command
from .models import FEATURES, ConfigError, ProjectConfig
from .monitor import Monitor, internet_available
from .notify import Notifier
from .registry import Registry, config_dir
from .reports import build_report
from .storage import ProjectStore, guardian_home
from .vault import Vault, VaultError


def _passphrase(confirm: bool = False) -> str:
    env = os.environ.get("GUARDIAN_PASSPHRASE")
    if env:
        return env
    first = getpass.getpass("Vault passphrase: ")
    if confirm and getpass.getpass("Repeat passphrase: ") != first:
        raise VaultError("Passphrases do not match")
    return first


def _vault() -> Vault:
    return Vault.open(guardian_home(), _passphrase())


def _client() -> httpx.Client:
    return httpx.Client(timeout=20, headers={"User-Agent": "GuardianMonitor/0.1"})


def cmd_init(args) -> int:
    Vault.initialize(guardian_home(), _passphrase(confirm=True))
    print(f"Vault initialized in {guardian_home() / 'system'}. Keep the passphrase safe; "
          "it cannot be recovered.")
    return 0


def cmd_project(args) -> int:
    reg = Registry(config_dir())
    if args.action == "add":
        features = {"seo": args.seo}
        project = ProjectConfig(id=args.id, name=args.name, type=args.type, url=args.url,
                                features=features)
        reg.add(project)
        ProjectStore(guardian_home(), project.id)
        print(f"Added {project.id} ({project.type.value}); SEO monitoring "
              f"{'ENABLED' if project.enabled('seo') else 'DISABLED'}.")
    elif args.action == "list":
        for p in reg.load_all():
            on = [k for k, v in p.features.items() if v]
            print(f"{p.id:<20} {p.type.value:<12} {p.url:<40} seo={'on' if p.enabled('seo') else 'off'}  [{', '.join(on)}]")
    elif args.action == "show":
        print(json.dumps(reg.get(args.id).to_dict(), indent=2, ensure_ascii=False))
    elif args.action == "set":
        if args.feature not in FEATURES:
            raise ConfigError(f"Unknown feature; choose from: {', '.join(FEATURES)}")
        p = reg.set_feature(args.id, args.feature, args.value == "on")
        print(f"{p.id}: {args.feature} = {'on' if p.enabled(args.feature) else 'off'}")
    return 0


def cmd_cred(args) -> int:
    Registry(config_dir()).get(args.id)  # project must exist
    creds = _vault().project(args.id)
    if args.action == "set":
        creds.set(args.name, getpass.getpass(f"Value for {args.id}/{args.name}: "))
        print(f"Stored {args.name} for {args.id} (encrypted with this project's key).")
    elif args.action == "list":
        print("\n".join(creds.names()) or "(none)")
    elif args.action == "delete":
        print("deleted" if creds.delete(args.name) else "not found")
    return 0


def cmd_system(args) -> int:
    system = _vault().system()
    system.set("telegram_bot_token", getpass.getpass("Telegram bot token: "))
    system.set("telegram_default_chat_id", input("Default Telegram chat id: ").strip())
    print("Telegram configured.")
    return 0


def _check_once(args, reg: Registry, vault: Vault) -> int:
    projects = reg.load_all() if args.id == "all" else [reg.get(args.id)]
    notifier = Notifier(vault.system())
    worst = 0
    with _client() as client, _client() as wporg:
        online = (lambda: True) if args.skip_connectivity else (lambda: internet_available(client))
        for project in projects:
            store = ProjectStore(guardian_home(), project.id)
            monitor = Monitor(project, store, vault.project(project.id), client, notifier,
                              confirm_delay_s=0 if args.no_confirm else 45, wporg_client=wporg,
                              connectivity=online)
            summary = monitor.run()
            if not args.quiet:
                print(build_report(project, store, summary.results, summary.run_id))
            if summary.alerts_not_sent:
                print(f"NOTE [{project.id}]: {summary.alerts_not_sent} alert(s) NOT sent (Telegram "
                      "not configured or send failed). See the project audit log.")
            if any(r.status.value == "fail" for r in summary.results):
                worst = 2
    return worst


def cmd_check(args) -> int:
    reg, vault = Registry(config_dir()), _vault()
    if not args.loop:
        return _check_once(args, reg, vault)
    print(f"Monitoring every {args.loop} min. Ctrl+C to stop.")
    while True:
        started = time.monotonic()
        code = _check_once(args, reg, vault)
        print(f"{time.strftime('%H:%M:%S')} run finished (exit {code})")
        time.sleep(max(5.0, args.loop * 60 - (time.monotonic() - started)))


def cmd_incidents(args) -> int:
    store = ProjectStore(guardian_home(), Registry(config_dir()).get(args.id).id)
    for inc in IncidentManager(store).list(include_closed=args.all):
        print(f"{inc['id']}  [{inc['severity']}]  {inc['status']:<26} {inc['title']}")
    return 0


def cmd_approvals(args) -> int:
    store = ProjectStore(guardian_home(), Registry(config_dir()).get(args.id).id)
    gate = ApprovalGate(store)
    if args.action == "list":
        for row in gate.list():
            print(f"{row['id']}  {row['status']:<9} {row['action']:<18} risk={row['data']['risk']}  "
                  f"{row['data']['reason']}")
    else:
        row = gate.decide(args.approval_id, approve=args.action == "approve")
        print(f"{row['id']} → {row['status']}")
    return 0


def cmd_lang(args) -> int:
    projects = {p.id: p.name for p in Registry(config_dir()).load_all()}
    intent = parse_command(args.text, projects)
    print(json.dumps(intent.__dict__, ensure_ascii=False, indent=2))
    return 0


def cmd_voice(args) -> int:
    from .voice.evaluate import evaluate, voice_status
    out_dir = guardian_home() / "voice"
    if args.action == "status":
        for k, v in voice_status(out_dir).items():
            print(f"{k}: {v}")
        return 0
    from .voice.providers import STT_PROVIDERS
    stt = STT_PROVIDERS[args.provider](args.model)
    report = evaluate(stt, Path(args.manifest), out_dir)
    print(json.dumps({k: v for k, v in report.items() if k != "details"}, ensure_ascii=False, indent=2))
    return 0 if report["verified"] else 3


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="guardian")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init").set_defaults(fn=cmd_init)

    p = sub.add_parser("project")
    ps = p.add_subparsers(dest="action", required=True)
    add = ps.add_parser("add")
    add.add_argument("--id", required=True)
    add.add_argument("--name", required=True)
    add.add_argument("--type", required=True, choices=["wordpress", "website", "application"])
    add.add_argument("--url", required=True)
    add.add_argument("--seo", action="store_true", help="include SEO monitoring")
    ps.add_parser("list")
    show = ps.add_parser("show")
    show.add_argument("id")
    st = ps.add_parser("set")
    st.add_argument("id")
    st.add_argument("feature")
    st.add_argument("value", choices=["on", "off"])
    p.set_defaults(fn=cmd_project)

    c = sub.add_parser("cred")
    cs = c.add_subparsers(dest="action", required=True)
    for name in ("set", "delete"):
        x = cs.add_parser(name)
        x.add_argument("id")
        x.add_argument("name")
    cl = cs.add_parser("list")
    cl.add_argument("id")
    c.set_defaults(fn=cmd_cred)

    s = sub.add_parser("system")
    s.add_argument("what", choices=["telegram"])
    s.set_defaults(fn=cmd_system)

    ch = sub.add_parser("check")
    ch.add_argument("id")
    ch.add_argument("--no-confirm", action="store_true", help="skip the 45 s failure re-check")
    ch.add_argument("--loop", type=float, metavar="MINUTES", help="repeat every N minutes")
    ch.add_argument("--quiet", action="store_true", help="do not print full reports")
    ch.add_argument("--skip-connectivity", action="store_true",
                    help="do not check the monitor's own internet connection first")
    ch.set_defaults(fn=cmd_check)

    inc = sub.add_parser("incidents")
    inc.add_argument("id")
    inc.add_argument("--all", action="store_true")
    inc.set_defaults(fn=cmd_incidents)

    a = sub.add_parser("approvals")
    a.add_argument("action", choices=["list", "approve", "reject"])
    a.add_argument("id")
    a.add_argument("approval_id", nargs="?")
    a.set_defaults(fn=cmd_approvals)

    lg = sub.add_parser("lang")
    lg.add_argument("text")
    lg.set_defaults(fn=cmd_lang)

    v = sub.add_parser("voice")
    v.add_argument("action", choices=["status", "eval"])
    v.add_argument("--manifest")
    v.add_argument("--provider", default="faster-whisper", choices=["faster-whisper", "hf"])
    v.add_argument("--model", default="medium")
    v.set_defaults(fn=cmd_voice)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "cmd", None) == "approvals" and args.action != "list" and not args.approval_id:
        print("approval id required", file=sys.stderr)
        return 1
    try:
        return args.fn(args)
    except (ConfigError, VaultError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
