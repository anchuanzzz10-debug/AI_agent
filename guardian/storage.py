"""Per-project storage. Every project gets its own directory and its own SQLite
database; nothing is shared between projects.

Layout under GUARDIAN_HOME (default ./data):
    system/                     vault salt + verifier, system secrets
    projects/<id>/
        credentials.enc         encrypted with this project's key only
        guardian.db             checks, incidents, approvals, audit log
        logs/guardian.log
        reports/
        backups/
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from .models import PROJECT_ID_RE, ConfigError

SCHEMA = """
CREATE TABLE IF NOT EXISTS check_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    run_id TEXT NOT NULL,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    status TEXT NOT NULL,
    summary TEXT NOT NULL,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS incidents (
    id TEXT PRIMARY KEY,
    opened REAL NOT NULL,
    updated REAL NOT NULL,
    check_name TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL,
    title TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    created REAL NOT NULL,
    expires REAL NOT NULL,
    status TEXT NOT NULL,
    action TEXT NOT NULL,
    digest TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    actor TEXT NOT NULL,
    event TEXT NOT NULL,
    data TEXT NOT NULL
);
"""


def guardian_home(path: str | os.PathLike | None = None) -> Path:
    root = Path(path or os.environ.get("GUARDIAN_HOME", "data")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_project_dir(home: Path, project_id: str) -> Path:
    if not PROJECT_ID_RE.match(project_id):
        raise ConfigError(f"Invalid project id {project_id!r}")
    base = (home / "projects").resolve()
    target = (base / project_id).resolve()
    if target.parent != base:  # defence in depth against traversal
        raise ConfigError(f"Project path escapes storage root: {project_id!r}")
    return target


class ProjectStore:
    """Storage bound to exactly one project. It cannot address other projects."""

    def __init__(self, home: Path, project_id: str):
        self.project_id = project_id
        self.dir = _safe_project_dir(home, project_id)
        for sub in ("logs", "reports", "backups"):
            (self.dir / sub).mkdir(parents=True, exist_ok=True)
        self.db_path = self.dir / "guardian.db"
        with self._conn() as conn:
            conn.executescript(SCHEMA)
        self.log = self._make_logger()

    # -- infrastructure -------------------------------------------------
    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _make_logger(self) -> logging.Logger:
        logger = logging.getLogger(f"guardian.project.{self.project_id}.{id(self)}")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        if not logger.handlers:
            handler = logging.FileHandler(self.dir / "logs" / "guardian.log", encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logger.addHandler(handler)
        return logger

    def path(self, *parts: str) -> Path:
        """Resolve a path inside this project's directory only."""
        target = self.dir.joinpath(*parts).resolve()
        if self.dir not in target.parents and target != self.dir:
            raise ConfigError("Path escapes project directory")
        return target

    # -- audit ----------------------------------------------------------
    def audit(self, actor: str, event: str, **data: Any) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO audit (ts, actor, event, data) VALUES (?, ?, ?, ?)",
                (time.time(), actor, event, json.dumps(data, default=str)),
            )
        self.log.info("%s %s %s", actor, event, json.dumps(data, default=str))

    def audit_entries(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM audit ORDER BY id").fetchall()
        return [dict(r) | {"data": json.loads(r["data"])} for r in rows]

    # -- check results --------------------------------------------------
    def record_check(self, run_id: str, result: "Any") -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO check_results (ts, run_id, name, category, status, summary, detail)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (time.time(), run_id, result.name, result.category, result.status.value,
                 result.summary, json.dumps(result.detail, default=str)),
            )

    def check_results(self, run_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM check_results"
        args: tuple = ()
        if run_id:
            sql += " WHERE run_id = ?"
            args = (run_id,)
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql + " ORDER BY id", args).fetchall()]

    def latest_run(self) -> tuple[str | None, float | None, list[dict[str, Any]]]:
        """(run_id, timestamp, results) of the most recent check run."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT run_id, ts FROM check_results ORDER BY id DESC LIMIT 1").fetchone()
            if row is None:
                return None, None, []
            rows = conn.execute("SELECT * FROM check_results WHERE run_id = ? ORDER BY id",
                                (row["run_id"],)).fetchall()
        return row["run_id"], row["ts"], [dict(r) for r in rows]

    # -- generic row access used by incidents / approvals ---------------
    def upsert(self, table: str, row: dict[str, Any]) -> None:
        if table not in {"incidents", "approvals"}:
            raise ValueError(table)
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        with self._conn() as conn:
            conn.execute(f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({marks})",
                         tuple(row.values()))

    def fetch(self, table: str, where: str = "", args: tuple = ()) -> list[dict[str, Any]]:
        if table not in {"incidents", "approvals"}:
            raise ValueError(table)
        sql = f"SELECT * FROM {table}" + (f" WHERE {where}" if where else "")
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]
