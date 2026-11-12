"""Technical SEO: internal links, structured data, and a measured audit.

What this module enforces, and why:

  - No orphan pages. A page with zero inbound internal links cannot be
    discovered and passes no authority. Links are computed at publish time
    against the whole site, plus a ring that makes zero orphans structural.
  - Structured data on every page: Article, FAQPage and BreadcrumbList in one
    JSON-LD graph. A question/answer pair is the easiest thing for a search or
    answer engine to lift and attribute.
  - Answer first: the page states its answer in the opening sentences.

Everything here is deterministic. No model call is needed to add a link or a
schema block, and the audit reads the files on disk, so it reports what is
actually published rather than what was intended.
"""
from __future__ import annotations

import html
import json
import re
from html.parser import HTMLParser

from . import config, db


def _g(row, key: str):
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


# ---------------------------------------------------------------- clusters ---
def _matrix_prefixes() -> list[str]:
    return [tpl.split("{role}")[0].strip("-") for tpl, _ in config.SEO_TEMPLATES]


def cluster_of(row) -> str:
    """A page's topic cluster: stored, or recovered from a matrix slug."""
    if not isinstance(row, str):
        stored = _g(row, "cluster")
        if stored:
            return stored
        slug = row["slug"]
    else:
        slug = row
    for prefix in _matrix_prefixes():
        if prefix and slug.startswith(prefix):
            return prefix
    return ""


def role_of(row) -> str:
    """A page's subject (the matrix 'role'): stored, or recovered from its slug."""
    if not isinstance(row, str):
        if _g(row, "role"):
            return row["role"]
        slug = row["slug"]
    else:
        slug = row
    for tpl, _ in config.SEO_TEMPLATES:
        pattern = "^" + re.escape(tpl).replace(re.escape("{role}"), "(.+)") + "$"
        m = re.match(pattern, slug)
        if m:
            return m.group(1).replace("-", " ")
    return ""


# ----------------------------------------------------------- internal links --
def related_links(slug: str, all_pages: list | None = None,
                  limit: int = 6) -> list[tuple[str, str]]:
    """Relevant neighbours first (same subject, then same cluster), then a ring.

    Relevance alone leaves stragglers: nothing ever picks the least similar
    page. So every page also links to its successor in a stable sorted order,
    which closes a ring across the site and makes zero orphans structural.
    """
    if all_pages is None:
        all_pages = db.q("SELECT slug,title,role,cluster FROM pages")
    me = next((r for r in all_pages if r["slug"] == slug), None)
    rows = [r for r in all_pages if r["slug"] != slug]
    if not rows:
        return []
    me_role = role_of(me if me is not None else slug)
    me_cluster = cluster_of(me if me is not None else slug)

    same_role, same_cluster, other = [], [], []
    for r in sorted(rows, key=lambda r: r["slug"]):
        pair = (r["slug"], r["title"])
        if me_role and role_of(r) == me_role:
            same_role.append(pair)
        elif me_cluster and cluster_of(r) == me_cluster:
            same_cluster.append(pair)
        else:
            other.append(pair)

    # Trim the relevance picks first, then append the ring link, so the ring
    # link can never fall off the end of the slice.
    picked = (same_role[:3] + same_cluster[:2] + other[:2])[:limit]
    if len(picked) < min(limit, len(rows)):
        seen = {s for s, _ in picked}
        for pair in same_role + same_cluster + other:
            if pair[0] not in seen and len(picked) < limit:
                picked.append(pair)
                seen.add(pair[0])

    all_slugs = sorted([r["slug"] for r in rows] + [slug])
    nxt = all_slugs[(all_slugs.index(slug) + 1) % len(all_slugs)]
    if nxt != slug and nxt not in {s for s, _ in picked}:
        picked.append((nxt, next(r["title"] for r in rows if r["slug"] == nxt)))
    return picked


def related_block(slug: str, all_pages: list | None = None) -> str:
    """Rendered related-pages section. Descriptive anchors, never 'click here'."""
    links = related_links(slug, all_pages)
    if not links:
        return ""
    items = "".join(
        f'<li><a href="{html.escape(s)}.html">{html.escape(t)}</a></li>'
        for s, t in links)
    return (f'<nav class="related" aria-label="Related guides">'
            f'<h2>Related guides</h2><ul>{items}</ul></nav>')


