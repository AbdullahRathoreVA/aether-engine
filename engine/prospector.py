"""Opportunity discovery - the engine finding niches I never told it about.

Until now the SEO page matrix came from a list I hand-wrote: 16 roles x 5
templates. That is a ceiling, and it is my ceiling, not the market's.

PROSPECTOR removes it. It mines everything harvested - hundreds of real posts
from people actually job-hunting right now - and extracts the job titles and
problem phrases that keep appearing but that we have never built a page for.
Those become new page targets automatically.

That is what "always searching for new opportunities" can honestly mean: the
engine widens its own target list from live demand. It is not clairvoyance and
it will not find a secret money faucet - it finds the roles real people are
struggling with this week, which is the actual signal that matters for SEO.
"""
from __future__ import annotations

import re
import time
from collections import Counter

from . import config, db, generator, llm

# Job titles show up in predictable frames in these posts.
ROLE_PATTERNS = [
    r"(?:as an?|i'?m an?|i am an?|for an?|hiring an?|entry[- ]level)\s+"
    r"([a-z]{3,}(?:\s+[a-z]{3,}){0,2})\s*(?:role|position|job|intern)?",
    r"([a-z]{3,}(?:\s+[a-z]{3,}){0,2})\s+(?:roles?|positions?|jobs?)\b",
]

# Words that look like roles but are not - kept because the naive regex is
# otherwise very confident that "the job" is a profession.
ROLE_STOP = {
    "the", "this", "that", "any", "new", "good", "same", "first", "next",
    "other", "such", "many", "few", "full time", "part time", "remote",
    "my", "his", "her", "their", "your", "our", "a lot", "some", "more",
    "job", "jobs", "role", "roles", "position", "work", "career", "company",
    "one", "two", "year", "years", "month", "months", "time", "people",
    "person", "someone", "anyone", "everyone", "thing", "things", "way",
    "lot", "bit", "day", "days", "week", "weeks", "place", "part",
    # Adjectives/comparatives that survive the -er/-ent morphology gate. These
    # were the actual top hits on live data before this list existed.
    "current", "higher", "lower", "different", "another", "better", "bigger",
    "smaller", "older", "younger", "recent", "most recent", "senior", "junior",
    "rising senior", "find another", "looking for another", "similar",
    "permanent", "excellent", "decent", "urgent", "consistent", "relevant",
    "former", "latter", "further", "greater", "longer", "shorter", "easier",
    "harder", "cheaper", "faster", "slower", "stronger", "weaker", "proper",
}

PAIN_PATTERNS = [
    r"(?:how (?:do|can) i|how to)\s+([a-z][a-z\s]{8,50})",
    r"(?:struggling|trouble|help)\s+(?:with|to)\s+([a-z][a-z\s]{8,50})",
]


def _clean(phrase: str) -> str:
    p = re.sub(r"\s+", " ", phrase.strip().lower())
    p = re.sub(r"[^a-z\s]", "", p)
    return p.strip()


# Real job titles almost always end in one of these. Tested against 500 live
# posts: the bare regex returned "current", "what", "get the" as top hits, so
# morphology is doing the heavy lifting, not the grammatical frame.
ROLE_SUFFIXES = (
    "er", "or", "ist", "ian", "ant", "ent", "eer", "smith", "wright",
    "ary", "ive", "ric", "ur", "ef", "ard",
)

ROLE_HEADS = {
    "engineer", "developer", "designer", "manager", "analyst", "scientist",
    "nurse", "teacher", "accountant", "consultant", "specialist", "technician",
    "administrator", "coordinator", "assistant", "associate", "director",
    "officer", "architect", "recruiter", "therapist", "pharmacist", "attorney",
    "paralegal", "electrician", "plumber", "machinist", "welder", "chef",
    "writer", "editor", "marketer", "auditor", "controller", "buyer",
    "planner", "supervisor", "instructor", "researcher", "programmer",
    "clerk", "cashier", "driver", "technologist", "practitioner", "advisor",
}


