"""SQLite persistence. Single writer thread is not assumed - we use WAL and a
short-lived connection per call, which is plenty for this workload."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterable

from . import config

_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source       TEXT NOT NULL,
    external_id  TEXT NOT NULL,
    url          TEXT,
    title        TEXT,
    body         TEXT,
    author       TEXT,
    created_utc  REAL,
    harvested_at REAL NOT NULL,
    score        REAL DEFAULT 0,
    intent       TEXT,
    status       TEXT DEFAULT 'new',
    UNIQUE(source, external_id)
);
CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status);
CREATE INDEX IF NOT EXISTS idx_signals_score  ON signals(score DESC);

CREATE TABLE IF NOT EXISTS content (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id     INTEGER,
    kind          TEXT NOT NULL,
    platform      TEXT,
    title         TEXT,
    body          TEXT NOT NULL,
    created_at    REAL NOT NULL,
    status        TEXT DEFAULT 'draft',
    published_url TEXT,
    published_at  REAL,
    FOREIGN KEY(signal_id) REFERENCES signals(id)
);
CREATE INDEX IF NOT EXISTS idx_content_status ON content(status);

CREATE TABLE IF NOT EXISTS pages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    slug         TEXT NOT NULL UNIQUE,
    title        TEXT NOT NULL,
    role         TEXT,
    keyword      TEXT,
    html         TEXT NOT NULL,
    created_at   REAL NOT NULL,
    published    INTEGER DEFAULT 0,
    published_at REAL
);

CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    level   TEXT NOT NULL,
    module  TEXT NOT NULL,
    message TEXT NOT NULL,
    meta    TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC);

CREATE TABLE IF NOT EXISTS errors (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL NOT NULL,
    module     TEXT NOT NULL,
    signature  TEXT NOT NULL,
    traceback  TEXT NOT NULL,
    status     TEXT DEFAULT 'new',
    patch      TEXT,
    attempts   INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_errors_status ON errors(status);

CREATE TABLE IF NOT EXISTS revenue (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    source      TEXT NOT NULL,
    external_id TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    currency    TEXT DEFAULT 'USD',
    product     TEXT,
    UNIQUE(source, external_id)
);

CREATE TABLE IF NOT EXISTS metrics (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    ts    REAL NOT NULL
);
"""


@contextmanager
def conn():
    with _lock:
        c = sqlite3.connect(config.DB_PATH, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()


def init() -> None:
    with conn() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)


def q(sql: str, args: Iterable = ()) -> list[sqlite3.Row]:
    with conn() as c:
        return c.execute(sql, tuple(args)).fetchall()


def one(sql: str, args: Iterable = ()) -> sqlite3.Row | None:
    rows = q(sql, args)
    return rows[0] if rows else None


def x(sql: str, args: Iterable = ()) -> int:
    """Execute and return lastrowid (0 if none)."""
    with conn() as c:
        cur = c.execute(sql, tuple(args))
        return cur.lastrowid or 0


def scalar(sql: str, args: Iterable = (), default: Any = 0) -> Any:
    row = one(sql, args)
    if row is None:
        return default
    val = row[0]
    return default if val is None else val


# ------------------------------------------------------------------ events ---
_subscribers: list = []
_sub_lock = threading.Lock()


def subscribe(queue) -> None:
    with _sub_lock:
        _subscribers.append(queue)


def unsubscribe(queue) -> None:
    with _sub_lock:
        if queue in _subscribers:
            _subscribers.remove(queue)


def log(module: str, message: str, level: str = "info", **meta) -> None:
    """Write an event and fan it out to every live dashboard stream."""
    ts = time.time()
    payload = {
        "ts": ts, "level": level, "module": module,
        "message": message, "meta": meta or None,
    }
    try:
        x("INSERT INTO events(ts,level,module,message,meta) VALUES(?,?,?,?,?)",
          (ts, level, module, message, json.dumps(meta) if meta else None))
    except Exception:
        pass  # logging must never break the caller

    with _sub_lock:
        dead = []
        for sub in _subscribers:
            try:
                sub.put_nowait(payload)
            except Exception:
                dead.append(sub)
        for d in dead:
            if d in _subscribers:
                _subscribers.remove(d)

    print(f"[{time.strftime('%H:%M:%S')}] {level.upper():5s} {module:10s} {message}",
          flush=True)


def set_metric(key: str, value: Any) -> None:
    x("INSERT INTO metrics(key,value,ts) VALUES(?,?,?) "
      "ON CONFLICT(key) DO UPDATE SET value=excluded.value, ts=excluded.ts",
      (key, json.dumps(value), time.time()))


def get_metric(key: str, default: Any = None) -> Any:
    row = one("SELECT value FROM metrics WHERE key=?", (key,))
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except Exception:
        return default


def prune(days: int = 30) -> None:
    """Keep the DB small enough to stay fast on a laptop forever."""
    cutoff = time.time() - days * 86400
    x("DELETE FROM events WHERE ts < ?", (cutoff,))
    x("DELETE FROM signals WHERE harvested_at < ? AND status IN ('used','skipped')",
      (cutoff,))
