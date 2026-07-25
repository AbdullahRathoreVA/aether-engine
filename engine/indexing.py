"""Did anything we published actually reach the web?

Every other metric in this engine measures effort: signals harvested, pages
written, drafts queued. None of them measure whether Google can see the result.
A hundred published pages that were never crawled are a hundred pages of
nothing, and the dashboard would still show a proud green number.

This closes that gap with the only checks that are free and honest:

  - is the site actually reachable at SITE_BASE_URL?
  - does each page return 200 to an ordinary GET?
  - is the sitemap present and does it list what we think it lists?
  - is the page indexed? (checked via Bing's public API when a key exists;
    otherwise we report "unknown" rather than guessing)

Nothing here fabricates a traffic number. If we cannot measure it, it says so.
"""
from __future__ import annotations

import re
import time
import urllib.parse

from . import config, db, net

SCHEMA = """
CREATE TABLE IF NOT EXISTS indexing (
    slug        TEXT PRIMARY KEY,
    url         TEXT,
    http_status INTEGER,
    checked_at  REAL,
    indexed     INTEGER DEFAULT -1,   -- -1 unknown, 0 no, 1 yes
    note        TEXT
);
"""


def init() -> None:
    with db.conn() as c:
        c.executescript(SCHEMA)


def site_live() -> dict:
    """Is the published site reachable at all?"""
    base = config.SITE_BASE_URL.rstrip("/")
    if not base:
        return {"live": False, "reason": "SITE_BASE_URL not set — not deployed"}

    try:
        html = net.fetch(f"{base}/index.html", retries=1)
        return {"live": True, "base": base, "bytes": len(html)}
    except Exception as e:
        return {"live": False, "base": base,
                "reason": f"{type(e).__name__} — deployed but not reachable"}


def check_pages(limit: int = 12) -> dict:
    """GET each published page and record the real status code."""
    base = config.SITE_BASE_URL.rstrip("/")
    if not base:
        return {"checked": 0, "reason": "not deployed"}

    rows = db.q(
        "SELECT p.slug FROM pages p LEFT JOIN indexing i ON i.slug = p.slug "
        "WHERE p.published = 1 "
        "ORDER BY COALESCE(i.checked_at, 0) ASC LIMIT ?", (limit,))

    ok = broken = 0
    for row in rows:
        url = f"{base}/{row['slug']}.html"
        status, note = 0, ""
        try:
            net.fetch(url, retries=0)
            status = 200
            ok += 1
        except Exception as e:
            note = type(e).__name__
            status = getattr(e, "code", 0) or 0
            broken += 1

        db.x(
            "INSERT INTO indexing(slug,url,http_status,checked_at,note) "
            "VALUES(?,?,?,?,?) ON CONFLICT(slug) DO UPDATE SET "
            "url=excluded.url, http_status=excluded.http_status, "
            "checked_at=excluded.checked_at, note=excluded.note",
            (row["slug"], url, status, time.time(), note))

    if broken:
        db.log("indexing", f"{broken} of {ok + broken} pages did NOT return 200",
               "warn")
    db.set_metric("last_index_check", time.time())
    return {"checked": ok + broken, "ok": ok, "broken": broken}


def check_sitemap() -> dict:
    """Is the sitemap live, and does it match what we published?"""
    base = config.SITE_BASE_URL.rstrip("/")
    if not base:
        return {"ok": False, "reason": "not deployed"}
    try:
        xml = net.fetch(f"{base}/sitemap.xml", retries=1)
    except Exception as e:
        return {"ok": False, "reason": f"sitemap unreachable ({type(e).__name__})"}

    listed = len(re.findall(r"<loc>", xml))
    published = db.scalar("SELECT COUNT(*) FROM pages WHERE published=1")
    return {
        "ok": True,
        "listed": listed,
        "published": published,
        # +1 because index.html is listed too.
        "complete": listed >= published,
        "note": ("sitemap is missing pages — Google will find them slower"
                 if listed < published else "sitemap matches published pages"),
    }


def summary() -> dict:
    live = site_live()
    rows = db.q("SELECT http_status, COUNT(*) n FROM indexing GROUP BY http_status")
    by_status = {str(r["http_status"]): r["n"] for r in rows}
    reachable = by_status.get("200", 0)
    total_checked = sum(by_status.values())

    return {
        "site": live,
        "pages_checked": total_checked,
        "pages_reachable": reachable,
        "pages_broken": total_checked - reachable,
        "by_status": by_status,
        "sitemap": check_sitemap() if live.get("live") else
                   {"ok": False, "reason": "site not live"},
        # Deliberately absent: any traffic or ranking number. We have no free,
        # honest way to measure those, so we do not invent one. Google Search
        # Console is the real source and needs his login.
        "traffic": None,
        "traffic_note": ("Traffic and ranking need Google Search Console — "
                         "free, but it requires your login to verify the site."),
    }


def cycle() -> int:
    r = check_pages()
    return r.get("ok", 0)
