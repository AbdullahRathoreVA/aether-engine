"""Turn a stored page (its parts) into the final HTML file.

Pages are rendered at publish time, not at write time. That is what keeps the
internal-link graph honest: a page written last month links to a page written
today, because both are rendered against the full page list.

The sanitiser is the trust boundary for page bodies. A body may be written by
an agent that has just read third-party posts, so it is reduced to an allowlist
of harmless tags before it is stored: no scripts, no event handlers, no
javascript: links, no embeds.
"""
from __future__ import annotations

import html
import json
import re
import time
import urllib.parse
from html.parser import HTMLParser

from . import config, seo

# ------------------------------------------------------------- sanitiser -----
_ALLOWED = {"p", "h2", "h3", "h4", "ul", "ol", "li", "strong", "em", "b", "i",
            "code", "pre", "blockquote", "table", "thead", "tbody", "tr", "th",
            "td", "a", "br", "hr"}
_VOID = {"br", "hr"}
_DROP_WITH_CONTENT = {"script", "style", "iframe", "object", "embed", "svg",
                      "math", "template", "noscript", "form", "head", "title"}


def safe_href(href: str) -> str:
    """'' unless the link is http(s), mailto, or a same-site relative page."""
    href = (href or "").strip()
    if re.match(r"^(https?://|mailto:)[^\s<>\"']+$", href, re.I):
        return href
    if re.match(r"^[a-z0-9][a-z0-9\-]*\.html(#[\w\-]*)?$", href):
        return href
    return ""


class _Sanitiser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0          # depth inside a dropped element
        self.open: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in _DROP_WITH_CONTENT:
            self.skip += 1
            return
        if self.skip or tag not in _ALLOWED:
            return
        if tag == "a":
            href = safe_href(dict(attrs).get("href") or "")
            if not href:
                return  # keep the text, drop the link
            ext = href.lower().startswith("http")
            self.out.append(f'<a href="{html.escape(href, quote=True)}"'
                            + (' rel="nofollow noopener"' if ext else "") + ">")
            self.open.append("a")
            return
        self.out.append(f"<{tag}>")
        if tag not in _VOID:
            self.open.append(tag)

    def handle_startendtag(self, tag, attrs):
        if not self.skip and tag in _VOID:
            self.out.append(f"<{tag}>")

    def handle_endtag(self, tag):
        if tag in _DROP_WITH_CONTENT:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip or tag not in self.open:
            return
        while self.open:  # close anything left open inside it
            t = self.open.pop()
            self.out.append(f"</{t}>")
            if t == tag:
                break

    def handle_data(self, data):
        if not self.skip:
            self.out.append(html.escape(data, quote=False))


def sanitize(fragment: str) -> str:
    s = _Sanitiser()
    s.feed(re.sub(r"```html?|```", "", fragment or ""))
    s.close()
    while s.open:
        s.out.append(f"</{s.open.pop()}>")
    return "".join(s.out).strip()


def text_of(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment or "")).split())


def word_count(fragment: str) -> int:
    return len(text_of(fragment).split())


