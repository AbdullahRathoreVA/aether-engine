"""Content synthesis.

Two products come out of here:

1. Reply drafts for high-intent signals. These are genuinely useful answers that
   mention the toolkit only where it actually fits. They go to an approval queue
   and are never auto-posted - see publisher.py for why that boundary exists.

2. Programmatic SEO pages. This is the real 24/7 compounding channel: long-tail
   pages on our own domain, published unattended, each funnelling to checkout.
   No platform can ban us from our own site.
"""
from __future__ import annotations

import html
import random
import re
import time

from . import config, db, llm, scorer

REPLY_SYSTEM = (
    "You are an experienced career coach answering on a public forum. You give "
    "concrete, immediately usable advice. You never open with flattery, never "
    "say 'great question', and never sound like marketing copy. You write in "
    "plain language, 120-200 words. If a paid resource is genuinely relevant you "
    "may mention it once at the end in a single low-key sentence - and if it is "
    "not relevant, you mention nothing."
)

PAGE_SYSTEM = (
    "You write practical career-advice web pages. Concrete, specific, skimmable. "
    "No filler, no 'in today's competitive job market', no restating the title."
)


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


# --------------------------------------------------------------- replies -----
def generate_replies(limit: int = 3) -> int:
    signals = scorer.top_signals(limit)
    if not signals:
        return 0

    made = 0
    for sig in signals:
        existing = db.one("SELECT id FROM content WHERE signal_id=?", (sig["id"],))
        if existing:
            db.x("UPDATE signals SET status='used' WHERE id=?", (sig["id"],))
            continue

        prompt = (
            f"Someone posted this on {sig['source']}:\n\n"
            f"Title: {sig['title']}\n\n{(sig['body'] or '')[:1500]}\n\n"
            "Write a reply that solves their actual problem. Be specific enough "
            "that they could act on it in the next ten minutes."
        )
        body = llm.generate(prompt, system=REPLY_SYSTEM)

        if not body:
            body = llm.template_post(config.PRODUCT_NAME, seed=sig["id"])

        db.x(
            "INSERT INTO content(signal_id,kind,platform,title,body,created_at,status)"
            " VALUES(?,?,?,?,?,?,'draft')",
            (sig["id"], "reply", sig["source"],
             f"Re: {sig['title'][:120]}", body, time.time()),
        )
        db.x("UPDATE signals SET status='used' WHERE id=?", (sig["id"],))
        made += 1

    if made:
        db.log("generator", f"drafted {made} replies -> approval queue")
    db.set_metric("last_generate", time.time())
    return made


# ------------------------------------------------------------ social posts ---
def generate_social_post() -> int:
    """One standalone value post per cycle, for LinkedIn/X/Threads."""
    role = random.choice(config.SEO_ROLES)
    prompt = (
        f"Write a short LinkedIn post (90-140 words) giving one specific, "
        f"non-obvious tip for a {role} whose job applications keep getting "
        f"auto-rejected. Start with a concrete observation, not a question. "
        f"No hashtags, no emoji."
    )
    body = llm.generate(prompt, system=REPLY_SYSTEM) or llm.template_post(
        f"{role} applications", seed=int(time.time()) % 9999)

    db.x(
        "INSERT INTO content(kind,platform,title,body,created_at,status)"
        " VALUES(?,?,?,?,?,'draft')",
        ("post", "linkedin", f"Daily post - {role}", body, time.time()),
    )
    return 1


