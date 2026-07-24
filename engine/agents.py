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

from . import config, db, healer, llm

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


# =============================================================== talk to me ==
# Each agent can answer for itself. The facts always come from the database -
# the model only phrases them. With no model at all you still get a real,
# accurate report, which is more useful than fluent guessing.

def _facts(key: str) -> dict:
    """Ground truth for one agent, straight out of the tables it writes to."""
    row = db.one("SELECT * FROM agents WHERE key=?", (key,))
    base = {
        "runs": row["runs"] if row else 0,
        "produced": row["output"] if row else 0,
        "failures": row["failures"] if row else 0,
        "last_run": row["last_run"] if row else None,
        "last_task": row["last_task"] if row else "",
    }

    if key == "hunter":
        base["open_leads"] = db.scalar(
            "SELECT COUNT(*) FROM signals WHERE status='lead'")
        base["worked"] = db.scalar(
            "SELECT COUNT(*) FROM signals WHERE status='lead_used'")
        base["top_leads"] = [
            f"{r['title'][:70]} (fit {r['score']})" for r in db.q(
                "SELECT title,score FROM signals WHERE status='lead' "
                "ORDER BY score DESC LIMIT 5")]
    elif key == "scout":
        base["by_source"] = {r["source"]: r["n"] for r in db.q(
            "SELECT source, COUNT(*) n FROM signals GROUP BY source")}
        base["last_24h"] = db.scalar(
            "SELECT COUNT(*) FROM signals WHERE harvested_at > ?",
            (time.time() - 86400,))
    elif key == "analyst":
        base["hot"] = db.scalar("SELECT COUNT(*) FROM signals WHERE status='scored'")
        base["skipped"] = db.scalar(
            "SELECT COUNT(*) FROM signals WHERE status='skipped'")
        base["top_scoring"] = [
            f"{r['title'][:70]} ({r['score']})" for r in db.q(
                "SELECT title,score FROM signals WHERE status='scored' "
                "ORDER BY score DESC LIMIT 5")]
    elif key in ("scribe", "architect"):
        base["drafts_waiting"] = db.scalar(
            "SELECT COUNT(*) FROM content WHERE status='draft'")
        base["recent"] = [r["title"][:70] for r in db.q(
            "SELECT title FROM content ORDER BY created_at DESC LIMIT 5")]
        if key == "architect":
            base["pages_built"] = db.scalar("SELECT COUNT(*) FROM pages")
            base["recent"] = [r["slug"] for r in db.q(
                "SELECT slug FROM pages ORDER BY created_at DESC LIMIT 5")]
    elif key == "herald":
        base["published"] = db.scalar(
            "SELECT COUNT(*) FROM pages WHERE published=1")
        base["site_dir"] = str(config.SITE_DIR)
        base["site_url"] = config.SITE_BASE_URL or "(not deployed yet)"
    elif key == "ledger":
        from . import revenue
        base.update(revenue.summary())
        base["checkout_url"] = config.CHECKOUT_URL or "(NOT SET - earning $0)"
    elif key == "medic":
        base.update(healer.health())
        base["recent_errors"] = [
            f"{r['module']}: {(r['traceback'] or '').strip().splitlines()[-1][:90]}"
            for r in db.q("SELECT module,traceback FROM errors "
                          "ORDER BY ts DESC LIMIT 5")]
    return base


def report(key: str) -> str:
    """A plain-language, always-accurate status line-up for one agent."""
    meta = next((a for a in config.AGENTS if a[0] == key), None)
    if not meta:
        return "No such agent."
    _, name, role, _module = meta
    f = _facts(key)

    live = next((a for a in snapshot() if a["key"] == key), {})
    status = live.get("status", "idle")
    when = ("never" if not f["last_run"]
            else f"{int(time.time() - f['last_run'])}s ago")

    lines = [
        f"I am {name}, the {role.lower()}.",
        f"Status right now: {status}.",
        f"I have run {f['runs']} times and produced {f['produced']} items. "
        f"Last run: {when}.",
    ]
    if f["failures"]:
        lines.append(f"I have failed {f['failures']} time(s).")

    extra = {k: v for k, v in f.items()
             if k not in ("runs", "produced", "failures", "last_run", "last_task")}
    for k, v in extra.items():
        if isinstance(v, list):
            if v:
                lines.append(f"{k.replace('_', ' ').title()}:")
                lines += [f"  - {item}" for item in v]
        elif isinstance(v, dict):
            if v:
                lines.append(f"{k.replace('_', ' ').title()}: " +
                             ", ".join(f"{a}={b}" for a, b in v.items()))
        else:
            lines.append(f"{k.replace('_', ' ').title()}: {v}")

    return "\n".join(lines)


def chat(key: str, message: str) -> str:
    """Answer a question as this agent, grounded in its real numbers."""
    meta = next((a for a in config.AGENTS if a[0] == key), None)
    if not meta:
        return "No such agent."
    _, name, role, _ = meta

    facts = report(key)
    if not message.strip():
        return facts

    system = (
        f"You are {name}, the {role} inside an autonomous income engine. "
        "You answer in first person, briefly (under 90 words), in plain language. "
        "You ONLY use the facts given - if the answer is not in them, you say you "
        "do not track that and name the agent who would. You never invent numbers "
        "and never promise earnings."
    )
    out = llm.generate(
        f"My current facts:\n{facts}\n\nThe operator asks: {message}",
        system=system, max_tokens=320)

    # No model available - the raw facts are still a correct answer.
    return out or facts

