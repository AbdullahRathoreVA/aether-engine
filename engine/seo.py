"""Technical SEO built from measured 2026 ranking evidence, not folklore.

Research this was built from (July 2026):

  - 40-60% of pages on a typical site have ZERO inbound internal links. Those
    orphans cannot be discovered, cannot rank, and contribute nothing to topical
    authority. Measured on our own site before this module existed: 35 of 35
    pages were orphans. That was the single biggest defect in the whole engine.
  - Only ~17% of the top 10M sites implement schema at all, and schema has moved
    from "rich result candy" to core AI-citation infrastructure - it is how AI
    Overviews, ChatGPT Search and Perplexity decide what to quote.
  - The strongest single AI-search factor is answering the question in the FIRST
    1-2 sentences. Burying the answer loses the citation.
  - The overlap between Google's top-10 and the sources AI actually cites fell
    from ~75% (mid-2025) to 17-38% (early 2026). Winning the old game no longer
    wins the new one, so we optimise for citation, not just ranking.

Everything here is deterministic. No model call is needed to add a link or a
schema block, so this works identically whether or not an LLM is reachable.
"""
from __future__ import annotations

import html
import json
import re

from . import config, db

# ---------------------------------------------------------------- clusters ---
# Hub-and-spoke: each topic family is a cluster. Pages inside a cluster link to
# each other with descriptive anchors, and every page links up to its hub. This
# is what turns 35 orphans into a topical graph.
CLUSTERS = {
    "ats-resume-keywords": {
        "hub": "ATS resume keywords",
        "label": "ATS & keyword optimisation",
    },
    "resume-summary-examples": {
        "hub": "resume summary examples",
        "label": "Resume writing",
    },
    "cover-letter-template": {
        "hub": "cover letter templates",
        "label": "Cover letters",
    },
    "linkedin-headline-examples": {
        "hub": "LinkedIn headline examples",
        "label": "LinkedIn & recruiter search",
    },
    "ai-prompts-for": {
        "hub": "AI prompts for job search",
        "label": "AI-assisted job search",
    },
}


def cluster_of(slug: str) -> str | None:
    for key in CLUSTERS:
        if slug.startswith(key):
            return key
    return None


def role_of(slug: str) -> str:
    """Recover the role from a slug like ats-resume-keywords-for-nurse."""
    for key in CLUSTERS:
        if slug.startswith(key):
            rest = slug[len(key):].strip("-")
            rest = re.sub(r"^for-", "", rest)
            rest = re.sub(r"-job-search$", "", rest)
            return rest.replace("-", " ")
    return slug.replace("-", " ")


# ----------------------------------------------------------- internal links --
def related_links(slug: str, limit: int = 6) -> list[tuple[str, str]]:
    """Pick genuinely relevant neighbours: same role first, then same cluster.

    Same-role links are the valuable ones - a nurse reading about ATS keywords
    wants the nurse cover-letter page, not a devops one. Falling back to the
    cluster keeps every page linked even when a role has only one page.
    """
    me_cluster = cluster_of(slug)
    me_role = role_of(slug)

    rows = db.q("SELECT slug, title FROM pages WHERE published=1 AND slug != ?",
                (slug,))

    same_role, same_cluster, other = [], [], []
    for r in rows:
        s = r["slug"]
        pair = (s, r["title"])
        if role_of(s) == me_role:
            same_role.append(pair)
        elif cluster_of(s) == me_cluster:
            same_cluster.append(pair)
        else:
            other.append(pair)

    picked = same_role[:3] + same_cluster[:2] + other[:2]

    # Guaranteed-coverage ring. Relevance-based picking alone leaves stragglers:
    # measured 1 of 37 pages still had zero inbound links because nothing ever
    # chose it. Every page additionally links to its successor in a stable
    # sorted order, which makes a closed ring across the whole site - so the
    # orphan count is structurally zero, not just usually zero.
    all_slugs = sorted(r["slug"] for r in rows) + [slug]
    all_slugs.sort()
    idx = all_slugs.index(slug)
    nxt = all_slugs[(idx + 1) % len(all_slugs)]
    if nxt != slug and nxt not in {s for s, _ in picked}:
        title = next((r["title"] for r in rows if r["slug"] == nxt), nxt)
        picked.append((nxt, title))

    return picked[:limit + 1]


def related_block(slug: str) -> str:
    """Rendered related-pages section. Descriptive anchors, never 'click here'."""
    links = related_links(slug)
    if not links:
        return ""
    items = "".join(
        f'<li><a href="{html.escape(s)}.html">{html.escape(t)}</a></li>'
        for s, t in links)
    return (f'<nav class="related" aria-label="Related guides">'
            f'<h2>Related guides</h2><ul>{items}</ul></nav>')


# ------------------------------------------------------------------ schema ---
def build_schema(title: str, desc: str, canonical: str, role: str,
                 faqs: list[tuple[str, str]], date: str) -> str:
    """A @graph carrying Article + FAQPage + Breadcrumb.

    FAQPage matters disproportionately now: a direct question/answer pair is the
    easiest thing for an AI answer engine to lift and attribute.
    """
    base = config.SITE_BASE_URL.rstrip("/")
    graph: list[dict] = [
        {
            "@type": "Article",
            "headline": title[:110],
            "description": desc,
            "datePublished": date,
            "dateModified": date,
            "inLanguage": "en",
            "author": {"@type": "Person", "name": "Muhammad Abdullah Rathore"},
            "about": {"@type": "Thing", "name": f"{role} job search"},
            "mainEntityOfPage": {"@type": "WebPage", "@id": canonical},
        }
    ]

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

    return json.dumps({"@context": "https://schema.org", "@graph": graph},
                      separators=(",", ":"))


