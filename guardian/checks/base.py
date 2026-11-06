"""Check result model.

NOT_CHECKED is a first-class status: when the agent lacks access or a feature
is not implemented, it says so with a reason instead of reporting OK.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CheckStatus(str, Enum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    NOT_CHECKED = "not_checked"


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class CheckResult:
    name: str
    category: str
    status: CheckStatus
    summary: str
    severity: Severity = Severity.MEDIUM
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def problem(self) -> bool:
        return self.status in (CheckStatus.FAIL, CheckStatus.WARN)


def ok(name: str, category: str, summary: str, **detail: Any) -> CheckResult:
    return CheckResult(name, category, CheckStatus.OK, summary, Severity.LOW, detail)


def warn(name: str, category: str, summary: str, severity: Severity = Severity.MEDIUM,
         **detail: Any) -> CheckResult:
    return CheckResult(name, category, CheckStatus.WARN, summary, severity, detail)


def fail(name: str, category: str, summary: str, severity: Severity = Severity.HIGH,
         **detail: Any) -> CheckResult:
    return CheckResult(name, category, CheckStatus.FAIL, summary, severity, detail)


def not_checked(name: str, category: str, reason: str, **detail: Any) -> CheckResult:
    return CheckResult(name, category, CheckStatus.NOT_CHECKED, reason, Severity.LOW, detail)
