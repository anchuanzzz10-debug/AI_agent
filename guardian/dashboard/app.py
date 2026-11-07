"""FastAPI dashboard application."""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Callable

import httpx
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from ..ai.agent import Agent
from ..ai.ollama import OllamaClient
from .. import __version__, autostart, secrets_store
from ..approvals import ApprovalGate, ApprovalStatus
from ..checks.wordpress import (CHANGE_PASSWORD as WP_CHANGE_PASSWORD, CHANGE_USER as WP_CHANGE_USER,
                                READ_PASSWORD as WP_READ_PASSWORD, READ_USER as WP_READ_USER,
                                WordPressInspector)
from ..incidents import CLOSED, IncidentManager, IncidentStatus
from ..models import FEATURES, ConfigError, ProjectConfig, ProjectType, _INCOMPATIBLE
from ..monitor import Monitor, internet_available
from ..notify import Notifier
from ..registry import Registry
from ..storage import ProjectStore
from ..vault import Vault
from . import auth
from .chat import ChatContext, respond
from .i18n import DEFAULT_LANG, LANGS, t

HERE = Path(__file__).parent
COOKIE = "guardian_session"
CRED_NAME_RE = re.compile(r"^[a-z0-9_]{2,40}$")
FLASH_KEYS = {"saved", "created", "created_wp", "deleted", "approved", "rejected", "check_started",
              "csrf_failed"}

CheckRunner = Callable[[ProjectConfig], None]
# Background monitoring checks every 5 minutes; older results suggest it stopped.
STALE_AFTER_MIN = 15


def default_check_runner(home: Path, vault: Vault) -> CheckRunner:
    def run(project: ProjectConfig) -> None:
        with httpx.Client(timeout=20, headers={"User-Agent": "GuardianMonitor/0.3"}) as client, \
                httpx.Client(timeout=20) as wporg:
            Monitor(project, ProjectStore(home, project.id), vault.project(project.id), client,
                    Notifier(vault.system()), confirm_delay_s=45, wporg_client=wporg,
                    connectivity=lambda: internet_available(client)).run()
    return run


def _default_http_client() -> httpx.Client:
    return httpx.Client(timeout=20, headers={"User-Agent": "GuardianMonitor/0.4"})


# Human-friendly labels for the WordPress connection test.
WP_LABELS = {
    "wp:rest_api": ("WordPress REST API", "WordPress REST API"),
    "wp:auth": ("ലോഗിൻ (Application Password)", "Login (Application Password)"),
    "wp:least_privilege": ("ആവശ്യത്തിന് മാത്രം അധികാരം", "Least-privilege access"),
    "wp:content": ("പേജുകൾ, പോസ്റ്റുകൾ, മീഡിയ", "Pages, posts, media"),
    "wp:plugins": ("പ്ലഗിനുകൾ", "Plugins"),
    "wp:themes": ("തീമുകൾ", "Themes"),
    "wp:settings": ("ക്രമീകരണങ്ങൾ", "Settings"),
    "wp:connector": ("Guardian Connector പ്ലഗിൻ", "Guardian Connector plugin"),
    "wp:core": ("WordPress പതിപ്പ്", "WordPress version"),
    "wp:updates": ("അപ്ഡേറ്റുകൾ", "Updates"),
    "wp:updates:plugins": ("പ്ലഗിൻ അപ്ഡേറ്റുകൾ", "Plugin updates"),
    "wp:updates:premium": ("പ്രീമിയം പ്ലഗിൻ അപ്ഡേറ്റുകൾ", "Premium plugin updates"),
    "wp:updates:core_themes": ("കോർ, തീം അപ്ഡേറ്റുകൾ", "Core and theme updates"),
    "errors:php_log": ("PHP പിശക് ലോഗ്", "PHP error log"),
    "security:admins": ("അഡ്മിൻ അക്കൗണ്ടുകൾ", "Administrator accounts"),
    "seo:rank_math_settings": ("Rank Math ക്രമീകരണങ്ങൾ", "Rank Math settings"),
    "seo:discourage_search": ("Search engine തടയൽ ക്രമീകരണം", "Discourage search engines setting"),
}


def wp_verdict(results) -> str:
    by = {r.name: r.status.value for r in results}
    if by.get("wp:rest_api") == "fail":
        return "wp_v_unreachable"
    if by.get("wp:auth") == "not_checked":
        return "wp_v_no_creds"
    if by.get("wp:auth") == "fail":
        return "wp_v_auth_failed"
    if by.get("wp:least_privilege") == "warn":
        return "wp_v_admin"
    return "wp_v_connector" if by.get("wp:connector") == "ok" else "wp_v_ok"