# --------------------------------------------------------------- answer box --
def answer_first(role: str, slug: str) -> str:
    """The opening answer block.

    Measured as the strongest AI-search factor: state the answer in the first
    one or two sentences. Everything below it is supporting detail.
    """
    cluster = cluster_of(slug) or ""
    role_t = html.escape(role.title())

    answers = {
        "ats-resume-keywords": (
            f"<strong>The fastest way to find the right ATS keywords for a "
            f"{html.escape(role)} role is to take three live job postings for "
            f"that exact title and list every hard skill that appears in at "
            f"least two of them.</strong> Those exact strings — spelled the way "
            f"the posting spells them — belong in your resume wherever you "
            f"genuinely have the skill."),
        "resume-summary-examples": (
            f"<strong>A {html.escape(role)} resume summary should be two or "
            f"three lines: your title and years of experience, your strongest "
            f"measurable result, and the specific thing you want to do next.</strong> "
            f"Anything longer gets skipped."),
        "cover-letter-template": (
            f"<strong>A {html.escape(role)} cover letter needs four short "
            f"paragraphs: why this specific company, the one result that proves "
            f"you can do the job, how your experience maps to their stated "
            f"requirements, and a direct close.</strong> One page, never more."),
        "linkedin-headline-examples": (
            f"<strong>A {html.escape(role)} LinkedIn headline should read like "
            f"the search a recruiter would actually type — job title, "
            f"specialisation, and one measurable proof point.</strong> Not the "
            f"internal title your last employer invented."),
        "ai-prompts-for": (
            f"<strong>The highest-value AI prompt for a {html.escape(role)} job "
            f"search is: &ldquo;Here is a job description and my resume. List "
            f"the hard requirements in the posting that my resume does not "
            f"mention.&rdquo;</strong> Fix the ones you can honestly claim, and "
            f"ignore the rest."),
    }

    text = answers.get(cluster)
    if not text:
        return ""
    return f'<div class="answer">{text}</div>'


def default_faqs(role: str, slug: str) -> list[tuple[str, str]]:
    """Question/answer pairs that AI answer engines can lift directly."""
    r = role
    cluster = cluster_of(slug) or ""
    common = [
        (f"How many keywords should a {r} resume include?",
         "Aim for the 12-15 hard skills that appear across multiple postings "
         "for the same title. Padding beyond that reads as keyword stuffing to "
         "both parsers and humans."),
        (f"Does an ATS reject a {r} resume automatically?",
         "Most applicant tracking systems rank and filter rather than reject "
         "outright, but a low keyword match means a human may never see the "
         "resume. The parser is matching strings, not judging experience."),
        ("Should I use a PDF or a Word document?",
         "Use .docx unless the posting explicitly asks for PDF. Older parsers "
         "still handle Word more reliably."),
    ]
    if cluster == "linkedin-headline-examples":
        common.insert(0, (
            f"How long should a {r} LinkedIn headline be?",
            "Keep it under 120 characters so it is not truncated in search "
            "results and recruiter views."))
    return common[:3]


# ------------------------------------------------------------------ audit ----
def audit() -> dict:
    """Measure the real state of the published site. Nothing here is estimated."""
    pages = db.q("SELECT slug, title, html FROM pages WHERE published=1")
    total = len(pages)
    if not total:
        return {"pages": 0, "note": "nothing published yet"}

    inbound: dict[str, int] = {p["slug"]: 0 for p in pages}
    with_schema = with_faq = with_answer = 0

    for p in pages:
        body = p["html"] or ""
        if "application/ld+json" in body:
            with_schema += 1
        if "FAQPage" in body:
            with_faq += 1
        if 'class="answer"' in body:
            with_answer += 1
        for target in inbound:
            if target != p["slug"] and f'href="{target}.html"' in body:
                inbound[target] += 1

    orphans = [s for s, n in inbound.items() if n == 0]
    return {
        "pages": total,
        "orphans": len(orphans),
        "orphan_pct": round(100 * len(orphans) / total),
        "avg_inbound": round(sum(inbound.values()) / total, 1),
        "with_schema": with_schema,
        "with_faq": with_faq,
        "with_answer_first": with_answer,
        "health": _health(total, len(orphans), with_faq, with_answer),
    }


def _health(total: int, orphans: int, faq: int, answer: int) -> list[str]:
    out = []
    if orphans:
        out.append(f"{orphans} orphan page(s) — no internal links point to them, "
                   f"so they cannot rank or pass authority")
    if faq < total:
        out.append(f"{total - faq} page(s) missing FAQ schema — that is the "
                   f"easiest thing for an AI answer engine to cite")
    if answer < total:
        out.append(f"{total - answer} page(s) do not answer in the first "
                   f"sentences — the strongest AI-search factor")
    if not out:
        out.append("all published pages are linked, schema'd and answer-first")
    return out
