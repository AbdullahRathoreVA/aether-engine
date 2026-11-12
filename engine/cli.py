"""One-shot CLI so an AI agent (or cron) can drive the engine without the daemon.

    python -m engine.cli <command> [n|id]

Every command prints JSON on stdout and exits 0 on success, 1 on failure.
"""
from __future__ import annotations

import json
import sys
from contextlib import redirect_stdout

from . import brain, db, generator, llm, publisher, scorer, seo, sources


def _status() -> dict:
    n = lambda sql: db.scalar(sql)
    return {"llm": llm.detect_backend(),
            "signals": n("SELECT COUNT(*) FROM signals"),
            "pages": n("SELECT COUNT(*) FROM pages"),
            "published": n("SELECT COUNT(*) FROM pages WHERE published=1"),
            "queued": len(publisher.queue()),
            "claude_usd_today": round(llm.claude_spent(), 4)}


CMDS = {
    "status":  lambda k: _status(),
    "harvest": lambda k: {"new_signals": sources.harvest_all()},
    "score":   lambda k: {"scored": scorer.score_pending(k or 40)},
    "top":     lambda k: [dict(r) for r in scorer.top_signals(k or 5)],
    "seo":     lambda k: {"pages_built": generator.generate_seo_pages(k or 2)},
    "publish": lambda k: {"pages_written": publisher.publish_pages()},
    "audit":   lambda k: seo.audit(),
    "queue":   lambda k: publisher.queue(k or 20),
    "approve": lambda k: {"approved": publisher.approve(k)},  # trains the model
    "reject":  lambda k: {"rejected": publisher.reject(k)},   # trains the model
    "train":   lambda k: {"samples": len(brain.samples("reply")),
                          "accuracy": brain.accuracy("reply")},
}


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in CMDS:
        print(f"usage: python -m engine.cli <{'|'.join(CMDS)}> [n]", file=sys.stderr)
        return 1
    try:
        k = int(argv[1]) if len(argv) > 1 else 0
        with redirect_stdout(sys.stderr):  # engine logs -> stderr, JSON -> stdout
            db.init()
            brain.init()
            out = CMDS[argv[0]](k)
        print(json.dumps(out, indent=2, default=str))
        return 0
    except Exception as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
