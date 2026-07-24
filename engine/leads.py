"""The fast-money lane: inbound service leads.

Why this exists. Measured July 2026: a brand-new domain in a competitive niche
needs 12-18 months of SEO before it earns meaningfully, while freelance AI
automation converts a first client in weeks - demand for "AI development" rose
847% year over year against only 23% growth in qualified freelancers, at
$60-150/hr. Abdullah already has a live, deployed AI system as proof, which is
the single thing most applicants cannot show.

So: the SEO lane compounds slowly in the background, and THIS lane pays rent.

HUNTER watches public "I need this built" posts, scores them for fit and budget,
and drafts outreach that leads with the live demo. Everything lands in the same
approval queue - nothing is ever sent automatically.
"""
from __future__ import annotations

import html
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET

from . import config, db, llm, net

ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}

# Boards where people post PAID WORK they want done.
#
# Learned the hard way: r/SideProject and r/smallbusiness were in this list and
# produced almost pure noise - they are showcase boards, so "AI agent hosting
# platform" is someone advertising THEIR product, not hiring. Only boards whose
# posts are requests for work belong here.
LEAD_SUBREDDITS = ["forhire", "jobbit", "DoneDirtCheap", "slavelabour",
                   "hiring", "freelance_forhire"]

# A post must prove someone is BUYING before we score it at all. Keyword fit
# alone matched product launches; this gate is what separates the two.
HIRING_MARKERS = [
    "[hiring]", "(hiring)", "hiring:", "looking to hire", "looking for someone",
    "looking for a dev", "need someone to", "need a developer", "need help with",
    "want to hire", "will pay", "paid gig", "paid work", "budget is",
    "seeking a developer", "seeking someone", "anyone available to",
]

# Weighted signals that a post is real, paid, and in our lane.
FIT_KEYWORDS = {
    4.0: ["ai agent", "ai automation", "automate my", "automation expert",
          "build me a bot", "workflow automation", "n8n", "zapier expert",
          "scrape and", "data pipeline", "chatbot for my", "llm integration"],
    2.5: ["automation", "python script", "web scraping", "api integration",
          "dashboard", "internal tool", "ai integration", "openai api"],
    1.5: ["developer needed", "looking for a developer", "need help building",
          "freelancer", "contract", "long term"],
}

BUDGET_RE = re.compile(
    r"(?:\$|usd\s*)\s?(\d{2,6})(?:\s*[-to]+\s*\$?\s?(\d{2,6}))?"
    r"|(\d{2,4})\s*(?:usd|\$)\s*(?:/|per\s*)?(?:hr|hour)",
    re.I,
)

# Posts we never want: people selling their own services, not buying.
EXCLUDE = ["[for hire]", "for hire -", "i am available", "my portfolio",
           "hire me", "offering my", "i will do", "seeking work"]

OUTREACH_SYSTEM = (
    "You write short cold outreach replies to people who publicly asked for a "
    "developer. You are a working AI automation developer with a live, deployed "
    "system as proof. You never grovel, never open with 'I hope this finds you "
    "well', never list generic skills, and never claim experience you were not "
    "given. You reference one specific detail from THEIR post to prove you read "
    "it, state plainly what you would build, and end with one low-friction "
    "question. 70-110 words. No emoji, no bullet lists, no headings."
)


def _budget_usd(text: str) -> int:
    """Best-effort budget extraction. 0 when the post names no number."""
    best = 0
    for m in BUDGET_RE.finditer(text[:2000]):
        for g in m.groups():
            if not g:
                continue
            try:
                v = int(g)
            except ValueError:
                continue
            # Ignore years and absurd values that are usually not budgets.
            if 20 <= v <= 100000 and not (1990 <= v <= 2035):
                best = max(best, v)
    return best


def fit_score(title: str, body: str) -> float:
    text = f"{title}\n{body}".lower()

    for bad in EXCLUDE:
        if bad in text:
            return 0.0

    # Gate: no evidence anyone is buying -> not a lead, whatever else it says.
    if not any(m in text for m in HIRING_MARKERS):
        return 0.0

    score = 0.0
    for weight, phrases in FIT_KEYWORDS.items():
        for p in phrases:
            if p in text:
                score += weight

    budget = _budget_usd(text)
    if budget >= 1000:
        score += 4.0
    elif budget >= 300:
        score += 2.5
    elif budget >= 100:
        score += 1.0

    if any(k in text for k in ("[hiring]", "paid", "budget", "will pay")):
        score += 1.5

    return round(score, 2)


