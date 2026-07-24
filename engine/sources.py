"""Signal harvesting from documented, free, public endpoints.

Every source below is an official read API that permits unauthenticated access:
  - Reddit  : the public .json view of any listing
  - HN      : Algolia's free public search API
  - StackEx : api.stackexchange.com, no key needed under the anonymous quota

We do not log in, do not touch private endpoints, and do not evade anything.
That is not a compromise - authenticated scraping is what gets accounts killed,
and a dead account harvests nothing.
"""
from __future__ import annotations

import datetime
import html
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET

from . import config, db, net


def _store(source: str, external_id: str, *, url: str, title: str,
           body: str, author: str, created_utc: float) -> bool:
    """Insert if new. Returns True when a genuinely new signal landed."""
    rid = db.x(
        "INSERT OR IGNORE INTO signals"
        "(source,external_id,url,title,body,author,created_utc,harvested_at)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (source, external_id, url, title[:500], body[:8000], author,
         created_utc, time.time()),
    )
    return rid > 0


# ----------------------------------------------------------------- reddit ----
# Reddit 403s /.json for unauthenticated clients as of 2026 and rate-limits even
# /.rss (measured: 3-4 of 6 subreddits fail per sweep regardless of spacing).
# Two lanes, in order of preference:
#   1. Official OAuth app - free, 100 req/min, reliable. Needs a client id.
#   2. Public .rss feed    - no setup, best-effort, expect partial sweeps.
# Failed subreddits are simply picked up on the next cycle rather than retried
# in-burst, which never works and only burns time.
ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}

_token: dict = {"value": "", "expires": 0.0}


def _reddit_token() -> str:
    """Client-credentials token for a Reddit 'script' app. '' if unconfigured."""
    cid, secret = config.REDDIT_CLIENT_ID, config.REDDIT_CLIENT_SECRET
    if not (cid and secret):
        return ""
    if _token["value"] and time.time() < _token["expires"]:
        return _token["value"]

    import base64
    basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    try:
        raw = net.fetch(
            "https://www.reddit.com/api/v1/access_token",
            data=b"grant_type=client_credentials",
            headers={"Authorization": f"Basic {basic}",
                     "Content-Type": "application/x-www-form-urlencoded"},
            method="POST", retries=1,
        )
        import json
        data = json.loads(raw)
    except Exception as e:
        db.log("harvest", f"Reddit OAuth failed ({type(e).__name__}); using RSS", "warn")
        return ""

    _token["value"] = data.get("access_token", "")
    _token["expires"] = time.time() + float(data.get("expires_in", 3600)) - 120
    if _token["value"]:
        db.log("harvest", "Reddit OAuth token acquired")
    return _token["value"]


def _harvest_reddit_oauth(token: str) -> int:
    new = 0
    for sub in config.SUBREDDITS:
        url = f"https://oauth.reddit.com/r/{urllib.parse.quote(sub)}/new?limit=50"
        try:
            data = net.get_json(url, headers={"Authorization": f"Bearer {token}"})
        except Exception as e:
            db.log("harvest", f"r/{sub} (oauth) failed: {type(e).__name__}", "warn")
            continue

        for child in data.get("data", {}).get("children", []):
            d = child.get("data", {})
            if d.get("stickied") or d.get("over_18"):
                continue
            if _store("reddit", d.get("id", ""),
                      url=f"https://reddit.com{d.get('permalink', '')}",
                      title=d.get("title", ""), body=d.get("selftext", ""),
                      author=d.get("author", ""),
                      created_utc=float(d.get("created_utc") or 0)):
                new += 1
    return new


def harvest_reddit() -> int:
    token = _reddit_token()
    if token:
        return _harvest_reddit_oauth(token)

    new = 0
    failed = 0
    for sub in config.SUBREDDITS:
        url = f"https://www.reddit.com/r/{urllib.parse.quote(sub)}/new/.rss"
        try:
            root = ET.fromstring(net.fetch(url, retries=0))
        except Exception:
            failed += 1
            continue

        for entry in root.findall("a:entry", ATOM_NS):
            def text(tag: str) -> str:
                el = entry.find(f"a:{tag}", ATOM_NS)
                return (el.text or "") if el is not None else ""

            link_el = entry.find("a:link", ATOM_NS)
            link = link_el.get("href", "") if link_el is not None else ""
            ext_id = text("id") or link
            if not link:
                continue

            # Reddit wraps the post body in escaped HTML inside <content>.
            content = html.unescape(text("content"))
            content = re.sub(r"<[^>]+>", " ", content)
            content = " ".join(content.split())

            author_el = entry.find("a:author/a:name", ATOM_NS)
            author = (author_el.text or "") if author_el is not None else ""

            created = 0.0
            updated = text("updated") or text("published")
            if updated:
                try:
                    created = datetime.datetime.fromisoformat(
                        updated.replace("Z", "+00:00")).timestamp()
                except ValueError:
                    pass

            if _store("reddit", ext_id, url=link, title=text("title"),
                      body=content, author=author, created_utc=created):
                new += 1

    if failed:
        db.log("harvest",
               f"reddit: {failed}/{len(config.SUBREDDITS)} subs rate-limited "
               f"(anonymous RSS is best-effort; set REDDIT_CLIENT_ID to fix)",
               "warn")
    return new


