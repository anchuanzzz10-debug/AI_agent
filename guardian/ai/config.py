"""AI settings stored in data/system/ai.json."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_MODEL = "qwen3:8b"
DEFAULT_URL = "http://127.0.0.1:11434"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class AIConfigError(ValueError):
    pass


@dataclass
class AIConfig:
    enabled: bool = True
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_URL
    timeout_s: float = 180.0       # CPU inference on a laptop is slow
    max_tool_rounds: int = 4

    def validate(self) -> "AIConfig":
        host = urlparse(self.base_url).hostname
        if host not in LOOPBACK:
            raise AIConfigError("The AI model server must run on this computer "
                                f"(127.0.0.1); refusing {self.base_url}")
        if not self.model or len(self.model) > 100:
            raise AIConfigError("Invalid model name")
        return self


def _path(home: Path) -> Path:
    return Path(home) / "system" / "ai.json"


def load(home: Path) -> AIConfig:
    cfg = AIConfig()
    path = _path(home)
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        cfg = AIConfig(**{k: v for k, v in data.items() if k in AIConfig.__dataclass_fields__})
    if os.environ.get("GUARDIAN_AI_MODEL"):
        cfg.model = os.environ["GUARDIAN_AI_MODEL"]
    return cfg.validate()


def save(home: Path, cfg: AIConfig) -> None:
    cfg.validate()
    path = _path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(cfg), indent=2), encoding="utf-8")
