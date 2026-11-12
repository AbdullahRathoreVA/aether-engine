"""Publishing, and the boundary that keeps this whole thing alive.

Two lanes, deliberately asymmetric:

  SITE  - our own static pages. Fully autonomous. Nobody can ban you from your
          own domain, so there is no reason to gate it.

  SOCIAL- reply and post drafts. These queue for one-click human approval and
          are never auto-posted. This is not timidity: automated posting through
          unofficial endpoints is the single fastest route to a permanent ban on
          every account you own, which ends the traffic and therefore the income.
          A two-second click per post is the cheapest insurance available.
"""
from __future__ import annotations

import html
import time

from . import brain, config, db, render


def _adopt_legacy_rows() -> None:
    """Version 1 stored finished HTML. Recover the parts once, so those pages
    are re-linked with the rest of the site from now on."""
    for row in db.q("SELECT id,html FROM pages WHERE body IS NULL AND html!=''"):
        parts = render.legacy_parts(row["html"])
        if parts and parts["body"]:
            db.x("UPDATE pages SET body=?,answer=?,faqs=?,description=? WHERE id=?",
                 (parts["body"], parts["answer"], parts["faqs"],
                  parts["description"], row["id"]))


def publish_pages() -> int:
    """Render every page against the whole site and write what changed.

    Returns the number of files written. Rendering is deterministic, so an
    unchanged page is not rewritten and its sitemap date does not move.
    """
    _adopt_legacy_rows()
    rows = db.q("SELECT * FROM pages ORDER BY slug")
    config.SITE_DIR.mkdir(parents=True, exist_ok=True)

    written = 0
    for row in rows:
        page = render.page_html(row, rows) if row["body"] else row["html"]
        if not page:
            continue
        path = config.SITE_DIR / f"{row['slug']}.html"
        try:
            old = path.read_text(encoding="utf-8") if path.exists() else None
            if old != page:
                path.write_text(page, encoding="utf-8", newline="\n")
                written += 1
            if old != page or not row["published"]:
                db.x("UPDATE pages SET published=1, published_at=?, html=? WHERE id=?",
                     (time.time(), page, row["id"]))
        except OSError as e:
            db.log("publisher", f"write {row['slug']} failed: {e}", "error")

    _write_index()
    _write_sitemap()
    _write_robots()

    if written:
        db.log("publisher",
               f"published {written} pages to {config.SITE_DIR.name}/")
    db.set_metric("pages_published",
                  db.scalar("SELECT COUNT(*) FROM pages WHERE published=1"))
    return written