def harvest_leads() -> int:
    """Sweep hiring boards. Stores into signals with source='lead:<board>'."""
    new = 0

    for sub in LEAD_SUBREDDITS:
        url = f"https://www.reddit.com/r/{urllib.parse.quote(sub)}/new/.rss"
        try:
            root = ET.fromstring(net.fetch(url, retries=0))
        except Exception:
            continue  # rate-limited; next cycle picks it up

        for entry in root.findall("a:entry", ATOM_NS):
            def text(tag: str) -> str:
                el = entry.find(f"a:{tag}", ATOM_NS)
                return (el.text or "") if el is not None else ""

            link_el = entry.find("a:link", ATOM_NS)
            link = link_el.get("href", "") if link_el is not None else ""
            if not link:
                continue

            content = html.unescape(text("content"))
            content = " ".join(re.sub(r"<[^>]+>", " ", content).split())
            title = text("title")

            score = fit_score(title, content)
            if score < 6.0:
                continue  # raised from 4.0: below this was noise, not leads

            author_el = entry.find("a:author/a:name", ATOM_NS)
            rid = db.x(
                "INSERT OR IGNORE INTO signals"
                "(source,external_id,url,title,body,author,created_utc,"
                "harvested_at,score,intent,status) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,'lead')",
                (f"lead:{sub}", text("id") or link, link, title[:500],
                 content[:8000],
                 (author_el.text if author_el is not None else ""),
                 time.time(), time.time(), score,
                 f"${_budget_usd(title + content) or '?'} budget"),
            )
            if rid:
                new += 1

    if new:
        db.log("leads", f"{new} new service lead(s) found", "info")
    db.set_metric("leads_total",
                  db.scalar("SELECT COUNT(*) FROM signals WHERE status='lead'"))
    return new


def draft_outreach(limit: int = 2) -> int:
    """Write personalised outreach for the best unworked leads."""
    rows = db.q(
        "SELECT * FROM signals WHERE status='lead' ORDER BY score DESC LIMIT ?",
        (limit,))
    if not rows:
        return 0

    made = 0
    for lead in rows:
        if db.one("SELECT id FROM content WHERE signal_id=?", (lead["id"],)):
            db.x("UPDATE signals SET status='lead_used' WHERE id=?", (lead["id"],))
            continue

        proof = (f"I have a live deployed example at {config.SHOWCASE_URL} "
                 f"({config.SHOWCASE_NAME} - {config.SHOWCASE_TAGLINE})."
                 if config.SHOWCASE_URL else "")

        prompt = (
            f"They posted on r/{lead['source'].split(':')[-1]}:\n\n"
            f"Title: {lead['title']}\n\n{(lead['body'] or '')[:1500]}\n\n"
            f"Proof you can cite: {proof}\n\n"
            "Write the reply."
        )
        body = llm.generate(prompt, system=OUTREACH_SYSTEM)

        if not body:
            body = (
                f"You mentioned needing this built - I do exactly this kind of "
                f"automation work.\n\n{proof}\n\nWhat I'd suggest: start with the "
                f"smallest working slice so you can see it running before "
                f"committing to the whole scope.\n\n"
                f"What does the data look like on your side right now?"
            )

        db.x(
            "INSERT INTO content(signal_id,kind,platform,title,body,created_at,status)"
            " VALUES(?,?,?,?,?,?,'draft')",
            (lead["id"], "outreach", lead["source"],
             f"LEAD (${_budget_usd(lead['title'] + (lead['body'] or '')) or '?'}): "
             f"{lead['title'][:100]}",
             body, time.time()),
        )
        db.x("UPDATE signals SET status='lead_used' WHERE id=?", (lead["id"],))
        made += 1

    if made:
        db.log("leads", f"drafted {made} outreach message(s) -> approval queue")
    return made


def pipeline() -> dict:
    """Numbers for the dashboard's money panel."""
    open_leads = db.q(
        "SELECT title,url,score,intent FROM signals WHERE status='lead' "
        "ORDER BY score DESC LIMIT 8")
    return {
        "open": db.scalar("SELECT COUNT(*) FROM signals WHERE status='lead'"),
        "worked": db.scalar("SELECT COUNT(*) FROM signals WHERE status='lead_used'"),
        "drafts": db.scalar(
            "SELECT COUNT(*) FROM content WHERE kind='outreach' AND status='draft'"),
        "top": [dict(r) for r in open_leads],
    }
