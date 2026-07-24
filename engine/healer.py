"""Closed-loop self-healing.

When a task raises, we:
  1. record the traceback under a stable signature (so repeats are one row)
  2. quarantine that task with exponential backoff - the daemon keeps running
  3. ask the LLM for a corrected version of the failing source file
  4. syntax-check the candidate, back up the original, and (only if AUTO_PATCH)
     write it in; otherwise park it in state/patches for a human to read

The daemon never dies. A permanently broken subsystem degrades to "disabled"
while everything else keeps earning.
"""
from __future__ import annotations

import ast
import hashlib
import shutil
import time
import traceback
from pathlib import Path

from . import config, db, llm

_backoff: dict[str, float] = {}
_failures: dict[str, int] = {}

PATCH_SYSTEM = (
    "You are a senior Python engineer fixing a runtime exception. You output the "
    "complete corrected file and nothing else - no explanation, no markdown fence. "
    "You make the smallest change that fixes the traceback. You never remove "
    "functionality, never add new third-party imports, and never delete error "
    "handling."
)


def signature(module: str, tb: str) -> str:
    """Stable id for 'this same bug again', ignoring line numbers and paths."""
    lines = [l for l in tb.splitlines() if l.strip().startswith(("File", " File"))]
    tail = tb.strip().splitlines()[-1] if tb.strip() else ""
    basis = f"{module}|{len(lines)}|{tail}"
    return hashlib.sha1(basis.encode()).hexdigest()[:16]


def record(module: str, exc: BaseException) -> str:
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    sig = signature(module, tb)

    existing = db.one("SELECT id,attempts FROM errors WHERE signature=?", (sig,))
    if existing:
        db.x("UPDATE errors SET ts=?, attempts=attempts+1 WHERE id=?",
             (time.time(), existing["id"]))
    else:
        db.x("INSERT INTO errors(ts,module,signature,traceback) VALUES(?,?,?,?)",
             (time.time(), module, sig, tb))

    _failures[module] = _failures.get(module, 0) + 1
    delay = min(300 * (2 ** (_failures[module] - 1)), 3600)
    _backoff[module] = time.time() + delay

    db.log("healer", f"{module} failed ({type(exc).__name__}); "
                     f"backing off {int(delay)}s", "error")
    db.set_metric("errors_open",
                  db.scalar("SELECT COUNT(*) FROM errors WHERE status='new'"))
    return sig


def clear(module: str) -> None:
    """Called after a successful run - resets the backoff ladder."""
    if module in _failures:
        _failures.pop(module, None)
        _backoff.pop(module, None)
        db.log("healer", f"{module} recovered", "info")


def blocked(module: str) -> bool:
    return time.time() < _backoff.get(module, 0)


def guard(module: str, fn, *args, **kwargs):
    """Run fn, absorbing any exception into the healing loop."""
    if blocked(module):
        return None
    try:
        result = fn(*args, **kwargs)
        clear(module)
        return result
    except Exception as e:
        record(module, e)
        return None


# ------------------------------------------------------------ patch cycle ----
def _module_path(module: str) -> Path | None:
    p = Path(__file__).parent / f"{module}.py"
    return p if p.exists() else None


def attempt_patch() -> int:
    """Ask the model to fix the oldest open error. Returns patches produced."""
    if llm.detect_backend() == "template":
        return 0  # no model, no patching - fail safe, not fail weird

    row = db.one(
        "SELECT * FROM errors WHERE status='new' AND attempts < ? "
        "ORDER BY ts ASC LIMIT 1", (config.MAX_PATCH_ATTEMPTS,))
    if not row:
        return 0

    path = _module_path(row["module"])
    if not path:
        db.x("UPDATE errors SET status='quarantined' WHERE id=?", (row["id"],))
        return 0

    source = path.read_text(encoding="utf-8")
    if len(source) > 24000:
        db.x("UPDATE errors SET status='quarantined' WHERE id=?", (row["id"],))
        db.log("healer", f"{row['module']} too large to patch; quarantined", "warn")
        return 0

    db.x("UPDATE errors SET status='patching' WHERE id=?", (row["id"],))
    db.log("healer", f"requesting patch for {row['module']}")

    prompt = (
        f"File: engine/{row['module']}.py\n\n"
        f"--- TRACEBACK ---\n{row['traceback'][-3000:]}\n\n"
        f"--- CURRENT SOURCE ---\n{source}\n\n"
        "Output the complete corrected file."
    )
    candidate = llm.generate(prompt, system=PATCH_SYSTEM, max_tokens=8000)

    if not candidate:
        db.x("UPDATE errors SET status='new' WHERE id=?", (row["id"],))
        return 0

    candidate = candidate.strip()
    if candidate.startswith("```"):
        candidate = candidate.split("\n", 1)[-1]
        candidate = candidate.rsplit("```", 1)[0].strip()

    # Gate 1: it must parse.
    try:
        ast.parse(candidate)
    except SyntaxError as e:
        db.x("UPDATE errors SET status='new', patch=? WHERE id=?",
             (f"REJECTED - syntax error: {e}", row["id"]))
        db.log("healer", f"patch for {row['module']} rejected (syntax)", "warn")
        return 0

    # Gate 2: it must not have gutted the file.
    if len(candidate) < len(source) * 0.5:
        db.x("UPDATE errors SET status='new', patch=? WHERE id=?",
             ("REJECTED - suspiciously short", row["id"]))
        db.log("healer", f"patch for {row['module']} rejected (too short)", "warn")
        return 0

    stamp = time.strftime("%Y%m%d-%H%M%S")
    patch_file = config.PATCH_DIR / f"{row['module']}-{stamp}.py"
    patch_file.write_text(candidate, encoding="utf-8")
    db.x("UPDATE errors SET patch=? WHERE id=?", (str(patch_file), row["id"]))

    if not config.AUTO_PATCH:
        db.x("UPDATE errors SET status='proposed' WHERE id=?", (row["id"],))
        db.log("healer",
               f"patch proposed for {row['module']} -> {patch_file.name} "
               f"(set AUTO_PATCH=1 to apply automatically)", "warn")
        return 1

    # Gate 3: back up, then apply.
    backup = config.PATCH_DIR / f"{row['module']}-{stamp}.bak.py"
    shutil.copy2(path, backup)
    path.write_text(candidate, encoding="utf-8")

    db.x("UPDATE errors SET status='patched' WHERE id=?", (row["id"],))
    _failures.pop(row["module"], None)
    _backoff.pop(row["module"], None)
    db.log("healer",
           f"APPLIED patch to {row['module']} (backup: {backup.name}); "
           f"restart to load", "warn")
    db.set_metric("patches_applied",
                  db.scalar("SELECT COUNT(*) FROM errors WHERE status='patched'"))
    return 1


def health() -> dict:
    return {
        "open_errors": db.scalar("SELECT COUNT(*) FROM errors WHERE status='new'"),
        "proposed": db.scalar("SELECT COUNT(*) FROM errors WHERE status='proposed'"),
        "patched": db.scalar("SELECT COUNT(*) FROM errors WHERE status='patched'"),
        "quarantined": db.scalar(
            "SELECT COUNT(*) FROM errors WHERE status='quarantined'"),
        "blocked_modules": [m for m in _backoff if blocked(m)],
    }
