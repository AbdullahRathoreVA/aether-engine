"""Intent scoring: which harvested signals describe a problem we actually solve.

Base pass is deterministic keyword weighting - free, instant, no model needed.
When an LLM is available it re-ranks the survivors, which mostly filters out
people venting rather than looking for a fix.
"""
from __future__ import annotations

import time

from . import config, db, llm

SYSTEM = (
    "You triage forum posts for a job-search product (ATS resume templates, "
    "AI prompts, LinkedIn optimisation, application tracker). You judge whether "
    "the author would genuinely benefit from that help right now. You are "
    "sceptical: venting, humblebrags, and people who already solved it score low."
)


def keyword_score(title: str, body: str) -> float:
    text = f"{title}\n{body}".lower()

    for neg in config.NEGATIVE_KEYWORDS:
        if neg in text:
            return 0.0

    score = 0.0
    for weight, phrases in config.INTENT_KEYWORDS.items():
        for phrase in phrases:
            if phrase in text:
                score += weight

    # A question mark and first-person framing both correlate with wanting help.
    if "?" in text:
        score += 0.5
    if any(p in text for p in (" i ", "i'm ", "im ", "my resume", "my linkedin")):
        score += 0.5

    # Very short posts rarely carry enough context to answer usefully.
    if len(body) < 120:
        score *= 0.5

    return round(score, 2)


def llm_rerank(title: str, body: str) -> tuple[float, str] | None:
    prompt = (
        f"Post title: {title}\n\nPost body:\n{body[:2000]}\n\n"
        "Return JSON: {\"score\": 0-10, \"intent\": \"<6 words describing what they need>\"}"
    )
    out = llm.generate_json(prompt, system=SYSTEM)
    if not isinstance(out, dict) or "score" not in out:
        return None
    try:
        return float(out["score"]), str(out.get("intent", ""))[:120]
    except (TypeError, ValueError):
        return None


def score_pending(limit: int = 40) -> int:
    rows = db.q(
        "SELECT id,title,body FROM signals WHERE status='new' "
        "ORDER BY harvested_at DESC LIMIT ?", (limit,))
    if not rows:
        return 0

    use_llm = llm.detect_backend() != "template"
    scored = 0

    for row in rows:
        title, body = row["title"] or "", row["body"] or ""
        base = keyword_score(title, body)
        intent = ""

        # Only spend model calls on things that already look promising.
        if use_llm and base >= 2.0:
            reranked = llm_rerank(title, body)
            if reranked:
                llm_score, intent = reranked
                base = (base + llm_score) / 2

        status = "scored" if base >= 2.0 else "skipped"
        db.x("UPDATE signals SET score=?,intent=?,status=? WHERE id=?",
             (base, intent, status, row["id"]))
        scored += 1

    hot = db.scalar("SELECT COUNT(*) FROM signals WHERE status='scored'")
    db.set_metric("signals_hot", hot)
    db.set_metric("last_score", time.time())
    db.log("scorer", f"scored {scored} signals, {hot} in the hot queue")
    return scored


def top_signals(limit: int = 5) -> list:
    return db.q(
        "SELECT * FROM signals WHERE status='scored' ORDER BY score DESC, "
        "created_utc DESC LIMIT ?", (limit,))
