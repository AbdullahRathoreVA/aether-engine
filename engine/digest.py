"""The 5% that needs a human, ranked and timed.

The research on autonomous income systems is consistent: the working ones are
human-in-the-loop, where software does ~95% and a person clears the rest. That
last 5% is not a design flaw to engineer away - it is the legally required human
(KYC/AML rules assume a natural person; an agent cannot hold a payout account).

So the goal is not to remove the human. It is to make the human's slice as small
and as obvious as possible: one screen, ranked by money impact, with a realistic
time cost, so the daily loop is minutes rather than an evening.
"""
from __future__ import annotations

import time

from . import config, db, leads


def _age_days(ts: float | None) -> float:
    return (time.time() - ts) / 86400 if ts else 0.0


def blockers() -> list[dict]:
    """Things that stop money entirely. Nothing else matters until these clear."""
    out = []

    if not config.CHECKOUT_URL:
        first_page = db.scalar("SELECT MIN(created_at) FROM pages", default=None)
        days = _age_days(first_page)
        pages = db.scalar("SELECT COUNT(*) FROM pages WHERE published=1")
        out.append({
            "id": "checkout",
            "severity": "critical",
            "title": "No payment link — every buy button is dead",
            "detail": (
                f"{pages} pages are published and every one of them ends in a "
                f"button that goes nowhere. This is the single reason revenue is "
                f"$0. Nothing the engine does can fix it: payout accounts legally "
                f"require a verified human (KYC/AML), so it has to be you."
            ),
            "action": "Create a Dodo Payments product, put its URL in .env as "
                      "CHECKOUT_URL, restart.",
            "warning": "Do NOT use Gumroad — it cannot pay out to Pakistan.",
            "minutes": 15,
            "cost_note": (f"{days:.0f} days of published pages have earned $0 "
                          f"because of this." if days >= 1 else ""),
        })

    if not config.SITE_BASE_URL:
        out.append({
            "id": "deploy",
            "severity": "high",
            "title": "Site is not deployed — Google cannot find it",
            "detail": "Pages exist only on this laptop. Until they are on the "
                      "public web, they cannot rank or be read by anyone.",
            "action": "gh auth login, then .\\scripts\\deploy.ps1 -Repo aether-engine",
            "minutes": 10,
            "cost_note": "",
        })

    from . import llm
    if llm.detect_backend() == "template":
        out.append({
            "id": "llm",
            "severity": "medium",
            "title": "No AI model connected — output is repetitive",
            "detail": "The engine is writing from fixed templates. It works, but "
                      "pages and outreach will read samey, which hurts both "
                      "ranking and reply rates.",
            "action": "Get a free key at console.groq.com/keys, add "
                      "GROQ_API_KEY to .env, restart.",
            "minutes": 2,
            "cost_note": "",
        })

    return out


def actions() -> list[dict]:
    """Ranked work waiting on a human, highest money impact first."""
    out = []

    # Outreach beats everything: research puts freelance conversion at weeks,
    # against 12-18 months for SEO on a new domain.
    for r in db.q(
        "SELECT c.id,c.title,c.body,s.url,s.score FROM content c "
        "LEFT JOIN signals s ON s.id=c.signal_id "
        "WHERE c.kind='outreach' AND c.status='draft' "
        "ORDER BY s.score DESC LIMIT 10"
    ):
        out.append({
            "id": r["id"], "kind": "outreach", "priority": 1,
            "label": "Send outreach to a paid lead",
            "title": r["title"], "body": r["body"], "url": r["url"],
            "why": "Service work converts in weeks. This is the fastest money "
                   "in the system.",
            "minutes": 2,
        })

    for r in db.q(
        "SELECT c.id,c.title,c.body,s.url,s.score FROM content c "
        "LEFT JOIN signals s ON s.id=c.signal_id "
        "WHERE c.kind='reply' AND c.status='draft' "
        "ORDER BY s.score DESC LIMIT 10"
    ):
        out.append({
            "id": r["id"], "kind": "reply", "priority": 2,
            "label": "Reply to someone who needs the toolkit",
            "title": r["title"], "body": r["body"], "url": r["url"],
            "why": "Warm traffic straight to the product, and it builds your "
                   "reputation on that board.",
            "minutes": 2,
        })

    for r in db.q(
        "SELECT id,title,body FROM content WHERE kind='post' AND status='draft' "
        "ORDER BY created_at DESC LIMIT 5"
    ):
        out.append({
            "id": r["id"], "kind": "post", "priority": 3,
            "label": "Post to LinkedIn",
            "title": r["title"], "body": r["body"], "url": "",
            "why": "Compounds slowly; keeps you visible to people who hire.",
            "minutes": 1,
        })

    out.sort(key=lambda a: a["priority"])
    return out


def summary() -> dict:
    b = blockers()
    a = actions()
    lp = leads.pipeline()

    return {
        "blockers": b,
        "actions": a,
        "minutes_total": sum(x["minutes"] for x in a),
        "blocker_minutes": sum(x["minutes"] for x in b),
        "counts": {
            "outreach": sum(1 for x in a if x["kind"] == "outreach"),
            "replies": sum(1 for x in a if x["kind"] == "reply"),
            "posts": sum(1 for x in a if x["kind"] == "post"),
            "leads_open": lp["open"],
        },
        "earning_possible": bool(config.CHECKOUT_URL),
    }