def create_app(home: Path, config_dir: Path, vault: Vault,
               check_runner: CheckRunner | None = None, sessions: auth.SessionStore | None = None,
               http_client_factory: Callable[[], httpx.Client] = _default_http_client,
               ai_client: OllamaClient | None = None,
               clock: Callable[[], float] = time.time) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    registry = Registry(config_dir)
    sessions = sessions or auth.SessionStore()
    runner = check_runner or default_check_runner(home, vault)
    running: set[str] = set()
    running_lock = threading.Lock()

    # -- helpers ----------------------------------------------------------
    def lang_of(request: Request) -> str:
        lang = request.cookies.get("lang", DEFAULT_LANG)
        return lang if lang in LANGS else DEFAULT_LANG

    def session_of(request: Request):
        return sessions.get(request.cookies.get(COOKIE))

    def render(request: Request, name: str, session=None, status_code: int = 200, **ctx) -> HTMLResponse:
        lang = lang_of(request)
        msg = request.query_params.get("msg")
        return templates.TemplateResponse(request, name, {
            "lang": lang, "t": lambda k: t(k, lang), "csrf": session.csrf if session else "",
            "flash": t(msg, lang) if msg in FLASH_KEYS else None, "path": request.url.path, **ctx,
        }, status_code=status_code)

    def to_login() -> RedirectResponse:
        return RedirectResponse("/login", status_code=303)

    def back(url: str, msg: str | None = None) -> RedirectResponse:
        return RedirectResponse(url + (f"?msg={msg}" if msg else ""), status_code=303)

    def store_for(pid: str) -> ProjectStore:
        return ProjectStore(home, pid)

    def start_check(pid: str) -> bool:
        project = registry.get(pid)
        with running_lock:
            if pid in running:
                return False
            running.add(pid)

        def job():
            try:
                runner(project)
            except Exception as exc:  # noqa: BLE001 - keep the dashboard alive
                store_for(pid).audit("dashboard", "check_error", error=f"{type(exc).__name__}: {exc}")
            finally:
                with running_lock:
                    running.discard(pid)
        threading.Thread(target=job, daemon=True, name=f"check-{pid}").start()
        return True

    ai_agent = Agent(ai_client, registry.load_all, store_for, start_check) if ai_client else None

    def summarize(project: ProjectConfig) -> dict:
        store = store_for(project.id)
        run_id, ts, results = store.latest_run()
        if not project.enabled("seo"):
            results = [r for r in results if r["category"] != "seo"]
        counts = {s: sum(r["status"] == s for r in results) for s in ("fail", "warn", "ok", "not_checked")}
        age_min = int((clock() - ts) // 60) if ts else None
        judged = counts["fail"] + counts["warn"] + counts["ok"]
        score = round(100 * (counts["ok"] + 0.5 * counts["warn"]) / judged) if judged else None
        health = ("problem" if counts["fail"] else "attention" if counts["warn"]
                  else "healthy" if counts["ok"] else "new")
        return {"project": project, "run_id": run_id,
                "checked_at": time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else None,
                "age_min": age_min, "stale": age_min is not None and age_min > STALE_AFTER_MIN,
                "score": score, "health": health,
                "results": results, "counts": counts, "running": project.id in running,
                "incidents": IncidentManager(store).list(),
                "pending": ApprovalGate(store).list(ApprovalStatus.PENDING)}

    def project_ctx(project: ProjectConfig) -> dict:
        names = vault.project(project.id).names()
        return {
            "s": summarize(project),
            "allowed": [f for f in FEATURES if f not in _INCOMPATIBLE[project.type]],
            "cred_names": names,
            "wp": {"read": WP_READ_USER in names and WP_READ_PASSWORD in names,
                   "change": WP_CHANGE_USER in names and WP_CHANGE_PASSWORD in names,
                   "https": project.url.startswith("https://"),
                   "profile_url": project.url + "/wp-admin/profile.php#application-passwords-section"},
            "wp_labels": WP_LABELS,
            "history": [h | {"at": time.strftime("%Y-%m-%d %H:%M", time.localtime(h["ts"]))}
                        for h in store_for(project.id).run_history(10)],
        }

    # -- middleware -------------------------------------------------------
    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response: Response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if not request.url.path.startswith("/static"):
            response.headers["Cache-Control"] = "no-store"
        return response

    # -- auth ---------------------------------------------------------------
    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        if session_of(request):
            return RedirectResponse("/", status_code=303)
        return render(request, "login.html")

    @app.post("/login")
    def login(request: Request, password: str = Form("")):
        client = request.client.host if request.client else "unknown"
        if sessions.locked_out(client):
            return render(request, "login.html", error="login_locked", status_code=429)
        if not auth.check_password(home, password):
            sessions.record_failure(client)
            return render(request, "login.html", error="login_failed", status_code=401)
        sessions.clear_failures(client)
        token, _ = sessions.create()
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(COOKIE, token, httponly=True, samesite="strict",
                            secure=request.url.scheme == "https", path="/")
        return response

    @app.post("/logout")
    def logout(request: Request, csrf: str = Form("")):
        session = session_of(request)
        if auth.csrf_ok(session, csrf):
            sessions.destroy(request.cookies.get(COOKIE))
        response = to_login()
        response.delete_cookie(COOKIE, path="/")
        return response

    @app.get("/lang/{code}")
    def set_lang(code: str, next: str = "/"):
        target = next if next.startswith("/") and not next.startswith("//") else "/"
        response = RedirectResponse(target, status_code=303)
        if code in LANGS:
            response.set_cookie("lang", code, samesite="strict", max_age=365 * 24 * 3600, path="/")
        return response

    # -- pages --------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def overview(request: Request):
        session = session_of(request)
        if not session:
            return to_login()
        cards = [summarize(p) for p in registry.load_all()]
        totals = {"projects": len(cards),
                  "fail": sum(c["counts"]["fail"] for c in cards),
                  "warn": sum(c["counts"]["warn"] for c in cards),
                  "incidents": sum(len(c["incidents"]) for c in cards),
                  "pending": sum(len(c["pending"]) for c in cards),
                  "stale": [c["project"].name for c in cards if c["stale"]],
                  "live": any(c["checked_at"] and not c["stale"] for c in cards)}
        return render(request, "overview.html", session, cards=cards, totals=totals,
                      stale_after=STALE_AFTER_MIN, today=time.strftime("%d-%m-%Y", time.localtime(clock())))

    @app.get("/projects/new", response_class=HTMLResponse)
    def new_project_page(request: Request):
        session = session_of(request)
        if not session:
            return to_login()
        return render(request, "project_new.html", session, form={})

    @app.post("/projects/new")
    def create_project(request: Request, csrf: str = Form(""), id: str = Form(""), name: str = Form(""),
                       type: str = Form("wordpress"), url: str = Form(""), seo: str = Form(""),
                       health_path: str = Form("")):
        session = session_of(request)
        if not session:
            return to_login()
        if not auth.csrf_ok(session, csrf):
            return back("/projects/new", "csrf_failed")
        form = {"id": id.strip(), "name": name.strip(), "type": type, "url": url.strip(), "seo": bool(seo),
                "health_path": health_path.strip()}
        try:
            features = {"seo": form["seo"]}
            extra = {}
            if form["type"] == "application":
                hp = form["health_path"]
                if hp and not hp.startswith("/"):
                    raise ConfigError("Health check address must start with / (e.g. /health)")
                # No health address given: don't invent one (it would raise a false alarm).
                features["health_endpoint"] = bool(hp)
                if hp:
                    extra["health_path"] = hp
            project = ProjectConfig(id=form["id"], name=form["name"] or form["id"], type=form["type"],
                                    url=form["url"], features=features, **extra)
            registry.add(project)
            store_for(project.id).audit("dashboard", "project_created", features=project.features)
        except ConfigError as exc:
            return render(request, "project_new.html", session, form=form, error=str(exc), status_code=400)
        if project.type is ProjectType.WORDPRESS:
            return RedirectResponse(f"/projects/{project.id}?msg=created_wp#wordpress", status_code=303)
        return back(f"/projects/{project.id}", "created")

    @app.get("/projects/{pid}", response_class=HTMLResponse)
    def project_page(request: Request, pid: str):
        session = session_of(request)
        if not session:
            return to_login()
        try:
            project = registry.get(pid)
        except ConfigError:
            return render(request, "error.html", session, error=f"No project {pid!r}", status_code=404)
        return render(request, "project.html", session, **project_ctx(project))

    @app.post("/projects/{pid}/wordpress")
    def wordpress_connect(request: Request, pid: str, csrf: str = Form(""), op: str = Form("save_test"),
                          read_user: str = Form(""), read_password: str = Form(""),
                          change_user: str = Form(""), change_password: str = Form("")):
        session = session_of(request)
        if not session:
            return to_login()
        if not auth.csrf_ok(session, csrf):
            return back(f"/projects/{pid}", "csrf_failed")
        project = registry.get(pid)
        if project.type is not ProjectType.WORDPRESS:
            return back(f"/projects/{pid}")
        creds = vault.project(pid)
        if op == "remove_change":
            creds.delete(WP_CHANGE_USER)
            creds.delete(WP_CHANGE_PASSWORD)
            store_for(pid).audit("dashboard", "wordpress_change_access_removed")
            return back(f"/projects/{pid}", "deleted")
        saved = []
        for name, value in ((WP_READ_USER, read_user), (WP_READ_PASSWORD, read_password),
                            (WP_CHANGE_USER, change_user), (WP_CHANGE_PASSWORD, change_password)):
            if value.strip():
                creds.set(name, value.strip())
                saved.append(name)
        with http_client_factory() as client:
            results = WordPressInspector(project, client, creds, wporg_client=None).run()
        verdict = wp_verdict(results)
        store_for(pid).audit("dashboard", "wordpress_connection_tested", saved=saved, verdict=verdict,
                             statuses={r.name: r.status.value for r in results})
        return render(request, "project.html", session, **project_ctx(project),
                      wp_test=results, wp_verdict=verdict)

    @app.post("/projects/{pid}/check")
    def check(request: Request, pid: str, csrf: str = Form("")):
        session = session_of(request)
        if not session:
            return to_login()
        if not auth.csrf_ok(session, csrf):
            return back(f"/projects/{pid}", "csrf_failed")
        start_check(pid)
        return back(f"/projects/{pid}", "check_started")

    @app.post("/projects/{pid}/settings")
    async def settings(request: Request, pid: str):
        session = session_of(request)
        if not session:
            return to_login()
        form = await request.form()
        if not auth.csrf_ok(session, form.get("csrf")):
            return back(f"/projects/{pid}", "csrf_failed")
        project = registry.get(pid)
        try:
            for feature in FEATURES:
                if feature in _INCOMPATIBLE[project.type]:
                    continue
                project.set_feature(feature, form.get(f"f_{feature}") == "on")
            paths = [p.strip() for p in str(form.get("key_paths", "")).split(",") if p.strip()]
            if not paths or any(not p.startswith("/") for p in paths):
                raise ConfigError("Key pages must start with / (e.g. /, /contact)")
            project.key_paths = paths
            expected = str(form.get("expected_text", "")).strip()
            project.expected_text = expected or None
            if project.type is ProjectType.APPLICATION:
                hp = str(form.get("health_path", "")).strip()
                if hp and not hp.startswith("/"):
                    raise ConfigError("Health check address must start with / (e.g. /health)")
                if project.enabled("health_endpoint") and not hp:
                    raise ConfigError("Enter a health check address, or turn off the app health check")
                project.health_path = hp or None
            registry.save(project)
            store_for(pid).audit("dashboard", "settings_saved", features=project.features,
                                 key_paths=project.key_paths)
        except ConfigError as exc:
            return render(request, "project.html", session, **project_ctx(registry.get(pid)),
                          error=str(exc), status_code=400)
        return back(f"/projects/{pid}", "saved")

    @app.post("/projects/{pid}/credentials")
    def set_credential(request: Request, pid: str, csrf: str = Form(""), name: str = Form(""),
                       value: str = Form("")):
        session = session_of(request)
        if not session:
            return to_login()
        if not auth.csrf_ok(session, csrf):
            return back(f"/projects/{pid}", "csrf_failed")
        registry.get(pid)
        if not CRED_NAME_RE.match(name) or not value:
            return back(f"/projects/{pid}")
        vault.project(pid).set(name, value)
        store_for(pid).audit("dashboard", "credential_set", name=name)  # name only, never the value
        return back(f"/projects/{pid}", "saved")

    @app.post("/projects/{pid}/credentials/delete")
    def delete_credential(request: Request, pid: str, csrf: str = Form(""), name: str = Form("")):
        session = session_of(request)
        if not session:
            return to_login()
        if not auth.csrf_ok(session, csrf):
            return back(f"/projects/{pid}", "csrf_failed")
        vault.project(registry.get(pid).id).delete(name)
        store_for(pid).audit("dashboard", "credential_deleted", name=name)
        return back(f"/projects/{pid}", "deleted")

    @app.get("/approvals", response_class=HTMLResponse)
    def approvals_page(request: Request):
        session = session_of(request)
        if not session:
            return to_login()
        rows = []
        for p in registry.load_all():
            for a in ApprovalGate(store_for(p.id)).list(ApprovalStatus.PENDING):
                rows.append({"project": p, "approval": a})
        return render(request, "approvals.html", session, rows=rows)

    @app.post("/approvals/{pid}/{aid}")
    def decide(request: Request, pid: str, aid: str, csrf: str = Form(""), decision: str = Form("")):
        session = session_of(request)
        if not session:
            return to_login()
        if not auth.csrf_ok(session, csrf) or decision not in ("approve", "reject"):
            return back("/approvals", "csrf_failed")
        ApprovalGate(store_for(registry.get(pid).id)).decide(aid, approve=decision == "approve",
                                                              actor="owner:dashboard")
        return back("/approvals", "approved" if decision == "approve" else "rejected")

    @app.get("/incidents", response_class=HTMLResponse)
    def incidents_page(request: Request):
        session = session_of(request)
        if not session:
            return to_login()
        open_rows, closed_rows = [], []
        for p in registry.load_all():
            for inc in IncidentManager(store_for(p.id)).list(include_closed=True):
                row = {"project": p, "inc": inc,
                       "opened_at": time.strftime("%Y-%m-%d %H:%M", time.localtime(inc["opened"])),
                       "history": [h | {"at": time.strftime("%Y-%m-%d %H:%M", time.localtime(h["ts"]))}
                                   for h in inc["data"].get("history", [])]}
                (closed_rows if IncidentStatus(inc["status"]) in CLOSED else open_rows).append(row)
        sev = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        open_rows.sort(key=lambda r: (sev.get(r["inc"]["severity"], 9), -r["inc"]["opened"]))
        closed_rows.sort(key=lambda r: -r["inc"]["updated"])
        return render(request, "incidents.html", session, open_rows=open_rows, closed_rows=closed_rows[:30])

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        session = session_of(request)
        if not session:
            return to_login()
        # Only yes/no facts are computed here; no secret value reaches the template.
        system_names = vault.system().names()
        ai = {"enabled": ai_client is not None}
        if ai_client is not None:
            st = ai_client.status()
            ai.update(model=ai_client.cfg.model, ready=st.ready, message=st.message)
        try:
            auto = {"available": True, "status": autostart.status()}
        except autostart.AutostartError:
            auto = {"available": False, "status": ""}
        status = {
            "ai": ai,
            "telegram": "telegram_bot_token" in system_names and "telegram_default_chat_id" in system_names,
            "passphrase_stored": secrets_store.get_passphrase(home) is not None,
            "autostart": auto,
            "projects": len(registry.load_all()),
            "version": __version__,
            "data_dir": str(home),
        }
        return render(request, "settings.html", session, status=status)

    @app.get("/chat", response_class=HTMLResponse)
    def chat_page(request: Request):
        session = session_of(request)
        if not session:
            return to_login()
        return render(request, "chat.html", session)

    @app.post("/api/chat")
    async def chat_api(request: Request):
        session = session_of(request)
        if not session:
            return JSONResponse({"error": "login required"}, status_code=401)
        if not auth.csrf_ok(session, request.headers.get("x-csrf-token")):
            return JSONResponse({"error": "csrf"}, status_code=403)
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"error": "bad request"}, status_code=400)
        ctx = ChatContext(registry.load_all(), store_for, start_check, ai_agent)
        # The AI can take a minute on a laptop CPU: run it off the event loop so
        # the rest of the dashboard stays responsive.
        preferred = body.get("lang") if body.get("lang") in ("ml", "en") else None
        result = await run_in_threadpool(respond, str(body.get("message", "")), ctx, preferred)
        return JSONResponse(result)

    @app.get("/api/ai/status")
    def ai_status(request: Request):
        if not session_of(request):
            return JSONResponse({"error": "login required"}, status_code=401)
        if ai_client is None:
            return {"enabled": False, "ready": False, "model": None,
                    "message": "AI is turned off (guardian ai on)"}
        st = ai_client.status()
        return {"enabled": True, "ready": st.ready, "model": ai_client.cfg.model, "message": st.message}

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    app.state.ai_agent = ai_agent
    app.state.running = running
    app.state.start_check = start_check
    return app


def is_loopback(host: str) -> bool:
    return host in ("127.0.0.1", "::1", "localhost")
