"""SQLite-backed configuration store.

Every successful import / assignment / reset writes a new immutable
``config_version`` and atomically flips the singleton active pointer.
Decoding always reads the active version.  Failed assignments roll their
whole transaction back, so the previous configuration stays in effect.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .domain import Range, TemplateSpec

SCHEMA = """
CREATE TABLE IF NOT EXISTS config_versions (
    version    INTEGER PRIMARY KEY AUTOINCREMENT,
    reason     TEXT NOT NULL,
    created_at TEXT NOT NULL,
    active     INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_single_active
    ON config_versions(active) WHERE active = 1;

CREATE TABLE IF NOT EXISTS templates (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    version       INTEGER NOT NULL REFERENCES config_versions(version),
    ord           INTEGER NOT NULL,
    name          TEXT NOT NULL DEFAULT '',
    marker        TEXT NOT NULL,
    prefix        TEXT NOT NULL,
    scale_start   INTEGER NOT NULL,
    scale_length  INTEGER NOT NULL,
    weight_start  INTEGER NOT NULL,
    weight_length INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_templates_version ON templates(version);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_db_path() -> str:
    env = os.environ.get("DB_PATH")
    if env:
        return env
    data_dir = Path(__file__).resolve().parent.parent / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return str(data_dir / "bangdan.db")


class Storage:
    """Thin thread-safe wrapper around SQLite."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or default_db_path()
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False + RLock: FastAPI sync endpoints may run
        # in a worker thread; all access is serialised by the lock.
        self._conn = sqlite3.connect(
            self.path, check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        # Default rollback journal keeps all committed data in the single .db
        # file (no auxiliary -wal needed for inspection). Access is serialised
        # by the RLock below.
        self._conn.execute("PRAGMA journal_mode=DELETE;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def transaction(self):
        """Serialised BEGIN IMMEDIATE transaction with rollback on error."""
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                yield self._conn
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    # ------------------------------------------------------------------ read

    def active_version(self, conn: Optional[sqlite3.Connection] = None) -> Optional[int]:
        c = conn or self._conn
        row = c.execute(
            "SELECT version FROM config_versions WHERE active = 1 LIMIT 1"
        ).fetchone()
        return int(row["version"]) if row else None

    def get_version_meta(self, version: int) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM config_versions WHERE version = ?", (version,)
        ).fetchone()
        return dict(row) if row else None

    def list_versions(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT version, reason, created_at, active FROM config_versions "
            "ORDER BY version DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def templates_at(
        self, version: Optional[int] = None, conn: Optional[sqlite3.Connection] = None
    ) -> list[TemplateSpec]:
        c = conn or self._conn
        if version is None:
            version = self.active_version(c)
            if version is None:
                return []
        rows = c.execute(
            "SELECT * FROM templates WHERE version = ? ORDER BY ord, id",
            (version,),
        ).fetchall()
        return [self._row_to_spec(r) for r in rows]

    def active_config(self) -> tuple[Optional[int], list[TemplateSpec]]:
        with self._lock:
            version = self.active_version()
            return version, self.templates_at(version)

    @staticmethod
    def _row_to_spec(row: sqlite3.Row) -> TemplateSpec:
        return TemplateSpec(
            id=int(row["id"]),
            name=row["name"],
            marker=row["marker"],
            prefix=row["prefix"],
            scale=Range(int(row["scale_start"]), int(row["scale_length"])),
            weight=Range(int(row["weight_start"]), int(row["weight_length"])),
        )

    # ----------------------------------------------------------------- write

    def commit_new_config(
        self,
        templates: list[TemplateSpec],
        reason: str,
    ) -> int:
        """Atomically store ``templates`` as the new active version.

        All templates must carry a marker.  Returns the new version number.
        Any exception leaves the previous active version untouched.
        """
        with self.transaction() as conn:
            conn.execute("UPDATE config_versions SET active = 0 WHERE active = 1")
            cur = conn.execute(
                "INSERT INTO config_versions (reason, created_at, active) "
                "VALUES (?, ?, 1)",
                (reason, now_iso()),
            )
            version = int(cur.lastrowid)
            for ord_, t in enumerate(templates):
                if t.marker is None:
                    raise ValueError("refusing to persist template without marker")
                conn.execute(
                    "INSERT INTO templates (version, ord, name, marker, prefix, "
                    "scale_start, scale_length, weight_start, weight_length) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        version,
                        ord_,
                        t.name,
                        t.marker,
                        t.prefix,
                        t.scale.start,
                        t.scale.length,
                        t.weight.start,
                        t.weight.length,
                    ),
                )
            return version

    def reset_empty(self) -> int:
        return self.commit_new_config([], reason="reset")