# ------------------------------------------------------------------ shell ----
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
table{{border-collapse:collapse;width:100%;margin:18px 0}}
th,td{{border:1px solid #242c3f;padding:8px 12px;text-align:left}}
.cta{{background:var(--card);border:1px solid #242c3f;border-left:3px solid var(--acc);
border-radius:12px;padding:26px;margin:44px 0}}
.cta h3{{margin:0 0 8px;font-size:1.15rem}}
.cta p{{color:var(--mut);margin:0 0 18px}}
.btn{{display:inline-block;background:var(--acc);color:#111;font-weight:700;
text-decoration:none;padding:13px 26px;border-radius:9px}}
.btn:hover{{filter:brightness(1.08)}}
.show{{margin:40px 0 0;padding:22px;border:1px solid #232b3d;border-radius:12px;
background:linear-gradient(135deg,#0f1420,#141a28)}}
.answer{{background:#11202b;border-left:3px solid #22d3ee;border-radius:10px;
padding:18px 22px;margin:0 0 28px;font-size:1.06rem;line-height:1.65}}
.answer strong{{color:#fff}}
.faq{{margin:44px 0 0}}
.faq h3{{font-size:1.02rem;margin:22px 0 6px;color:#fff}}
.faq p{{color:var(--mut);margin:0}}
.related{{margin:44px 0 0;padding:22px;border:1px solid #232b3d;border-radius:12px}}
.related h2{{margin:0 0 12px;font-size:1.05rem}}
.related ul{{margin:0;padding-left:20px}}
.related li{{margin:7px 0}}
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
{cta}
{showcase}
<footer>
  <a href="{home}">More free guides</a> &middot; {product}
</footer>
</div></body></html>"""


def checkout_link(slug: str) -> str:
    """The buy link, tagged with the page it sits on. '' when not configured."""
    url = config.CHECKOUT_URL
    if not url:
        return ""
    if not config.TRACK_REF or "utm_campaign=" in url:
        return url
    base, hash_, frag = url.partition("#")
    tag = urllib.parse.urlencode({"utm_source": "aether", "utm_medium": "seo",
                                  "utm_campaign": slug})
    return f"{base}{'&' if '?' in base else '?'}{tag}{hash_}{frag}"


def cta_block(slug: str) -> str:
    link = checkout_link(slug)
    if not link:
        return ""  # no dead buy buttons: `status` reports the missing link
    name = config.PRODUCT_NAME
    title = config.CTA_TITLE or f"Get {name}"
    body = config.CTA_BODY or config.PRODUCT_DESCRIPTION
    button = config.CTA_BUTTON or (f"Get it \u2014 {config.PRODUCT_PRICE}"
                                   if config.PRODUCT_PRICE else "Learn more")
    fill = lambda t: t.replace("{product}", name).replace("{price}", config.PRODUCT_PRICE)
    title, body, button = fill(title), fill(body), fill(button)
    return (f'<div class="cta">\n  <h3>{html.escape(title)}</h3>\n'
            f'  <p>{html.escape(body)}</p>\n'
            f'  <a class="btn" href="{html.escape(link, quote=True)}">'
            f'{html.escape(button)}</a>\n</div>')


def showcase_block() -> str:
    """The optional second offer. Rendered only when its owner configured it."""
    if not config.SHOWCASE_ENABLED:
        return ""
    contact = config.SERVICE_CONTACT
    ok = contact.startswith("mailto:") or safe_href(contact)
    hire = (f'<a class="btn2" href="{html.escape(contact, quote=True)}" '
            f'style="margin-left:8px">Hire me from '
            f'{html.escape(config.SERVICE_PRICE_FROM)}</a>' if contact and ok else "")
    return f"""<div class="show">
  <div class="eyebrow">Need this built for your business?</div>
  <h3>{html.escape(config.SHOWCASE_NAME)}</h3>
  <p>This page was written and published by an autonomous system I built.
  {html.escape(config.SHOWCASE_NAME)} is {html.escape(config.SHOWCASE_TAGLINE)}
  &mdash; open it and test it yourself, no signup. I build
  {html.escape(config.SERVICE_NAME)} for clients, and the live system is the
  portfolio.</p>
  <a class="btn2" href="{html.escape(config.SHOWCASE_URL, quote=True)}"
     target="_blank" rel="noopener">Open the live system &rarr;</a>{hire}
</div>"""


def faqs_of(row) -> list[tuple[str, str]]:
    try:
        raw = json.loads(row["faqs"] or "[]")
    except (ValueError, TypeError):
        return []
    out = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict):
            q, a = item.get("q"), item.get("a")
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            q, a = item
        else:
            continue
        if q and a:
            out.append((str(q).strip(), str(a).strip()))
    return out


def page_html(row, all_pages: list) -> str:
    """Final HTML for one stored page. `all_pages` is every page row."""
    slug, title = row["slug"], row["title"]
    body = row["body"] or ""
    answer = (row["answer"] or "").strip()
    faqs = faqs_of(row)
    desc = (row["description"] or "").strip()
    base = config.SITE_BASE_URL.rstrip("/")
    canonical = f"{base}/{slug}.html" if base else f"{slug}.html"
    created = time.localtime(row["created_at"] or time.time())
    updated = time.localtime(row["updated_at"] or row["created_at"] or time.time())

    content = ""
    if answer:
        # Answer-first: state the answer before anything else on the page.
        lead, sep, rest = answer.partition(". ")
        if not sep or len(lead) < 40:
            lead, sep, rest = answer, "", ""
        content += (f'<div class="answer"><strong>{html.escape(lead + sep.strip())}'
                    f'</strong>{" " + html.escape(rest) if rest else ""}</div>')
    content += body
    if faqs:
        qa = "".join(f"<h3>{html.escape(q)}</h3><p>{html.escape(a)}</p>"
                     for q, a in faqs)
        content += f'<section class="faq"><h2>Common questions</h2>{qa}</section>'
    content += seo.related_block(slug, all_pages)

    words = word_count(content)
    schema = seo.build_schema(
        title, desc, canonical, row["role"] or row["keyword"] or title, faqs,
        time.strftime("%Y-%m-%d", created), time.strftime("%Y-%m-%d", updated))

    return PAGE_SHELL.format(
        title=html.escape(title), h1=html.escape(title),
        desc=html.escape(desc, quote=True), canonical=html.escape(canonical, quote=True),
        schema=schema, content=content,
        date=time.strftime("%d %B %Y", updated), readtime=max(2, words // 220),
        cta=cta_block(slug), showcase=showcase_block(),
        home=f"{base}/index.html" if base else "index.html",
        product=html.escape(config.PRODUCT_NAME),
    )


# -------------------------------------------------------- legacy migration ---
def legacy_parts(page: str) -> dict | None:
    """Recover the parts from a page stored as finished HTML by version 1.

    Returns None when the page does not look like ours, in which case the
    caller keeps publishing the stored HTML untouched.
    """
    m = re.search(r'<div class="meta">.*?</div>\s*(.*?)\s*<div class="cta">',
                  page or "", re.S)
    if not m:
        return None
    content = m.group(1)
    content = re.sub(r'<nav class="related".*?</nav>', "", content, flags=re.S)

    answer = ""
    am = re.search(r'<div class="answer">(.*?)</div>', content, re.S)
    if am:
        answer = text_of(am.group(1))
        content = content.replace(am.group(0), "")

    faqs = []
    fm = re.search(r'<section class="faq">(.*?)</section>', content, re.S)
    if fm:
        faqs = [{"q": text_of(q), "a": text_of(a)} for q, a in
                re.findall(r"<h3>(.*?)</h3>\s*<p>(.*?)</p>", fm.group(1), re.S)]
        content = content.replace(fm.group(0), "")

    dm = re.search(r'<meta name="description" content="(.*?)">', page, re.S)
    return {"body": sanitize(content), "answer": answer,
            "faqs": json.dumps(faqs),
            "description": html.unescape(dm.group(1)) if dm else ""}
