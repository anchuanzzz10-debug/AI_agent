"""Project configuration model and validation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class ProjectType(str, Enum):
    WORDPRESS = "wordpress"
    WEBSITE = "website"          # non-WordPress website
    APPLICATION = "application"  # custom software application


# Every feature a project can switch on or off. Defaults depend on project type.
FEATURES = (
    "uptime",            # reachability, status code, response time, expected text
    "ssl",               # certificate validity / expiry
    "error_detection",   # visible error symptoms (500, critical-error pages, DB errors)
    "wordpress",         # WordPress REST / connector inspection
    "updates",           # core / plugin / theme update monitoring
    "security",          # security checks
    "seo",               # SEO audits, indexing, keyword data, SEO reports
    "health_endpoint",   # application health endpoint
    "backups",           # backups before changes
    "reports",           # periodic reports
    "voice_reports",     # spoken (Malayalam) summaries
)

_DEFAULTS: dict[ProjectType, dict[str, bool]] = {
    ProjectType.WORDPRESS: {
        "uptime": True, "ssl": True, "error_detection": True, "wordpress": True,
        "updates": True, "security": True, "seo": False, "health_endpoint": False,
        "backups": True, "reports": True, "voice_reports": False,
    },
    ProjectType.WEBSITE: {
        "uptime": True, "ssl": True, "error_detection": True, "wordpress": False,
        "updates": False, "security": True, "seo": False, "health_endpoint": False,
        "backups": False, "reports": True, "voice_reports": False,
    },
    ProjectType.APPLICATION: {
        "uptime": True, "ssl": True, "error_detection": True, "wordpress": False,
        "updates": False, "security": True, "seo": False, "health_endpoint": True,
        "backups": False, "reports": True, "voice_reports": False,
    },
}

# Features that make no sense for a project type and are refused.
_INCOMPATIBLE: dict[ProjectType, set[str]] = {
    ProjectType.WORDPRESS: {"health_endpoint"},
    ProjectType.WEBSITE: {"wordpress"},
    ProjectType.APPLICATION: {"wordpress"},
}

PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")


class ConfigError(ValueError):
    pass


def default_features(ptype: ProjectType) -> dict[str, bool]:
    return dict(_DEFAULTS[ptype])


@dataclass
class ProjectConfig:
    id: str
    name: str
    type: ProjectType
    url: str
    features: dict[str, bool] = field(default_factory=dict)
    # Pages that must keep working (checked after every change).
    key_paths: list[str] = field(default_factory=lambda: ["/"])
    # Text expected on the homepage; its absence is an incident.
    expected_text: str | None = None
    health_path: str | None = None          # application health endpoint
    alert_chat_id: str | None = None        # per-project Telegram chat
    language: str = "ml+en"                 # report language: ml, en, ml+en
    thresholds: dict[str, float] = field(default_factory=lambda: {
        "response_time_warn_s": 3.0,
        "ssl_warn_days": 14,
    })

    def __post_init__(self) -> None:
        if not PROJECT_ID_RE.match(self.id):
            raise ConfigError(
                f"Invalid project id {self.id!r}: use lowercase letters, digits and hyphens.")
        if isinstance(self.type, str):
            try:
                self.type = ProjectType(self.type)
            except ValueError as exc:
                raise ConfigError(f"Unknown project type {self.type!r}") from exc
        if not self.url.startswith(("https://", "http://")):
            raise ConfigError(f"Project {self.id}: url must start with https:// or http://")
        self.url = self.url.rstrip("/")
        merged = default_features(self.type)
        for name, value in (self.features or {}).items():
            if name not in FEATURES:
                raise ConfigError(f"Project {self.id}: unknown feature {name!r}")
            merged[name] = bool(value)
        for name in _INCOMPATIBLE[self.type]:
            if merged.get(name):
                raise ConfigError(
                    f"Project {self.id}: feature {name!r} is not available for type {self.type.value}")
        self.features = merged
        if self.type is ProjectType.APPLICATION and self.features["health_endpoint"] and not self.health_path:
            self.health_path = "/health"

    def enabled(self, feature: str) -> bool:
        if feature not in FEATURES:
            raise ConfigError(f"Unknown feature {feature!r}")
        return self.features.get(feature, False)

    def set_feature(self, feature: str, value: bool) -> None:
        if feature not in FEATURES:
            raise ConfigError(f"Unknown feature {feature!r}")
        if value and feature in _INCOMPATIBLE[self.type]:
            raise ConfigError(
                f"Feature {feature!r} is not available for type {self.type.value}")
        self.features[feature] = value

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["type"] = self.type.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProjectConfig":
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(data) - known
        if unknown:
            raise ConfigError(f"Unknown config keys: {sorted(unknown)}")
        return cls(**data)