def _looks_like_role(role: str) -> bool:
    """Cheap morphology gate before we spend a model call."""
    words = role.split()
    if not (1 <= len(words) <= 3):
        return False
    if any(w in ROLE_STOP for w in words):
        return False
    head = words[-1]
    if head in ROLE_HEADS:
        return True
    # Single bare words are almost always noise unless clearly occupational.
    if len(words) == 1:
        return head.endswith(ROLE_SUFFIXES) and len(head) >= 6
    return head.endswith(ROLE_SUFFIXES) and len(head) >= 5


def discover_roles(limit: int = 400) -> list[tuple[str, int]]:
    """Job titles that real posts mention but our page matrix does not cover."""
    known = {r.lower() for r in config.SEO_ROLES}
    found: Counter = Counter()

    rows = db.q(
        "SELECT title, body FROM signals ORDER BY harvested_at DESC LIMIT ?",
        (limit,))

    for row in rows:
        text = f"{row['title'] or ''} {row['body'] or ''}".lower()
        for pattern in ROLE_PATTERNS:
            for match in re.findall(pattern, text):
                role = _clean(match)
                if not (4 <= len(role) <= 34):
                    continue
                if role in ROLE_STOP or role in known:
                    continue
                if not _looks_like_role(role):
                    continue
                found[role] += 1

    # Appearing once is noise; twice or more is a pattern worth a page.
    return [(r, n) for r, n in found.most_common(40) if n >= 2]


def discover_pains(limit: int = 400) -> list[tuple[str, int]]:
    """Recurring problem phrasings - these make good page titles verbatim."""
    found: Counter = Counter()
    for row in db.q("SELECT title, body FROM signals "
                    "ORDER BY harvested_at DESC LIMIT ?", (limit,)):
        text = f"{row['title'] or ''} {row['body'] or ''}".lower()
        for pattern in PAIN_PATTERNS:
            for match in re.findall(pattern, text):
                phrase = _clean(match)
                if 12 <= len(phrase) <= 60:
                    found[phrase] += 1
    return [(p, n) for p, n in found.most_common(25) if n >= 2]


def validate(role: str) -> bool:
    """Decide whether a discovered phrase is really a job title.

    This is deliberately NOT an LLM call. Measured on llama3.2:1b (2026-07-25):
    it accepted "current" and rejected "registered nurse" and "data engineer".
    A 1B model writes acceptable prose but cannot be trusted for a yes/no gate,
    and a wrong yes here permanently pollutes the page matrix.

    So the rule is deterministic and high-precision: the head noun must be a
    recognised occupation word. We would rather miss a real niche than publish
    pages for "current" - a missed niche costs nothing, a bad one costs
    credibility with Google.
    """
    words = role.split()
    if not (1 <= len(words) <= 3):
        return False
    if any(w in ROLE_STOP for w in words):
        return False
    return words[-1] in ROLE_HEADS


def expand_targets(max_new: int = 3) -> int:
    """Add validated discoveries to the live page matrix."""
    discovered = db.get_metric("discovered_roles", []) or []
    known = {r.lower() for r in config.SEO_ROLES} | {r.lower() for r in discovered}

    added = 0
    for role, count in discover_roles():
        if added >= max_new:
            break
        if role in known:
            continue
        if not validate(role):
            continue

        discovered.append(role)
        config.SEO_ROLES.append(role)   # live matrix, no restart needed
        known.add(role)
        added += 1
        db.log("prospector",
               f"new niche found: '{role}' (seen {count}x in real posts)")

    if added:
        db.set_metric("discovered_roles", discovered)
    db.set_metric("last_prospect", time.time())
    return added


def report() -> dict:
    roles = discover_roles()
    pains = discover_pains()
    discovered = db.get_metric("discovered_roles", []) or []
    return {
        "adopted": discovered,
        "adopted_count": len(discovered),
        "candidate_roles": roles[:12],
        "candidate_pains": pains[:8],
        "matrix_size": len(config.SEO_ROLES) * len(config.SEO_TEMPLATES),
        "pages_built": db.scalar("SELECT COUNT(*) FROM pages"),
    }