# ------------------------------------------------------------------ schema ---
def build_schema(title: str, desc: str, canonical: str, about: str,
                 faqs: list[tuple[str, str]], published: str,
                 modified: str = "") -> str:
    """One JSON-LD @graph: Article + FAQPage + BreadcrumbList."""
    base = config.SITE_BASE_URL.rstrip("/")
    article: dict = {
        "@type": "Article",
        "headline": title[:110],
        "description": desc,
        "datePublished": published,
        "dateModified": modified or published,
        "inLanguage": "en",
        "mainEntityOfPage": {"@type": "WebPage", "@id": canonical},
    }
    if about:
        article["about"] = {"@type": "Thing", "name": about}
    if config.SITE_AUTHOR:
        article["author"] = {"@type": "Person", "name": config.SITE_AUTHOR}
    graph: list[dict] = [article]

    if faqs:
        graph.append({
            "@type": "FAQPage",
            "mainEntity": [
                {"@type": "Question", "name": q,
                 "acceptedAnswer": {"@type": "Answer", "text": a}}
                for q, a in faqs
            ],
        })
    if base:
        graph.append({
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Guides",
                 "item": f"{base}/index.html"},
                {"@type": "ListItem", "position": 2, "name": title[:70],
                 "item": canonical},
            ],
        })
    # No "<" may appear inside the <script> block, whatever the text contains:
    # "</script" would close it and "<!--<script" changes how it is parsed.
    return json.dumps({"@context": "https://schema.org", "@graph": graph},
                      separators=(",", ":")).replace("<", "\\u003c")


