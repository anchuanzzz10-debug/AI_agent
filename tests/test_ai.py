"""AI brain tests against a simulated Ollama server (scripted model replies).

These verify tool handling and the safety rules enforced in code. They do NOT
measure the real model's answer quality, which must be tried on the laptop.
"""

import json
import re

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import make_project
from guardian import cli
from guardian.ai import config as ai_config
from guardian.ai.agent import TOOL_NAMES, Agent
from guardian.ai.config import AIConfig, AIConfigError
from guardian.ai.ollama import OllamaClient
from guardian.approvals import ApprovalGate
from guardian.checks.base import fail, not_checked, ok, warn
from guardian.dashboard import auth
from guardian.dashboard.app import create_app
from guardian.registry import Registry
from guardian.storage import ProjectStore


class FakeOllama:
    def __init__(self, replies=None, models=("qwen3:8b",), down=False):
        self.replies = list(replies or [])
        self.models = list(models)
        self.down = down
        self.requests: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("connection refused")
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": m} for m in self.models]})
        if request.url.path == "/api/chat":
            body = json.loads(request.content)
            self.requests.append(body)
            reply = self.replies.pop(0) if self.replies else {"content": "done"}
            return httpx.Response(200, json={"message": {"role": "assistant", **reply}, "done": True})
        return httpx.Response(404)

    def client(self, cfg=None) -> OllamaClient:
        return OllamaClient(cfg or AIConfig(), httpx.Client(transport=httpx.MockTransport(self.handler)))


def call(name, **args):
    return {"tool_calls": [{"function": {"name": name, "arguments": args}}], "content": ""}


