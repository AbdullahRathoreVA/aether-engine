"""Signal harvesting from documented, free, public endpoints.

Every source below is an official read API that permits unauthenticated access:
  - Reddit  : the public .json view of any listing
  - HN      : Algolia's free public search API
  - StackEx : api.stackexchange.com, no key needed under the anonymous quota
  - RSS     : any feed the profile lists

Where to listen comes from the profile (sources.*). Everything harvested is
third-party text: it is stored and shown as data and never acted on.

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

# What could not be reached in the current sweep, so a caller can tell "nothing
# new" from "the source was down" instead of reporting a silent zero.
_failures: list[str] = []


def _fail(what: str, e: Exception) -> None:
    code = getattr(e, "code", None)
    _failures.append(f"{what}: {'HTTP ' + str(code) if code else type(e).__name__}")


def _store(source: str, external_id: str, *, url: str, title: str,
           body: str, author: str, created_utc: float) -> bool:
    """Insert if new. Returns True when a genuinely new signal landed."""
    if not external_id or not (url or "").startswith(("http://", "https://")):
        return False
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
            _fail(f"r/{sub}", e)
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
        except Exception as e:
            failed += 1
            _fail(f"r/{sub}", e)
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
    """Stories (Ask HN included) and comments matching the profile's queries."""
    new = 0
    for query in config.HN_QUERIES:
        url = ("https://hn.algolia.com/api/v1/search_by_date"
               f"?query={urllib.parse.quote(query)}&tags=(story,comment)&hitsPerPage=40")
        try:
            data = net.get_json(url)
        except Exception as e:
            _fail(f"hackernews '{query}'", e)
            db.log("harvest", f"HN '{query}' failed: {type(e).__name__}", "warn")
            continue

        for hit in data.get("hits", []):
            is_story = bool(hit.get("title"))
            text = html.unescape(hit.get("story_text" if is_story else "comment_text") or "")
            text = " ".join(re.sub(r"<[^>]+>", " ", text).split())
            if not is_story and len(text) < 80:
                continue
            if _store(
                "hackernews", str(hit.get("objectID", "")),
                url=f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
                title=hit.get("title") or hit.get("story_title") or query,
                body=text,
                author=hit.get("author", ""),
                created_utc=float(hit.get("created_at_i") or 0),
            ):
                new += 1
    return new


# ------------------------------------------------------------ stackexchange --
def harvest_stackexchange() -> int:
    new = 0
    for src in config.STACKEXCHANGE:
        if not src["site"]:
            continue
        url = ("https://api.stackexchange.com/2.3/questions"
               "?order=desc&sort=creation&filter=withbody&pagesize=50"
               f"&site={urllib.parse.quote(src['site'])}")
        if src["tagged"]:
            url += f"&tagged={urllib.parse.quote(src['tagged'])}"
        try:
            data = net.get_json(url)
        except Exception as e:
            _fail(f"stackexchange {src['site']}", e)
            db.log("harvest", f"StackExchange {src['site']} failed: {type(e).__name__}", "warn")
            continue

        for item in data.get("items", []):
            body = html.unescape(item.get("body", ""))
            body = " ".join(re.sub(r"<[^>]+>", " ", body).split())
            if _store(
                "stackexchange", f"{src['site']}:{item.get('question_id', '')}",
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
    new = 0
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for feed in (feeds if feeds is not None else config.RSS_FEEDS):
        try:
            root = ET.fromstring(net.fetch(feed))
        except Exception as e:
            _fail(f"rss {feed}", e)
            db.log("harvest", f"RSS {feed} failed: {type(e).__name__}", "warn")
            continue

        for e in root.findall(".//item") or root.findall(".//a:entry", ns):
            def txt(*names: str) -> str:
                for n in names:
                    el = e.find(n, ns) if n.startswith("a:") else e.find(n)
                    if el is not None and el.text:
                        return el.text
                return ""

            # An Element with no children is falsy, so `find(..) or find(..)`
            # silently discards a perfectly good <link>. Test for None.
            link_el = e.find("link")
            if link_el is None:
                link_el = e.find("a:link", ns)
            link = ""
            if link_el is not None:
                link = (link_el.text or "").strip() or link_el.get("href", "")
            title = txt("title", "a:title")
            if not link or not title:
                continue
            body = html.unescape(txt("description", "a:summary", "a:content"))
            body = " ".join(re.sub(r"<[^>]+>", " ", body).split())
            if _store("rss", link, url=link, title=html.unescape(title),
                      body=body, author="", created_utc=time.time()):
                new += 1
    return new


SOURCES = (("reddit", harvest_reddit), ("hackernews", harvest_hn),
           ("stackexchange", harvest_stackexchange), ("rss", harvest_rss))


def harvest_report(only: list[str] | None = None) -> dict:
    """One sweep. Says what arrived per source and what could not be reached."""
    _failures.clear()
    by_source: dict[str, int] = {}
    for name, fn in SOURCES:
        if only and name not in only:
            continue
        try:
            n = fn()
            by_source[name] = n
            if n:
                db.log("harvest", f"{name}: +{n} new signals")
        except Exception as e:
            # One dead source must never stop the others.
            _fail(name, e)
            db.log("harvest", f"{name} crashed: {type(e).__name__}: {e}", "error")

    db.set_metric("last_harvest", time.time())
    db.set_metric("signals_total", db.scalar("SELECT COUNT(*) FROM signals"))
    return {"new_signals": sum(by_source.values()), "by_source": by_source,
            "unreachable": list(_failures)}


def harvest_all() -> int:
    return harvest_report()["new_signals"]