# ------------------------------------------------------------- seo pages -----
PAGE_SHELL = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<meta name="description" content="{desc}">
<link rel="canonical" href="{canonical}">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{desc}">
<meta property="og:type" content="article">
<script type="application/ld+json">{schema}</script>
<style>
:root{{--bg:#0b0d12;--fg:#e6e9f0;--mut:#9aa4b8;--acc:#ffc857;--card:#141824}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);
font:16px/1.7 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}}
.wrap{{max-width:760px;margin:0 auto;padding:48px 20px 80px}}
h1{{font-size:2.1rem;line-height:1.25;margin:0 0 8px}}
h2{{font-size:1.35rem;margin:40px 0 12px;color:#fff}}
.meta{{color:var(--mut);font-size:.9rem;margin-bottom:32px}}
ul{{padding-left:22px}} li{{margin:8px 0}}
code{{background:#1b2030;padding:2px 7px;border-radius:5px;font-size:.9em}}
.cta{{background:var(--card);border:1px solid #242c3f;border-left:3px solid var(--acc);
border-radius:12px;padding:26px;margin:44px 0}}
.cta h3{{margin:0 0 8px;font-size:1.15rem}}
.cta p{{color:var(--mut);margin:0 0 18px}}
.btn{{display:inline-block;background:var(--acc);color:#111;font-weight:700;
text-decoration:none;padding:13px 26px;border-radius:9px}}
.btn:hover{{filter:brightness(1.08)}}
.show{{margin:40px 0 0;padding:22px;border:1px solid #232b3d;border-radius:12px;
background:linear-gradient(135deg,#0f1420,#141a28)}}
.show .eyebrow{{font-size:.72rem;letter-spacing:2px;color:#22d3ee;
text-transform:uppercase;margin-bottom:8px}}
.show h3{{margin:0 0 8px;font-size:1.1rem;color:#fff}}
.show p{{color:var(--mut);margin:0 0 14px;font-size:.94rem}}
.btn2{{display:inline-block;border:1px solid #2c3550;
color:var(--fg);text-decoration:none;padding:10px 20px;border-radius:8px;
font-size:.9rem}}
.btn2:hover{{border-color:#22d3ee;color:#22d3ee}}
footer{{margin-top:56px;padding-top:24px;border-top:1px solid #1e2433;
color:var(--mut);font-size:.85rem}}
a{{color:var(--acc)}}
</style></head><body><div class="wrap">
<h1>{h1}</h1>
<div class="meta">Updated {date} &middot; {readtime} min read</div>
{content}
<div class="cta">
  <h3>{cta_title}</h3>
  <p>{cta_body}</p>
  <a class="btn" href="{checkout}">{cta_button}</a>
</div>
{showcase}
<footer>
  <a href="{home}">More free guides</a> &middot; {product}
</footer>
</div></body></html>"""


def showcase_block() -> str:
    """The high-ticket half of the funnel.

    The $14 toolkit converts strangers. This converts the rare visitor who does
    not want a template - they want the person who can build the system. Same
    traffic, same page, second price point, and it costs nothing to include.
    """
    if not (config.SHOWCASE_ENABLED and config.SHOWCASE_URL):
        return ""

    contact = config.SERVICE_CONTACT
    hire = (f'<a class="btn2" href="{html.escape(contact)}" '
            f'style="margin-left:8px">Hire me from {html.escape(config.SERVICE_PRICE_FROM)}</a>'
            if contact else "")

    return f"""<div class="show">
  <div class="eyebrow">Built by the same person</div>
  <h3>{html.escape(config.SHOWCASE_NAME)}</h3>
  <p>These guides are written by an autonomous system I built &mdash; and
  {html.escape(config.SHOWCASE_NAME)} is {html.escape(config.SHOWCASE_TAGLINE)}.
  If you need {html.escape(config.SERVICE_NAME)} for your own business, the live
  demo is the portfolio.</p>
  <a class="btn2" href="{html.escape(config.SHOWCASE_URL)}"
     target="_blank" rel="noopener">See the live demo &rarr;</a>{hire}
</div>"""


def _fallback_page_content(role: str, kind: str) -> str:
    """Deterministic, genuinely useful content when no model is available."""
    common = [
        "Cross-reference three live job postings for the same title and list every "
        "hard skill that appears in at least two of them.",
        "Put those exact strings in your skills section, spelled the way the posting "
        "spells them. <code>PostgreSQL</code> and <code>Postgres</code> are different "
        "tokens to a parser.",
        "Rewrite each bullet as action verb + scope + measurable result.",
        "Keep the layout single-column. Tables, text boxes, and headers/footers are "
        "where parsers lose content.",
        "Save as <code>.docx</code> unless the posting explicitly asks for PDF.",
    ]
    items = "".join(f"<li>{c}</li>" for c in common)
    return (
        f"<p>If you are applying for {html.escape(role)} roles and not hearing back, "
        f"the bottleneck is usually parsing and keyword match, not your experience.</p>"
        f"<h2>What to do this week</h2><ul>{items}</ul>"
        f"<h2>How to check your work</h2>"
        f"<p>Paste your resume and the job description into any AI assistant and ask: "
        f"<em>&ldquo;List the hard requirements in this posting that my resume does not "
        f"mention.&rdquo;</em> Fix the ones you can honestly claim. Ignore the rest.</p>"
    )


def build_page(slug: str, title: str, role: str) -> str:
    prompt = (
        f"Write the body of a web page titled '{title}'.\n"
        f"Audience: someone applying for {role} jobs who keeps getting rejected.\n"
        "Structure: one short intro paragraph, then 2-3 <h2> sections with <ul> "
        "lists of specific, concrete items. Output raw HTML fragments only "
        "(<p>, <h2>, <ul>, <li>, <code>). No <html>, <head>, or <body> tags. "
        "No CTA - one is appended separately. 400-600 words."
    )
    content = llm.generate(prompt, system=PAGE_SYSTEM, max_tokens=1600)

    if not content or "<" not in content:
        content = _fallback_page_content(role, slug)

    content = re.sub(r"```html?|```", "", content).strip()

    base = config.SITE_BASE_URL.rstrip("/")
    canonical = f"{base}/{slug}.html" if base else f"{slug}.html"
    desc = (f"Practical, specific guidance for {role} job seekers. "
            f"Free guide, updated {time.strftime('%B %Y')}.")
    words = len(re.sub(r"<[^>]+>", " ", content).split())

    schema = (
        '{"@context":"https://schema.org","@type":"Article",'
        f'"headline":"{html.escape(title)}",'
        f'"datePublished":"{time.strftime("%Y-%m-%d")}",'
        f'"description":"{html.escape(desc)}"}}'
    )

    checkout = config.CHECKOUT_URL or "#checkout-not-configured"

    return PAGE_SHELL.format(
        title=html.escape(title), h1=html.escape(title), desc=html.escape(desc),
        canonical=canonical, schema=schema, content=content,
        date=time.strftime("%d %B %Y"), readtime=max(2, words // 220),
        checkout=html.escape(checkout),
        cta_title=f"Skip the rewriting: get the {config.PRODUCT_NAME}",
        cta_body=("ATS-ready resume and cover letter templates, 100 AI prompts for "
                  "the whole job search, a LinkedIn checklist, and an application "
                  "tracker. Instant download."),
        cta_button=f"Get instant access &mdash; {config.PRODUCT_PRICE}",
        showcase=showcase_block(),
        home=f"{base}/index.html" if base else "index.html",
        product=html.escape(config.PRODUCT_NAME),
    )


def generate_seo_pages(count: int = 2) -> int:
    """Create pages we have not built yet, newest gaps first."""
    made = 0
    combos = [(r, tpl, title) for r in config.SEO_ROLES
              for tpl, title in config.SEO_TEMPLATES]
    random.shuffle(combos)

    for role, tpl, title_tpl in combos:
        if made >= count:
            break
        slug = tpl.format(role=slugify(role))
        if db.one("SELECT id FROM pages WHERE slug=?", (slug,)):
            continue

        title = title_tpl.format(Role=role.title(), role=role)
        try:
            page_html = build_page(slug, title, role)
        except Exception as e:
            db.log("generator", f"page {slug} failed: {type(e).__name__}", "error")
            continue

        db.x(
            "INSERT INTO pages(slug,title,role,keyword,html,created_at)"
            " VALUES(?,?,?,?,?,?)",
            (slug, title, role, title, page_html, time.time()),
        )
        made += 1
        db.log("generator", f"built SEO page: {slug}")

    total = db.scalar("SELECT COUNT(*) FROM pages")
    db.set_metric("pages_total", total)
    return made
