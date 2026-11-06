"""TLS certificate expiry check."""

from __future__ import annotations

import socket
import ssl
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urlparse

from ..models import ProjectConfig
from .base import CheckResult, Severity, fail, not_checked, ok, warn

CertFetcher = Callable[[str, int], datetime]


def fetch_cert_expiry(host: str, port: int = 443, timeout: float = 10.0) -> datetime:
    context = ssl.create_default_context()
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with context.wrap_socket(sock, server_hostname=host) as tls:
            cert = tls.getpeercert()
    expires = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z")
    return expires.replace(tzinfo=timezone.utc)


def ssl_checks(project: ProjectConfig, fetcher: CertFetcher = fetch_cert_expiry,
               now: datetime | None = None) -> list[CheckResult]:
    parsed = urlparse(project.url)
    if parsed.scheme != "https":
        return [fail("ssl", "ssl", "Site is not served over HTTPS", Severity.HIGH)]
    now = now or datetime.now(timezone.utc)
    try:
        expires = fetcher(parsed.hostname, parsed.port or 443)
    except ssl.SSLCertVerificationError as exc:
        return [fail("ssl", "ssl", f"Certificate invalid: {exc.verify_message}", Severity.CRITICAL)]
    except (OSError, ssl.SSLError) as exc:
        return [not_checked("ssl", "ssl", f"Could not connect to check certificate: {exc}")]
    days = (expires - now).days
    warn_days = int(project.thresholds.get("ssl_warn_days", 14))
    if days < 0:
        return [fail("ssl", "ssl", f"Certificate EXPIRED {-days} day(s) ago", Severity.CRITICAL,
                     expires=expires.isoformat())]
    if days <= 3:
        return [fail("ssl", "ssl", f"Certificate expires in {days} day(s)", Severity.HIGH,
                     expires=expires.isoformat())]
    if days <= warn_days:
        return [warn("ssl", "ssl", f"Certificate expires in {days} day(s)", Severity.MEDIUM,
                     expires=expires.isoformat())]
    return [ok("ssl", "ssl", f"Certificate valid for {days} more days", expires=expires.isoformat())]