# ------------------------------------------------------------------ audit ----
class _Scan(HTMLParser):
    """Pull the SEO-relevant facts out of one HTML file."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = self.desc = self.canonical = ""
        self.h1 = 0
        self.hrefs: list[str] = []
        self.ld: list[str] = []
        self.words = 0
        self.has_answer = False
        self._in: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta" and (a.get("name") or "").lower() == "description":
            self.desc = a.get("content") or ""
        elif tag == "link" and (a.get("rel") or "").lower() == "canonical":
            self.canonical = a.get("href") or ""
        elif tag == "h1":
            self.h1 += 1
        elif tag == "a" and a.get("href"):
            self.hrefs.append(a["href"])
        elif tag == "div" and "answer" in (a.get("class") or "").split():
            self.has_answer = True
        if tag == "script" and a.get("type") == "application/ld+json":
            self._in.append("ld")
            self.ld.append("")
        elif tag in ("script", "style", "title"):
            self._in.append(tag)

    def handle_endtag(self, tag):
        if tag in ("script", "style", "title") and self._in:
            self._in.pop()

    def handle_data(self, data):
        where = self._in[-1] if self._in else ""
        if where == "ld":
            self.ld[-1] += data
        elif where == "title":
            self.title += data
        elif not where:
            self.words += len(data.split())


def _types(ld_blocks: list[str]) -> tuple[set[str], bool]:
    """(schema types found, True if every JSON-LD block parsed)."""
    found: set[str] = set()
    ok = True
    for block in ld_blocks:
        try:
            data = json.loads(block)
        except ValueError:
            ok = False
            continue
        nodes = data.get("@graph", [data]) if isinstance(data, dict) else data
        for node in nodes if isinstance(nodes, list) else []:
            t = node.get("@type") if isinstance(node, dict) else None
            for name in t if isinstance(t, list) else [t]:
                if name:
                    found.add(str(name))
    return found, ok


def audit() -> dict:
    """Measure the published site from the files on disk. Nothing is estimated."""
    pages = db.q("SELECT slug,title FROM pages WHERE published=1 ORDER BY slug")
    total = len(pages)
    if not total:
        return {"pages": 0, "note": "nothing published yet"}

    site, base = config.SITE_DIR, config.SITE_BASE_URL.rstrip("/")
    slugs = {p["slug"] for p in pages}
    inbound = {s: 0 for s in slugs}
    issues: list[dict] = []
    titles: dict[str, list[str]] = {}
    descs: dict[str, list[str]] = {}
    with_schema = with_faq = with_answer = with_cta = scanned = 0

    def flag(slug: str, severity: str, issue: str) -> None:
        issues.append({"page": slug, "severity": severity, "issue": issue})

    for p in pages:
        slug = p["slug"]
        path = site / f"{slug}.html"
        if not path.exists():
            flag(slug, "error", "marked published but the file is missing - run `publish`")
            continue
        s = _Scan()
        s.feed(path.read_text(encoding="utf-8", errors="replace"))
        scanned += 1

        title = " ".join(s.title.split())
        titles.setdefault(title.lower(), []).append(slug)
        if not title:
            flag(slug, "error", "no <title>")
        elif len(title) > 65:
            flag(slug, "warn", f"title is {len(title)} characters; search results cut it near 60")
        desc = " ".join(s.desc.split())
        if not desc:
            flag(slug, "error", "no meta description")
        else:
            descs.setdefault(desc.lower(), []).append(slug)
            if not 70 <= len(desc) <= 165:
                flag(slug, "warn", f"meta description is {len(desc)} characters; aim for 70-160")
        if s.h1 != 1:
            flag(slug, "error", f"{s.h1} <h1> tags; a page needs exactly one")
        if not s.canonical:
            flag(slug, "error", "no canonical link")
        elif base and s.canonical != f"{base}/{slug}.html":
            flag(slug, "warn", "canonical does not match site.base_url - run `publish`")

        types, parsed = _types(s.ld)
        if not parsed:
            flag(slug, "error", "a JSON-LD block does not parse")
        if types:
            with_schema += 1
        else:
            flag(slug, "error", "no structured data")
        if "FAQPage" in types:
            with_faq += 1
        if s.has_answer:
            with_answer += 1
        if s.words < 300:
            flag(slug, "warn", f"thin content: {s.words} words")

        for href in s.hrefs:
            m = re.match(r"^([a-z0-9][a-z0-9\-]*)\.html(?:#.*)?$", href)
            if not m:
                continue
            target = m.group(1)
            if target in inbound and target != slug:
                inbound[target] += 1
            elif target != "index" and target != slug and not (site / f"{target}.html").exists():
                flag(slug, "error", f"broken internal link to {target}.html")
        if config.CHECKOUT_URL and any(h.startswith(config.CHECKOUT_URL.split("#")[0].split("?")[0])
                                       for h in s.hrefs):
            with_cta += 1

    for label, groups in (("title", titles), ("meta description", descs)):
        for text, group in groups.items():
            if text and len(group) > 1:
                flag(group[0], "warn", f"same {label} as {', '.join(group[1:4])}")

    orphans = sorted(s for s, n in inbound.items() if n == 0) if total > 1 else []
    for s in orphans:
        flag(s, "error", "orphan: no other page links here - run `publish` to relink")

    sitemap = "skipped: site.base_url is not set"
    if base:
        sm = site / "sitemap.xml"
        text = sm.read_text(encoding="utf-8", errors="replace") if sm.exists() else ""
        missing = [s for s in sorted(slugs) if f"{base}/{s}.html" not in text]
        sitemap = ("missing - run `publish`" if not text else
                   f"{len(missing)} page(s) not listed - run `publish`" if missing
                   else "lists every page")
        if not text or missing:
            flag("sitemap.xml", "error", sitemap)

    errors = sum(1 for i in issues if i["severity"] == "error")
    return {
        "pages": total,
        "files_checked": scanned,
        "errors": errors,
        "warnings": len(issues) - errors,
        "orphans": len(orphans),
        "orphan_pct": round(100 * len(orphans) / total),
        "avg_inbound": round(sum(inbound.values()) / total, 1),
        "with_schema": with_schema,
        "with_faq": with_faq,
        "with_answer_first": with_answer,
        "with_buy_link": with_cta,
        "sitemap": sitemap,
        "buy_link": "set" if config.CHECKOUT_URL else "not set - pages carry no buy button",
        "issues": issues[:60],
        "issues_not_shown": max(0, len(issues) - 60),
        "health": _health(total, len(orphans), with_faq, with_answer, errors),
    }


def _health(total: int, orphans: int, faq: int, answer: int, errors: int = 0) -> list[str]:
    out = []
    if orphans:
        out.append(f"{orphans} orphan page(s) - no internal links point to them, "
                   f"so they cannot rank or pass authority")
    if faq < total:
        out.append(f"{total - faq} page(s) missing FAQ schema - the easiest "
                   f"thing for an answer engine to cite")
    if answer < total:
        out.append(f"{total - answer} page(s) do not answer in the first sentences")
    if errors and not out:
        out.append(f"{errors} error(s) - see issues")
    if not out and not errors:
        out.append("all published pages are linked, schema'd and answer-first")
    return out