def _write_index() -> None:
    rows = db.q("SELECT slug,title,role,cluster,created_at FROM pages "
                "WHERE published=1 ORDER BY created_at DESC")

    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(r["role"] or r["cluster"] or "guides", []).append(r)

    sections = []
    for name in sorted(groups):
        links = "".join(
            f'<li><a href="{html.escape(r["slug"])}.html">'
            f'{html.escape(r["title"])}</a></li>'
            for r in groups[name]
        )
        sections.append(f"<h2>{html.escape(name.title())}</h2><ul>{links}</ul>")
    body = "".join(sections) or "<p>No guides published yet.</p>"

    name = config.PRODUCT_NAME
    title = config.SITE_TITLE or (f"{name} guides" if name else "Guides")
    lede = config.SITE_LEDE or config.PRODUCT_DESCRIPTION
    desc = str(config.PROFILE["site"].get("description") or "").strip() or lede
    hero = ""
    link = render.checkout_link("index")
    if link:
        button = (f"Get it &mdash; {html.escape(config.PRODUCT_PRICE)}"
                  if config.PRODUCT_PRICE else "Learn more")
        hero = (f'<div class="hero">\n  <b>{html.escape(name)}</b>\n'
                f'  <p style="color:var(--mut);margin:8px 0 0">'
                f'{html.escape(config.PRODUCT_DESCRIPTION)}</p>\n'
                f'  <a class="btn" href="{html.escape(link, quote=True)}">{button}</a>\n</div>')
    suffix = " &middot; " + html.escape(name) if name and name not in title else ""

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}{suffix}</title>
<meta name="description" content="{html.escape(desc, quote=True)}">
<style>
:root{{--bg:#0b0d12;--fg:#e6e9f0;--mut:#9aa4b8;--acc:#ffc857}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);
font:16px/1.7 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}}
.wrap{{max-width:820px;margin:0 auto;padding:56px 20px 80px}}
h1{{font-size:2.4rem;margin:0 0 10px}}
h2{{font-size:1.2rem;margin:36px 0 10px;color:#fff;
border-bottom:1px solid #1e2433;padding-bottom:8px}}
.lede{{color:var(--mut);font-size:1.05rem;margin-bottom:40px;max-width:60ch}}
ul{{padding-left:20px}} li{{margin:7px 0}}
a{{color:var(--acc);text-decoration:none}} a:hover{{text-decoration:underline}}
.hero{{background:#141824;border:1px solid #242c3f;border-left:3px solid var(--acc);
border-radius:12px;padding:28px;margin:0 0 44px}}
.btn{{display:inline-block;background:var(--acc);color:#111;font-weight:700;
padding:13px 26px;border-radius:9px;margin-top:14px}}
.btn:hover{{text-decoration:none;filter:brightness(1.08)}}
</style></head><body><div class="wrap">
<h1>{html.escape(title)}</h1>
<p class="lede">{html.escape(lede)}</p>
{hero}
{body}
{_index_showcase()}
</div></body></html>"""
    (config.SITE_DIR / "index.html").write_text(page, encoding="utf-8", newline="\n")


def _index_showcase() -> str:
    if not config.SHOWCASE_ENABLED:
        return ""
    contact = config.SERVICE_CONTACT
    ok = contact.startswith("mailto:") or render.safe_href(contact)
    hire = (f' &middot; <a href="{html.escape(contact, quote=True)}">Hire me from '
            f'{html.escape(config.SERVICE_PRICE_FROM)}</a>') if contact and ok else ""
    return (
        f'<div class="hero" style="margin-top:48px">'
        f'<b>{html.escape(config.SHOWCASE_NAME)}</b>'
        f'<p style="color:var(--mut);margin:8px 0 12px">'
        f'These guides are written by an autonomous system I built. '
        f'{html.escape(config.SHOWCASE_NAME)} is '
        f'{html.escape(config.SHOWCASE_TAGLINE)}. '
        f'If you need {html.escape(config.SERVICE_NAME)}, the live demo is the '
        f'portfolio.</p>'
        f'<a href="{html.escape(config.SHOWCASE_URL, quote=True)}" target="_blank" '
        f'rel="noopener">See the live demo &rarr;</a>{hire}</div>'
    )


def _write_sitemap() -> None:
    base = config.SITE_BASE_URL.rstrip("/")
    if not base:
        return
    rows = db.q("SELECT slug,published_at FROM pages WHERE published=1")
    urls = [f"<url><loc>{base}/index.html</loc></url>"]
    urls += [
        f"<url><loc>{base}/{r['slug']}.html</loc>"
        f"<lastmod>{time.strftime('%Y-%m-%d', time.localtime(r['published_at'] or time.time()))}</lastmod>"
        f"</url>"
        for r in rows
    ]
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           + "".join(urls) + "</urlset>")
    (config.SITE_DIR / "sitemap.xml").write_text(xml, encoding="utf-8", newline="\n")


def _write_robots() -> None:
    base = config.SITE_BASE_URL.rstrip("/")
    lines = ["User-agent: *", "Allow: /"]
    if base:
        lines.append(f"Sitemap: {base}/sitemap.xml")
    (config.SITE_DIR / "robots.txt").write_text("\n".join(lines) + "\n",
                                                encoding="utf-8", newline="\n")


# ------------------------------------------------------------ approval q -----
def queue(limit: int = 50) -> list[dict]:
    rows = db.q(
        "SELECT c.*, s.url AS signal_url, s.title AS signal_title, s.score "
        "FROM content c LEFT JOIN signals s ON s.id=c.signal_id "
        "WHERE c.status='draft' ORDER BY c.created_at DESC LIMIT ?", (limit,))
    return [dict(r) for r in rows]


def _teach(content_id: int, label: int) -> None:
    """Turn a human decision into a training example.

    The text we learn from is the ORIGINAL post, not our draft - we are trying
    to predict which opportunities the owner considers worth answering, so the
    signal is what they were shown, not what we wrote.
    """
    row = db.one(
        "SELECT c.kind, s.title, s.body, s.source, s.id AS sid "
        "FROM content c LEFT JOIN signals s ON s.id=c.signal_id WHERE c.id=?",
        (content_id,))
    if not row or not row["title"]:
        return
    try:
        brain.record(row["kind"] or "reply", label,
                     f"{row['title']}\n{row['body'] or ''}",
                     row["source"] or "", row["sid"])
    except Exception as e:
        db.log("brain", f"could not record training example: {e}", "warn")


def _decide(content_id: int, status: str, label: int) -> bool:
    """Only a draft can be decided, and only once - so a double click or a bad
    id never records a duplicate training example and skews the model."""
    if not db.one("SELECT 1 FROM content WHERE id=? AND status='draft'",
                  (content_id,)):
        return False
    db.x("UPDATE content SET status=? WHERE id=?", (status, content_id))
    _teach(content_id, label)
    return True


def approve(content_id: int) -> bool:
    """Mark approved. The dashboard copies the text; the human posts it."""
    ok = _decide(content_id, "approved", 1)
    if ok:
        db.log("publisher", f"content #{content_id} approved for posting")
    return ok


def reject(content_id: int) -> bool:
    return _decide(content_id, "rejected", 0)


def mark_posted(content_id: int, url: str = "") -> bool:
    """Record that the human posted an approved draft themselves."""
    if not db.one("SELECT 1 FROM content WHERE id=? AND status='approved'",
                  (content_id,)):
        return False
    db.x("UPDATE content SET status='published', published_url=?, published_at=? "
         "WHERE id=?", (url, time.time(), content_id))
    db.set_metric("content_published",
                  db.scalar("SELECT COUNT(*) FROM content WHERE status='published'"))
    return True