@pytest.fixture
def world(home, tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    reg = Registry(config)
    reg.add(make_project("connectdocsmedia", "website", seo=True))
    store = ProjectStore(home, "connectdocsmedia")
    for r in (ok("uptime:/", "uptime", "HTTP 200 in 0.4s"),
              warn("security:headers", "security", "Missing security headers: x-content-type-options"),
              ok("seo:title", "seo", "Title present (47 chars)"),
              not_checked("seo:indexing", "seo", "Search Console integration is not implemented yet")):
        store.record_check("r1", r)
    checks = []

    def start_check(pid):
        checks.append(pid)
        return True
    return {"home": home, "config": config, "reg": reg, "checks": checks,
            "agent": lambda fake: Agent(fake.client(), reg.load_all, lambda pid: ProjectStore(home, pid),
                                        start_check)}


# ---------------- configuration & status ----------------

def test_model_server_must_be_local(home):
    with pytest.raises(AIConfigError):
        AIConfig(base_url="http://evil.example:11434").validate()
    with pytest.raises(AIConfigError):
        ai_config.save(home, AIConfig(base_url="https://api.example.com"))
    ai_config.save(home, AIConfig(model="qwen3:4b"))
    assert ai_config.load(home).model == "qwen3:4b"


def test_status_messages():
    assert FakeOllama().client().status().ready
    missing = FakeOllama(models=["llama3.2:3b"]).client().status()
    assert not missing.ready and "ollama pull qwen3:8b" in missing.message
    down = FakeOllama(down=True).client().status()
    assert not down.reachable and "ollama serve" in down.message


# ---------------- agent loop ----------------

def test_answers_from_tool_results(world):
    fake = FakeOllama([call("get_project_status", project_id="connectdocsmedia"),
                       {"content": "<think>hmm</think>One warning: missing x-content-type-options header."}])
    reply = world["agent"](fake).answer("is my site ok?", "en")
    assert reply.ok and reply.tools_used == ["get_project_status"]
    assert reply.text == "One warning: missing x-content-type-options header."   # think tags stripped
    tool_msg = fake.requests[1]["messages"][-1]
    assert tool_msg["role"] == "tool"
    data = json.loads(tool_msg["content"])
    assert data["warnings"] == ["security:headers: Missing security headers: x-content-type-options"]
    assert data["not_checked"] == ["seo:indexing: Search Console integration is not implemented yet"]


def test_request_is_grounded_and_local(world):
    fake = FakeOllama([{"content": "ok"}])
    world["agent"](fake).answer("hello", "ml")
    body = fake.requests[0]
    system = body["messages"][0]["content"]
    assert "Answer ONLY from tool results" in system and "Malayalam" in system
    assert "connectdocsmedia" in system
    assert {t["function"]["name"] for t in body["tools"]} == TOOL_NAMES
    assert body["stream"] is False


def test_no_dangerous_tools_exist():
    for forbidden in ("approve", "reject", "apply_change", "get_credentials", "set_credential", "delete"):
        assert not any(forbidden in n for n in TOOL_NAMES)


def test_model_cannot_approve(world):
    store = ProjectStore(world["home"], "connectdocsmedia")
    req = ApprovalGate(store).request({"project": "connectdocsmedia", "type": "t"}, "r", "low")
    fake = FakeOllama([call("approve", approval_id=req["id"]), {"content": "I can't approve."}])
    reply = world["agent"](fake).answer(f"approve {req['id']}", "en")
    assert ApprovalGate(store).list()[0]["status"] == "pending"
    err = json.loads(fake.requests[1]["messages"][-1]["content"])
    assert "does not exist" in err["error"] and reply.tools_used == []


def test_invalid_project_is_reported_to_model(world):
    fake = FakeOllama([call("get_project_status", project_id="../../etc"), {"content": "Unknown project."}])
    world["agent"](fake).answer("status of x", "en")
    assert "Unknown project_id" in json.loads(fake.requests[1]["messages"][-1]["content"])["error"]


def test_string_arguments_and_start_check(world):
    fake = FakeOllama([{"tool_calls": [{"function": {"name": "start_check",
                                                      "arguments": '{"project_id": "connectdocsmedia"}'}}]},
                       {"content": "Started."}])
    reply = world["agent"](fake).answer("check my site now please", "en")
    assert world["checks"] == ["connectdocsmedia"] and reply.tools_used == ["start_check"]


def test_seo_tool_respects_seo_switch(world):
    world["reg"].set_feature("connectdocsmedia", "seo", False)
    fake = FakeOllama([call("get_seo_results", project_id="connectdocsmedia"), {"content": "SEO is off."}])
    world["agent"](fake).answer("seo?", "en")
    assert "turned off" in fake.requests[1]["messages"][-1]["content"]


def test_tool_rounds_are_capped(world):
    fake = FakeOllama([call("list_projects")] * 20)
    reply = world["agent"](fake).answer("loop forever", "en")
    assert not reply.ok and len(fake.requests) == AIConfig().max_tool_rounds + 1


def test_model_down_is_reported_honestly(world):
    reply = world["agent"](FakeOllama(down=True)).answer("anything", "ml")
    assert not reply.ok and "AI മോഡൽ ലഭ്യമല്ല" in reply.text


def test_one_question_at_a_time(world):
    agent = world["agent"](FakeOllama([{"content": "x"}]))
    agent._busy.acquire()
    try:
        assert "still answering" in agent.answer("q", "en").text
    finally:
        agent._busy.release()


# ---------------- dashboard + CLI ----------------

@pytest.fixture
def dash(world, vault):
    auth.set_password(world["home"], "dashboard-pass-123")
    fake = FakeOllama([call("list_incidents"), {"content": "No open incidents."}])
    app = create_app(world["home"], world["config"], vault, check_runner=lambda p: None,
                     ai_client=fake.client())
    c = TestClient(app, follow_redirects=False)
    c.cookies.set("lang", "en")
    c.post("/login", data={"password": "dashboard-pass-123"})
    token = re.search(r'name="csrf" value="([^"]+)"', c.get("/chat").text).group(1)
    return c, token, fake


def test_dashboard_routes_free_questions_to_ai(dash):
    c, token, fake = dash
    r = c.post("/api/chat", json={"message": "anything I should worry about today?"},
               headers={"X-CSRF-Token": token}).json()
    assert r["source"] == "ai" and r["reply"] == "No open incidents." and r["tools"] == ["list_incidents"]


def test_dashboard_commands_still_skip_ai(dash):
    c, token, fake = dash
    r = c.post("/api/chat", json={"message": "report"}, headers={"X-CSRF-Token": token}).json()
    assert r["source"] == "commands" and fake.requests == []


def test_dashboard_ai_status(dash):
    c, _, _ = dash
    st = c.get("/api/ai/status").json()
    assert st == {"enabled": True, "ready": True, "model": "qwen3:8b", "message": "Ready: qwen3:8b"}


def test_cli_ai_settings(home, monkeypatch, capsys):
    monkeypatch.setenv("GUARDIAN_HOME", str(home))
    assert cli.main(["ai", "model", "qwen3:4b"]) == 0
    assert cli.main(["ai", "off"]) == 0
    cfg = ai_config.load(home)
    assert cfg.model == "qwen3:4b" and cfg.enabled is False
    out = capsys.readouterr().out
    assert "ollama pull qwen3:4b" in out


def test_dashboard_stays_responsive_while_ai_thinks(world, vault):
    """Real uvicorn server: while one AI answer is in progress, other pages must load."""
    import socket
    import threading
    import time as _time
    import uvicorn
    gate = threading.Event()

    class SlowOllama(FakeOllama):
        def handler(self, request):
            if request.url.path == "/api/chat":
                gate.wait(10)
            return super().handler(request)
    auth.set_password(world["home"], "dashboard-pass-123")
    app = create_app(world["home"], world["config"], vault, check_runner=lambda p: None,
                     ai_client=SlowOllama([{"content": "slow answer"}]).client())
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        _time.sleep(0.05)
    base = f"http://127.0.0.1:{port}"
    try:
        with httpx.Client(base_url=base, timeout=15, trust_env=False) as c:
            c.post("/login", data={"password": "dashboard-pass-123"})
            token = re.search(r'name="csrf" value="([^"]+)"', c.get("/chat").text).group(1)
            cookies = dict(c.cookies)
        answer = {}

        def ask():
            with httpx.Client(base_url=base, timeout=15, trust_env=False, cookies=cookies) as c2:
                answer.update(c2.post("/api/chat", json={"message": "a free question"},
                                      headers={"X-CSRF-Token": token}).json())
        t = threading.Thread(target=ask)
        t.start()
        _time.sleep(0.5)                                  # AI request is now blocked "thinking"
        with httpx.Client(base_url=base, timeout=3, trust_env=False, cookies=cookies) as c3:
            assert c3.get("/").status_code == 200         # would time out if the event loop were blocked
        gate.set()
        t.join(10)
        assert answer["reply"] == "slow answer"
    finally:
        gate.set()
        server.should_exit = True
