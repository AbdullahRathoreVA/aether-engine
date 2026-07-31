"""Local dashboard server: static files, a JSON state snapshot, and an SSE feed.

Binds to 127.0.0.1 by default - this is a control surface for the daemon, not a
public service, and it has no auth by design.
"""
from __future__ import annotations

import json
import queue
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import (agents, brain, config, db, digest, evolve, healer, indexing,
               leads, llm, projects, prospector, publisher, revenue, teacher)

DASHBOARD = config.ROOT / "dashboard"


def snapshot() -> dict:
    now = time.time()
    started = db.get_metric("started_at", now)

    recent = db.q("SELECT ts,level,module,message FROM events "
                  "ORDER BY ts DESC LIMIT 60")

    # 24h activity histogram for the 3D bars
    buckets = [0] * 24
    for row in db.q("SELECT harvested_at FROM signals WHERE harvested_at > ?",
                    (now - 86400,)):
        idx = int((now - row["harvested_at"]) // 3600)
        if 0 <= idx < 24:
            buckets[23 - idx] += 1

    rev = revenue.summary()

    return {
        "ts": now,
        "uptime": now - started,
        "product": {
            "name": config.PRODUCT_NAME,
            "price": config.PRODUCT_PRICE,
            "checkout_configured": bool(config.CHECKOUT_URL),
            "site_configured": bool(config.SITE_BASE_URL),
        },
        "showcase": {
            "enabled": config.SHOWCASE_ENABLED and bool(config.SHOWCASE_URL),
            "name": config.SHOWCASE_NAME,
            "url": config.SHOWCASE_URL,
            "contact_configured": bool(config.SERVICE_CONTACT),
        },
        "agents": agents.snapshot(),
        "leads": leads.pipeline(),
        "brain": brain.stats("reply"),
        "prospector": prospector.report(),
        "teacher": teacher.stats(),
        "evolve": evolve.stats(),
        "reach": indexing.summary(),
        "portfolio": projects.summary(),
        "pipeline": {
            "signals_total": db.scalar("SELECT COUNT(*) FROM signals"),
            "signals_hot": db.scalar(
                "SELECT COUNT(*) FROM signals WHERE status='scored'"),
            "signals_24h": db.scalar(
                "SELECT COUNT(*) FROM signals WHERE harvested_at > ?", (now - 86400,)),
            "drafts": db.scalar(
                "SELECT COUNT(*) FROM content WHERE status='draft'"),
            "approved": db.scalar(
                "SELECT COUNT(*) FROM content WHERE status='approved'"),
            "published": db.scalar(
                "SELECT COUNT(*) FROM content WHERE status='published'"),
            "pages": db.scalar("SELECT COUNT(*) FROM pages WHERE published=1"),
            "histogram": buckets,
        },
        "revenue": rev,
        "health": healer.health(),
        "llm": llm.detect_backend(),
        "events": [dict(r) for r in recent],
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # silence default stderr spam
        pass

    # ------------------------------------------------------------- helpers --
    def _send(self, code: int, body: bytes, ctype: str,
              extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, default=str).encode("utf-8"),
                   "application/json; charset=utf-8")

    # ----------------------------------------------------------------- GET --
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path

        if path in ("/", "/index.html"):
            return self._file(DASHBOARD / "index.html", "text/html; charset=utf-8")

        if path == "/scene.js":
            return self._file(DASHBOARD / "scene.js",
                              "application/javascript; charset=utf-8")

        if path == "/api/state":
            return self._json(snapshot())

        if path == "/api/queue":
            return self._json(publisher.queue())

        if path == "/api/digest":
            return self._json(digest.summary())

        if path == "/api/stream":
            return self._stream()

        if path.startswith("/api/agent/"):
            key = path.rsplit("/", 1)[-1]
            return self._json({"key": key, "report": agents.report(key)})

        if path.startswith("/site/"):
            target = config.SITE_DIR / path[6:]
            if target.is_file():
                return self._file(target, "text/html; charset=utf-8")

        return self._json({"error": "not found"}, 404)

    def _file(self, path, ctype: str) -> None:
        if not path.exists():
            return self._json({"error": f"missing {path.name}"}, 404)
        self._send(200, path.read_bytes(), ctype)

    def _stream(self) -> None:
        """Server-sent events: live terminal feed for the dashboard."""
        q: queue.Queue = queue.Queue(maxsize=200)
        db.subscribe(q)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            while True:
                try:
                    payload = q.get(timeout=15)
                    data = f"data: {json.dumps(payload, default=str)}\n\n"
                except queue.Empty:
                    data = ": keepalive\n\n"   # stops proxies/browsers idling out
                self.wfile.write(data.encode("utf-8"))
                self.wfile.flush()
        except Exception:
            pass
        finally:
            db.unsubscribe(q)

    # ---------------------------------------------------------------- POST --
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            payload = {}

        cid = int(payload.get("id", 0) or 0)

        if path == "/api/approve" and cid:
            publisher.approve(cid)
            return self._json({"ok": True})

        if path == "/api/reject" and cid:
            publisher.reject(cid)
            return self._json({"ok": True})

        if path == "/api/posted" and cid:
            publisher.mark_posted(cid, payload.get("url", ""))
            return self._json({"ok": True})

        if path == "/api/approve_all":
            kind = str(payload.get("kind", ""))
            rows = db.q(
                "SELECT id FROM content WHERE status='draft'" +
                (" AND kind=?" if kind else ""), (kind,) if kind else ())
            for r in rows:
                publisher.approve(r["id"])
            return self._json({"ok": True, "approved": len(rows)})

        if path == "/api/chat":
            key = str(payload.get("agent", ""))
            msg = str(payload.get("message", ""))
            return self._json({"agent": key, "reply": agents.chat(key, msg)})

        if path == "/api/sale":
            # Log a direct Easypaisa / JazzCash / bank sale. Zero fees, so these
            # are worth recording properly instead of guessing at month end.
            try:
                amount = float(payload.get("amount", 0))
            except (TypeError, ValueError):
                return self._json({"error": "bad amount"}, 400)
            if amount <= 0:
                return self._json({"error": "amount must be > 0"}, 400)
            revenue.log_manual(amount,
                               str(payload.get("currency", "PKR")),
                               str(payload.get("product", "")),
                               str(payload.get("note", "")))
            return self._json({"ok": True, "revenue": revenue.summary()})

        return self._json({"error": "not found"}, 404)


def serve_forever() -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((config.HOST, config.PORT), Handler)
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True, name="http")
    t.start()
    db.log("server", f"dashboard live at http://{config.HOST}:{config.PORT}")
    return srv
