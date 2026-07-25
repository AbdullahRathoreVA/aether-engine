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

from . import brain, config, db


def publish_pages() -> int:
    """Write unpublished pages to site/ and regenerate index + sitemap."""
    rows = db.q("SELECT * FROM pages WHERE published=0")
    if not rows and (config.SITE_DIR / "index.html").exists():
        return 0

    written = 0
    for row in rows:
        path = config.SITE_DIR / f"{row['slug']}.html"
        try:
            path.write_text(row["html"], encoding="utf-8")
            db.x("UPDATE pages SET published=1, published_at=? WHERE id=?",
                 (time.time(), row["id"]))
            written += 1
        except Exception as e:
            db.log("publisher", f"write {row['slug']} failed: {e}", "error")

    _write_index()
    _write_sitemap()
    _write_robots()

    if written:
        db.log("publisher", f"published {written} pages to site/")
    db.set_metric("pages_published",
                  db.scalar("SELECT COUNT(*) FROM pages WHERE published=1"))
    return written


def _write_index() -> None:
    rows = db.q("SELECT slug,title,role,created_at FROM pages WHERE published=1 "
                "ORDER BY created_at DESC")

    by_role: dict[str, list] = {}
    for r in rows:
        by_role.setdefault(r["role"] or "general", []).append(r)

    sections = []
    for role in sorted(by_role):
        links = "".join(
            f'<li><a href="{html.escape(r["slug"])}.html">'
            f'{html.escape(r["title"])}</a></li>'
            for r in by_role[role]
        )
        sections.append(f"<h2>{html.escape(role.title())}</h2><ul>{links}</ul>")

    checkout = config.CHECKOUT_URL or "#checkout-not-configured"
    body = "".join(sections) or "<p>No guides published yet.</p>"

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Free Job-Search Guides &middot; {html.escape(config.PRODUCT_NAME)}</title>
<meta name="description" content="Free, specific guides on ATS resumes, cover letters,
LinkedIn headlines and AI prompts for the job search.">
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
<h1>Free Job-Search Guides</h1>
<p class="lede">Specific, no-filler guides on getting past applicant tracking
systems &mdash; organised by role.</p>
<div class="hero">
  <b>{html.escape(config.PRODUCT_NAME)}</b>
  <p style="color:var(--mut);margin:8px 0 0">ATS-ready resume &amp; cover letter
  templates, 100 AI prompts, LinkedIn checklist, application tracker.</p>
  <a class="btn" href="{html.escape(checkout)}">Get it &mdash;
  {html.escape(config.PRODUCT_PRICE)}</a>
</div>
{body}
{_index_showcase()}
</div></body></html>"""
    (config.SITE_DIR / "index.html").write_text(page, encoding="utf-8")


def _index_showcase() -> str:
    if not (config.SHOWCASE_ENABLED and config.SHOWCASE_URL):
        return ""
    contact = config.SERVICE_CONTACT
    hire = (f' &middot; <a href="{html.escape(contact)}">Hire me from '
            f'{html.escape(config.SERVICE_PRICE_FROM)}</a>') if contact else ""
    return (
        f'<div class="hero" style="margin-top:48px">'
        f'<b>{html.escape(config.SHOWCASE_NAME)}</b>'
        f'<p style="color:var(--mut);margin:8px 0 12px">'
        f'These guides are written by an autonomous system I built. '
        f'{html.escape(config.SHOWCASE_NAME)} is '
        f'{html.escape(config.SHOWCASE_TAGLINE)}. '
        f'If you need {html.escape(config.SERVICE_NAME)}, the live demo is the '
        f'portfolio.</p>'
        f'<a href="{html.escape(config.SHOWCASE_URL)}" target="_blank" '
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
    (config.SITE_DIR / "sitemap.xml").write_text(xml, encoding="utf-8")


def _write_robots() -> None:
    base = config.SITE_BASE_URL.rstrip("/")
    lines = ["User-agent: *", "Allow: /"]
    if base:
        lines.append(f"Sitemap: {base}/sitemap.xml")
    (config.SITE_DIR / "robots.txt").write_text("\n".join(lines) + "\n",
                                                encoding="utf-8")


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
    to predict which opportunities Abdullah considers worth his time, so the
    signal is what he was shown, not what we wrote.
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


def approve(content_id: int) -> bool:
    """Mark approved. The dashboard copies the text; the human posts it."""
    db.x("UPDATE content SET status='approved' WHERE id=? AND status='draft'",
         (content_id,))
    _teach(content_id, 1)
    db.log("publisher", f"content #{content_id} approved for posting")
    return True


def reject(content_id: int) -> bool:
    db.x("UPDATE content SET status='rejected' WHERE id=? AND status='draft'",
         (content_id,))
    _teach(content_id, 0)
    return True


def mark_posted(content_id: int, url: str = "") -> bool:
    db.x("UPDATE content SET status='published', published_url=?, published_at=? "
         "WHERE id=?", (url, time.time(), content_id))
    db.set_metric("content_published",
                  db.scalar("SELECT COUNT(*) FROM content WHERE status='published'"))
    return True
