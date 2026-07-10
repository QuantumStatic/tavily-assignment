from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from vendor_dd.logs import get_logger

_LOG = get_logger("db")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid_hex() -> str:
    return uuid.uuid4().hex


@dataclass
class Project:
    id: int
    name: str
    created_at: str
    session_id: str | None = None


@dataclass
class Vendor:
    id: int
    project_id: int
    name: str
    vendor_key: str | None
    created_at: str


class Store:
    """Projects + vendors persistence. Shares its SQLite file with the report cache."""

    def __init__(self, path: str | Path, clock: Callable[[], str] = _utcnow_iso,
                 id_gen: Callable[[], str] = _uuid_hex):
        self._clock = clock
        self._id_gen = id_gen
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._exec(
            """CREATE TABLE IF NOT EXISTS projects (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 name TEXT NOT NULL,
                 created_at TEXT NOT NULL,
                 session_id TEXT
               )"""
        )
        # Migrate a DB created before session_id existed (Phase 2). No-op on fresh DBs.
        cols = {row[1] for row in self._exec("PRAGMA table_info(projects)").fetchall()}
        if "session_id" not in cols:
            self._exec("ALTER TABLE projects ADD COLUMN session_id TEXT")
        self._exec(
            """CREATE TABLE IF NOT EXISTS vendors (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 project_id INTEGER NOT NULL,
                 name TEXT NOT NULL,
                 vendor_key TEXT,
                 created_at TEXT NOT NULL,
                 FOREIGN KEY (project_id) REFERENCES projects(id)
               )"""
        )
        self._conn.commit()

    def _exec(self, sql: str, params: tuple = ()):
        cur = self._conn.execute(sql, params)
        _LOG.info("db.query", extra={"payload": {
            "sql": " ".join(sql.split()), "params": list(params),
            "rowcount": cur.rowcount, "lastrowid": cur.lastrowid,
        }})
        return cur

    def create_project(self, name: str) -> Project:
        ts = self._clock()
        sid = self._id_gen()
        cur = self._exec(
            "INSERT INTO projects (name, created_at, session_id) VALUES (?,?,?)",
            (name, ts, sid))
        self._conn.commit()
        return Project(id=cur.lastrowid, name=name, created_at=ts, session_id=sid)

    def list_projects(self) -> list[Project]:
        cur = self._exec(
            "SELECT id, name, created_at, session_id FROM projects ORDER BY id")
        return [Project(*row) for row in cur.fetchall()]

    def get_project(self, project_id: int) -> Project | None:
        cur = self._exec(
            "SELECT id, name, created_at, session_id FROM projects WHERE id=?", (project_id,))
        row = cur.fetchone()
        return Project(*row) if row else None

    def add_vendor(self, project_id: int, name: str) -> Vendor:
        ts = self._clock()
        cur = self._exec(
            "INSERT INTO vendors (project_id, name, vendor_key, created_at) VALUES (?,?,?,?)",
            (project_id, name, None, ts))
        self._conn.commit()
        return Vendor(id=cur.lastrowid, project_id=project_id, name=name,
                      vendor_key=None, created_at=ts)

    def list_vendors(self, project_id: int) -> list[Vendor]:
        cur = self._exec(
            "SELECT id, project_id, name, vendor_key, created_at FROM vendors "
            "WHERE project_id=? ORDER BY id", (project_id,))
        return [Vendor(*row) for row in cur.fetchall()]

    def get_vendor(self, vendor_id: int) -> Vendor | None:
        cur = self._exec(
            "SELECT id, project_id, name, vendor_key, created_at FROM vendors WHERE id=?",
            (vendor_id,))
        row = cur.fetchone()
        return Vendor(*row) if row else None

    def remove_vendor(self, vendor_id: int) -> None:
        row = self._exec(
            "SELECT name, vendor_key FROM vendors WHERE id=?", (vendor_id,)).fetchone()
        self._exec("DELETE FROM vendors WHERE id=?", (vendor_id,))
        if row is not None:
            self._clear_cache(*row)
        self._conn.commit()

    def _clear_cache(self, name: str, vendor_key: str | None) -> None:
        """Evict a deleted vendor's cached research so a re-add re-runs fresh. The cache
        is keyed by domain and SHARED across projects, so only evict a key if no other
        vendor still references it (same name for the snapshot key, same domain for the
        section key) — otherwise a delete in one project would wipe another's report."""
        exists = self._exec(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='report_cache'").fetchone()
        if not exists:   # no report has run yet -> nothing cached
            return
        keys = {name.strip().lower()}
        if vendor_key:
            keys.add(vendor_key.strip().lower())
        for key in keys:
            # the target vendor row is already deleted, so any hit here is another vendor
            still_referenced = self._exec(
                "SELECT 1 FROM vendors WHERE LOWER(name)=? OR LOWER(vendor_key)=? LIMIT 1",
                (key, key)).fetchone()
            if still_referenced:
                continue
            self._exec("DELETE FROM report_cache WHERE vendor_key=?", (key,))

    def set_vendor_key(self, vendor_id: int, vendor_key: str) -> None:
        self._exec(
            "UPDATE vendors SET vendor_key=? WHERE id=?", (vendor_key, vendor_id))
        self._conn.commit()
