from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

from .util import now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    url         TEXT NOT NULL,
    video_id    TEXT,
    title       TEXT,
    duration    REAL,
    path        TEXT,
    status      TEXT NOT NULL DEFAULT 'new',      -- new|processing|done|failed
    attempts    INTEGER NOT NULL DEFAULT 0,
    error       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidates (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id   INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    start       REAL NOT NULL,
    end         REAL NOT NULL,
    ai_score    REAL,
    final_score REAL,
    title       TEXT,
    hook        TEXT,
    description TEXT,
    hashtags    TEXT,
    scores_json TEXT,
    reason      TEXT,
    status      TEXT NOT NULL DEFAULT 'selected',  -- selected|rendered|rejected|failed
    error       TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clips (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER REFERENCES candidates(id) ON DELETE SET NULL,
    source_id    INTEGER REFERENCES sources(id) ON DELETE SET NULL,
    path         TEXT NOT NULL,
    meta_path    TEXT,
    thumb_path   TEXT,
    duration     REAL,
    width        INTEGER,
    height       INTEGER,
    score        REAL,
    title        TEXT,
    description  TEXT,
    hashtags     TEXT,
    status       TEXT NOT NULL DEFAULT 'ready',     -- ready|scheduled|posted|failed|skipped
    attempts     INTEGER NOT NULL DEFAULT 0,
    youtube_id   TEXT,
    error        TEXT,
    created_at   TEXT NOT NULL,
    posted_at    TEXT
);

CREATE TABLE IF NOT EXISTS posts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_id     INTEGER REFERENCES clips(id) ON DELETE SET NULL,
    platform    TEXT NOT NULL DEFAULT 'youtube',
    external_id TEXT,
    url         TEXT,
    status      TEXT NOT NULL,
    detail      TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    level      TEXT NOT NULL,
    message    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_clips_status ON clips(status, score DESC);
CREATE INDEX IF NOT EXISTS idx_sources_status ON sources(status);
CREATE INDEX IF NOT EXISTS idx_posts_created ON posts(created_at);
"""


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.commit()

    # -- plumbing ------------------------------------------------------
    def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- events --------------------------------------------------------
    def event(self, level: str, message: str) -> None:
        self.execute(
            "INSERT INTO events (created_at, level, message) VALUES (?,?,?)",
            (now_iso(), level, message[:4000]),
        )

    # -- sources -------------------------------------------------------
    def add_source(self, url: str) -> int:
        existing = self.one("SELECT id FROM sources WHERE url = ? AND status IN ('new','processing','done')", (url,))
        if existing:
            return int(existing["id"])
        ts = now_iso()
        return self.execute(
            "INSERT INTO sources (url, status, created_at, updated_at) VALUES (?,?,?,?)",
            (url, "new", ts, ts),
        )

    def update_source(self, source_id: int, **fields) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        params = list(fields.values()) + [now_iso(), source_id]
        self.execute(f"UPDATE sources SET {sets}, updated_at = ? WHERE id = ?", params)

    def next_source(self) -> dict | None:
        return self.one(
            "SELECT * FROM sources WHERE status = 'new' OR (status = 'failed' AND attempts < 3) "
            "ORDER BY (status = 'failed'), id LIMIT 1"
        )

    def source(self, source_id: int) -> dict | None:
        return self.one("SELECT * FROM sources WHERE id = ?", (source_id,))

    # -- candidates ----------------------------------------------------
    def add_candidate(self, **fields) -> int:
        fields.setdefault("created_at", now_iso())
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        return self.execute(f"INSERT INTO candidates ({cols}) VALUES ({marks})", fields.values())

    def update_candidate(self, candidate_id: int, **fields) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.execute(f"UPDATE candidates SET {sets} WHERE id = ?", list(fields.values()) + [candidate_id])

    # -- clips ---------------------------------------------------------
    def add_clip(self, **fields) -> int:
        fields.setdefault("created_at", now_iso())
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        return self.execute(f"INSERT INTO clips ({cols}) VALUES ({marks})", fields.values())

    def update_clip(self, clip_id: int, **fields) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.execute(f"UPDATE clips SET {sets} WHERE id = ?", list(fields.values()) + [clip_id])

    def next_ready_clip(self, min_score: float, exclude_recent_sources: int = 2) -> dict | None:
        """Best ready clip. Tries not to post two clips from the same source back to back."""
        recent = self.query(
            "SELECT source_id FROM clips WHERE status = 'posted' AND source_id IS NOT NULL "
            "ORDER BY posted_at DESC LIMIT ?",
            (exclude_recent_sources,),
        )
        recent_ids = {int(r["source_id"]) for r in recent if r["source_id"] is not None}
        rows = self.query(
            "SELECT * FROM clips WHERE status = 'ready' AND score >= ? ORDER BY score DESC, id ASC LIMIT 25",
            (min_score,),
        )
        for row in rows:
            if row["source_id"] not in recent_ids:
                return row
        return rows[0] if rows else None

    def clip(self, clip_id: int) -> dict | None:
        return self.one("SELECT * FROM clips WHERE id = ?", (clip_id,))

    def ready_clips(self, min_score: float = 0) -> list[dict]:
        return self.query(
            "SELECT * FROM clips WHERE status = 'ready' AND score >= ? ORDER BY score DESC, id",
            (min_score,),
        )

    def counts(self) -> dict:
        out = {}
        for table in ("sources", "candidates", "clips", "posts"):
            rows = self.query(f"SELECT status, COUNT(*) AS n FROM {table} GROUP BY status")
            out[table] = {r["status"]: r["n"] for r in rows}
        return out

    def posts_today(self, day_prefix: str) -> int:
        row = self.one(
            "SELECT COUNT(*) AS n FROM posts WHERE status = 'ok' AND substr(created_at,1,10) = ?",
            (day_prefix,),
        )
        return int(row["n"]) if row else 0

    def last_post_time(self) -> str | None:
        row = self.one("SELECT created_at FROM posts WHERE status = 'ok' ORDER BY id DESC LIMIT 1")
        return row["created_at"] if row else None
