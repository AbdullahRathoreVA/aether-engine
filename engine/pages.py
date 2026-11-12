"""Storing pages: validation, the quality gate, and the page list.

A page arrives as its parts (title, description, answer, body, FAQs), whether
an agent wrote it from a brief or the matrix builder produced it. The parts are
checked and sanitised here, stored, and rendered into HTML only at publish.
"""
from __future__ import annotations

import json
import re
import time

from . import config, db, render

MIN_WORDS = 300
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:80].strip("-")


def _clean(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def add(data: dict, *, replace: bool = False, allow_thin: bool = False) -> dict:
    """Validate and store one page. Returns {"slug", "words", "warnings"} or
    raises ValueError listing every problem at once."""
    if not isinstance(data, dict):
        raise ValueError("page must be a JSON object")
    errors: list[str] = []
    warnings: list[str] = []

    title = _clean(data.get("title"), 200)
    keyword = _clean(data.get("keyword"), 120).lower()
    slug = str(data.get("slug") or "").strip() or slugify(keyword or title)
    desc = _clean(data.get("description"), 400)
    answer = _clean(data.get("answer"), 1000)
    body = render.sanitize(str(data.get("body_html") or data.get("body") or ""))
    words = render.word_count(body)

    if not 10 <= len(title) <= 110:
        errors.append("title must be 10-110 characters")
    elif len(title) > 65:
        warnings.append(f"title is {len(title)} characters; search results cut it near 60")
    if not _SLUG.match(slug) or len(slug) > 80 or slug == "index":
        errors.append("slug must be lowercase letters, digits and hyphens (max 80), not 'index'")
    if not 50 <= len(desc) <= 200:
        errors.append("description must be 50-200 characters (aim for 120-160)")
    elif not 70 <= len(desc) <= 165:
        warnings.append(f"description is {len(desc)} characters; aim for 70-160")
    if not 40 <= len(answer) <= 600:
        errors.append("answer must be 40-600 characters: the direct answer, in one or two sentences")
    if words < MIN_WORDS and not allow_thin:
        errors.append(f"body has {words} words; the minimum is {MIN_WORDS} "
                      "(thin pages do not rank) - write more or pass --allow-thin")
    if body.count("<h2>") < 2:
        warnings.append("body has fewer than two <h2> sections; it will be hard to skim")

    faqs = []
    for item in data.get("faqs") or []:
        q = _clean(item.get("q") if isinstance(item, dict) else (item[0] if item else ""), 300)
        a = _clean(item.get("a") if isinstance(item, dict) else (item[1] if len(item) > 1 else ""), 1200)
        if q and a:
            faqs.append({"q": q, "a": a})
    faqs = faqs[:8]
    if len(faqs) < 2:
        warnings.append("fewer than 2 FAQs; FAQ schema is what answer engines quote")

    existing = db.one("SELECT id,created_at FROM pages WHERE slug=?", (slug,)) if _SLUG.match(slug) else None
    if existing and not replace:
        errors.append(f"a page with slug '{slug}' exists - pass --replace to overwrite it")
    clash = db.one("SELECT slug FROM pages WHERE lower(title)=? AND slug!=?",
                   (title.lower(), slug))
    if clash:
        errors.append(f"page '{clash['slug']}' already has this exact title")
    if errors:
        raise ValueError("; ".join(errors))

    now = time.time()
    subject = _clean(data.get("subject"), 120)
    cluster = _clean(data.get("cluster"), 120).lower()
    args = (title, subject, keyword or title.lower(), body, answer,
            json.dumps(faqs), desc, cluster, now)
    if existing:
        db.x("UPDATE pages SET title=?,role=?,keyword=?,body=?,answer=?,faqs=?,"
             "description=?,cluster=?,updated_at=?,published=0,html='' WHERE id=?",
             args + (existing["id"],))
    else:
        db.x("INSERT INTO pages(title,role,keyword,body,answer,faqs,description,"
             "cluster,updated_at,slug,html,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,'',?)",
             args + (slug, now))
    db.set_metric("pages_total", db.scalar("SELECT COUNT(*) FROM pages"))
    return {"slug": slug, "words": words, "faqs": len(faqs), "warnings": warnings,
            "next": "run `publish`, then `audit`"}


def listing() -> list[dict]:
    return [dict(r) for r in db.q(
        "SELECT slug,title,keyword,cluster,published,created_at FROM pages ORDER BY slug")]


def remove(slug: str) -> bool:
    """Delete a page and its published file."""
    if not _SLUG.match(slug or "") or not db.one("SELECT 1 FROM pages WHERE slug=?", (slug,)):
        return False
    db.x("DELETE FROM pages WHERE slug=?", (slug,))
    path = config.SITE_DIR / f"{slug}.html"
    if path.exists():
        path.unlink()
    return True