# --------------------------------------------------------------- hackernews --
def harvest_hn() -> int:
    new = 0
    queries = ["resume ATS", "job search advice", "applying to jobs rejection"]
    for query in queries:
        url = ("https://hn.algolia.com/api/v1/search_by_date"
               f"?query={urllib.parse.quote(query)}&tags=comment&hitsPerPage=40")
        try:
            data = net.get_json(url)
        except Exception as e:
            db.log("harvest", f"HN '{query}' failed: {type(e).__name__}", "warn")
            continue

        for hit in data.get("hits", []):
            text = html.unescape(hit.get("comment_text") or "")
            if len(text) < 80:
                continue
            if _store(
                "hackernews", str(hit.get("objectID", "")),
                url=f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
                title=hit.get("story_title") or query,
                body=text,
                author=hit.get("author", ""),
                created_utc=float(hit.get("created_at_i") or 0),
            ):
                new += 1
    return new


# ------------------------------------------------------------ stackexchange --
def harvest_stackexchange() -> int:
    url = ("https://api.stackexchange.com/2.3/questions"
           "?order=desc&sort=creation&site=workplace&filter=withbody&pagesize=50")
    try:
        data = net.get_json(url)
    except Exception as e:
        db.log("harvest", f"StackExchange failed: {type(e).__name__}", "warn")
        return 0

    new = 0
    for item in data.get("items", []):
        body = html.unescape(item.get("body", ""))
        body = " ".join(body.replace("<", " <").split())
        if _store(
            "stackexchange", str(item.get("question_id", "")),
            url=item.get("link", ""),
            title=html.unescape(item.get("title", "")),
            body=body,
            author=(item.get("owner") or {}).get("display_name", ""),
            created_utc=float(item.get("creation_date") or 0),
        ):
            new += 1
    return new


# -------------------------------------------------------------------- rss ----
def harvest_rss(feeds: list[str] | None = None) -> int:
    feeds = feeds or [
        "https://www.reddit.com/r/jobs/.rss",
        "https://hnrss.org/newest?q=resume",
    ]
    new = 0
    for feed in feeds:
        try:
            root = ET.fromstring(net.fetch(feed))
        except Exception as e:
            db.log("harvest", f"RSS {feed} failed: {type(e).__name__}", "warn")
            continue

        ns = {"a": "http://www.w3.org/2005/Atom"}
        entries = root.findall(".//item") or root.findall(".//a:entry", ns)
        for e in entries:
            def txt(*names: str) -> str:
                for n in names:
                    el = e.find(n) if not n.startswith("a:") else e.find(n, ns)
                    if el is not None and el.text:
                        return el.text
                return ""

            link_el = e.find("link") or e.find("a:link", ns)
            link = (link_el.text if link_el is not None and link_el.text
                    else (link_el.get("href", "") if link_el is not None else ""))
            title = txt("title", "a:title")
            if not link or not title:
                continue
            if _store("rss", link, url=link, title=title,
                      body=txt("description", "a:summary", "a:content"),
                      author="", created_utc=time.time()):
                new += 1
    return new


def harvest_all() -> int:
    total = 0
    for name, fn in (("reddit", harvest_reddit),
                     ("hackernews", harvest_hn),
                     ("stackexchange", harvest_stackexchange),
                     ("rss", harvest_rss)):
        try:
            n = fn()
            total += n
            if n:
                db.log("harvest", f"{name}: +{n} new signals")
        except Exception as e:
            # One dead source must never stop the others.
            db.log("harvest", f"{name} crashed: {type(e).__name__}: {e}", "error")

    db.set_metric("last_harvest", time.time())
    db.set_metric("signals_total", db.scalar("SELECT COUNT(*) FROM signals"))
    return total
