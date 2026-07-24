"""Named agents.

Each subsystem is wrapped in an agent identity so the dashboard can show who is
working, on what, right now. This is a real telemetry layer over the existing
task functions - not decoration. Every number shown on screen comes from an
actual run: tasks completed, failures, average duration, last output.

An agent is one of: IDLE (waiting for its next turn), WORKING (mid-task),
BLOCKED (its module is in healer backoff), or DOWN (quarantined).
"""
from __future__ import annotations

import threading
import time

from . import config, db, healer

_lock = threading.RLock()

# key -> live runtime state. Persisted counters live in the DB; this holds the
# volatile bits (what it is doing this instant) that would be noise to write.
_live: dict[str, dict] = {}

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    key         TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    role        TEXT NOT NULL,
    module      TEXT NOT NULL,
    runs        INTEGER DEFAULT 0,
    failures    INTEGER DEFAULT 0,
    output      INTEGER DEFAULT 0,
    total_ms    REAL DEFAULT 0,
    last_run    REAL,
    last_task   TEXT,
    last_error  TEXT
);
"""


def init() -> None:
    with db.conn() as c:
        c.executescript(SCHEMA)
    for key, name, role, module in config.AGENTS:
        db.x("INSERT OR IGNORE INTO agents(key,name,role,module) VALUES(?,?,?,?)",
             (key, name, role, module))
        with _lock:
            _live.setdefault(key, {"status": "idle", "task": "", "started": 0.0})


def _set(key: str, **kw) -> None:
    with _lock:
        _live.setdefault(key, {"status": "idle", "task": "", "started": 0.0})
        _live[key].update(kw)


def run(key: str, module: str, task_label: str, fn, *args, **kwargs):
    """Execute one unit of work under an agent's identity.

    Returns whatever fn returned, or None if it failed. Failure is absorbed by
    the healer exactly as before - the agent layer only adds visibility.
    """
    if healer.blocked(module):
        _set(key, status="blocked", task=f"backing off after error")
        return None

    _set(key, status="working", task=task_label, started=time.time())
    db.log(module, f"{task_label}", "info", agent=key)

    t0 = time.perf_counter()
    result = healer.guard(module, fn, *args, **kwargs)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    failed = result is None and healer.blocked(module)
    produced = result if isinstance(result, int) else 0

    db.x(
        "UPDATE agents SET runs=runs+1, failures=failures+?, output=output+?, "
        "total_ms=total_ms+?, last_run=?, last_task=?, last_error=? WHERE key=?",
        (1 if failed else 0, produced, elapsed_ms, time.time(), task_label,
         "module in backoff" if failed else None, key),
    )

    _set(key, status="blocked" if failed else "idle",
         task="" if not failed else "waiting to retry")
    return result


def snapshot() -> list[dict]:
    rows = db.q("SELECT * FROM agents")
    health = healer.health()
    quarantined = set()
    for r in db.q("SELECT DISTINCT module FROM errors WHERE status='quarantined'"):
        quarantined.add(r["module"])

    out = []
    for r in rows:
        with _lock:
            live = dict(_live.get(r["key"], {"status": "idle", "task": "",
                                             "started": 0.0}))

        status = live["status"]
        if r["module"] in quarantined:
            status = "down"
        elif status != "working" and r["module"] in health["blocked_modules"]:
            status = "blocked"

        runs = r["runs"] or 0
        out.append({
            "key": r["key"],
            "name": r["name"],
            "role": r["role"],
            "module": r["module"],
            "status": status,
            "task": live["task"] or (r["last_task"] or ""),
            "elapsed": (time.time() - live["started"]) if status == "working"
                       and live["started"] else 0,
            "runs": runs,
            "failures": r["failures"] or 0,
            "output": r["output"] or 0,
            "avg_ms": round((r["total_ms"] or 0) / runs) if runs else 0,
            "last_run": r["last_run"],
            "success_rate": round(100 * (runs - (r["failures"] or 0)) / runs)
                            if runs else 100,
        })

    order = [a[0] for a in config.AGENTS]
    out.sort(key=lambda a: order.index(a["key"]) if a["key"] in order else 99)
    return out
