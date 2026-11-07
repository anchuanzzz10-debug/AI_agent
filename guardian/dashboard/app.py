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

from ..approvals import ApprovalGate, ApprovalStatus
from ..incidents import IncidentManager
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
FLASH_KEYS = {"saved", "created", "deleted", "approved", "rejected", "check_started", "csrf_failed"}

CheckRunner = Callable[[ProjectConfig], None]


def default_check_runner(home: Path, vault: Vault) -> CheckRunner:
    def run(project: ProjectConfig) -> None:
        with httpx.Client(timeout=20, headers={"User-Agent": "GuardianMonitor/0.3"}) as client, \
                httpx.Client(timeout=20) as wporg:
            Monitor(project, ProjectStore(home, project.id), vault.project(project.id), client,
                    Notifier(vault.system()), confirm_delay_s=45, wporg_client=wporg,
                    connectivity=lambda: internet_available(client)).run()
    return run


def create_app(home: Path, config_dir: Path, vault: Vault,
               check_runner: CheckRunner | None = None, sessions: auth.SessionStore | None = None) -> FastAPI:
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

    def summarize(project: ProjectConfig) -> dict:
        store = store_for(project.id)
        run_id, ts, results = store.latest_run()
        if not project.enabled("seo"):
            results = [r for r in results if r["category"] != "seo"]
        counts = {s: sum(r["status"] == s for r in results) for s in ("fail", "warn", "ok", "not_checked")}
        return {"project": project, "run_id": run_id,
                "checked_at": time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else None,
                "results": results, "counts": counts, "running": project.id in running,
                "incidents": IncidentManager(store).list(),
                "pending": ApprovalGate(store).list(ApprovalStatus.PENDING)}

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
        return render(request, "overview.html", session, cards=cards)

    @app.get("/projects/new", response_class=HTMLResponse)
    def new_project_page(request: Request):
        session = session_of(request)
        if not session:
            return to_login()
        return render(request, "project_new.html", session, form={})

    @app.post("/projects/new")
    def create_project(request: Request, csrf: str = Form(""), id: str = Form(""), name: str = Form(""),
                       type: str = Form("wordpress"), url: str = Form(""), seo: str = Form("")):
        session = session_of(request)
        if not session:
            return to_login()
        if not auth.csrf_ok(session, csrf):
            return back("/projects/new", "csrf_failed")
        form = {"id": id.strip(), "name": name.strip(), "type": type, "url": url.strip(), "seo": bool(seo)}
        try:
            project = ProjectConfig(id=form["id"], name=form["name"] or form["id"], type=form["type"],
                                    url=form["url"], features={"seo": form["seo"]})
            registry.add(project)
            store_for(project.id).audit("dashboard", "project_created", features=project.features)
        except ConfigError as exc:
            return render(request, "project_new.html", session, form=form, error=str(exc), status_code=400)
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
        allowed = [f for f in FEATURES if f not in _INCOMPATIBLE[project.type]]
        return render(request, "project.html", session, s=summarize(project), allowed=allowed,
                      cred_names=vault.project(pid).names())

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
            registry.save(project)
            store_for(pid).audit("dashboard", "settings_saved", features=project.features,
                                 key_paths=project.key_paths)
        except ConfigError as exc:
            allowed = [f for f in FEATURES if f not in _INCOMPATIBLE[project.type]]
            return render(request, "project.html", session, s=summarize(registry.get(pid)), allowed=allowed,
                          cred_names=vault.project(pid).names(), error=str(exc), status_code=400)
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
        ctx = ChatContext(registry.load_all(), store_for, start_check)
        return JSONResponse(respond(str(body.get("message", "")), ctx))

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    app.state.running = running
    app.state.start_check = start_check
    return app


def is_loopback(host: str) -> bool:
    return host in ("127.0.0.1", "::1", "localhost")
