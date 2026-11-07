"""Minimal client for a local Ollama server (https://ollama.com)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from .config import AIConfig


class OllamaError(RuntimeError):
    pass


@dataclass
class OllamaStatus:
    reachable: bool
    model_installed: bool
    models: list[str]
    message: str

    @property
    def ready(self) -> bool:
        return self.reachable and self.model_installed


class OllamaClient:
    def __init__(self, cfg: AIConfig, client: httpx.Client | None = None):
        self.cfg = cfg.validate()
        self.http = client or httpx.Client(timeout=cfg.timeout_s)

    def status(self) -> OllamaStatus:
        try:
            resp = self.http.get(self.cfg.base_url + "/api/tags", timeout=5)
            resp.raise_for_status()
            models = [m.get("name", "") for m in resp.json().get("models", [])]
        except (httpx.HTTPError, ValueError) as exc:
            return OllamaStatus(False, False, [], f"Ollama is not running at {self.cfg.base_url} "
                                f"({type(exc).__name__}). Start the Ollama app, or run: ollama serve")
        wanted = self.cfg.model
        installed = any(m == wanted or m == f"{wanted}:latest" or m.split(":")[0] == wanted
                        for m in models)
        if not installed:
            return OllamaStatus(True, False, models,
                                f"Model {wanted} is not installed. Run: ollama pull {wanted}")
        return OllamaStatus(True, True, models, f"Ready: {wanted}")

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        body = {
            "model": self.cfg.model, "messages": messages, "tools": tools, "stream": False,
            "think": False,                       # faster answers on a laptop CPU
            "keep_alive": "15m",
            "options": {"temperature": 0.2, "num_ctx": 8192},
        }
        try:
            resp = self.http.post(self.cfg.base_url + "/api/chat", json=body, timeout=self.cfg.timeout_s)
        except httpx.TimeoutException as exc:
            raise OllamaError(f"The model took longer than {self.cfg.timeout_s:.0f}s") from exc
        except httpx.HTTPError as exc:
            raise OllamaError(f"Cannot reach Ollama: {type(exc).__name__}") from exc
        if resp.status_code >= 400:
            raise OllamaError(f"Ollama error HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            return resp.json()
        except ValueError as exc:
            raise OllamaError("Ollama returned invalid JSON") from exc
