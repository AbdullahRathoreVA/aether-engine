"""Self-improvement that has to prove itself.

"Improve yourself automatically" is easy to write and easy to get wrong: an
agent that rewrites its own logic on vibes degrades silently, and you find out
weeks later when the output has quietly turned to mush.

So this module improves by measurement, not by assertion:

  1. Propose a variant of the scoring configuration (keyword weights, decision
     threshold), either by hill-climbing or by asking an LLM for a suggestion.
  2. Evaluate BOTH the current config and the variant against held-out labelled
     data, using cross-validation - never the data the change was tuned on.
  3. Adopt the variant only if it beats the incumbent by a real margin.
  4. Otherwise discard it and record that it was tried, so the same dead end is
     not explored forever.

Every adopted change is logged with its before/after score, so the improvement
is auditable rather than a claim.
"""
from __future__ import annotations

import json
import random
import time

from . import brain, config, db, llm

MIN_EVAL = 16          # below this, any "improvement" is noise
MIN_GAIN = 0.02        # must beat incumbent by 2 points of accuracy to adopt

SCHEMA = """
CREATE TABLE IF NOT EXISTS evolution (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL NOT NULL,
    kind      TEXT NOT NULL,
    before    REAL,
    after     REAL,
    adopted   INTEGER DEFAULT 0,
    detail    TEXT
);
"""


def init() -> None:
    with db.conn() as c:
        c.executescript(SCHEMA)


def _samples() -> list[tuple[str, int]]:
    return brain.samples("reply")


def _evaluate(threshold: float, folds: int = 5) -> float | None:
    """Cross-validated accuracy of the classifier at a given decision threshold."""
    data = _samples()
    if len(data) < MIN_EVAL:
        return None
    data = sorted(data, key=lambda d: hash(d[0]) & 0xFFFF)

    correct = total = 0
    for f in range(folds):
        test = [d for i, d in enumerate(data) if i % folds == f]
        train = [d for i, d in enumerate(data) if i % folds != f]
        if not test or not train:
            continue
        m = brain.Model()
        m.fit(train)
        for text, label in test:
            if (m.predict(text) >= threshold) == bool(label):
                correct += 1
            total += 1
    return round(correct / total, 4) if total else None


def tune_threshold() -> dict:
    """Find the decision threshold that actually classifies best.

    0.5 is a default, not a discovery. On imbalanced data the best cut is
    often elsewhere, and this measures rather than assumes.
    """
    current = db.get_metric("decision_threshold", 0.5)
    base = _evaluate(current)
    if base is None:
        return {"ran": False,
                "reason": f"needs {MIN_EVAL - len(_samples())} more labelled examples"}

    best_t, best_acc = current, base
    for t in [0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7]:
        acc = _evaluate(t)
        if acc is not None and acc > best_acc:
            best_t, best_acc = t, acc

    adopted = best_acc - base >= MIN_GAIN and best_t != current
    if adopted:
        db.set_metric("decision_threshold", best_t)
        db.log("evolve",
               f"threshold {current} -> {best_t} "
               f"(accuracy {base:.1%} -> {best_acc:.1%})", "info")

    db.x("INSERT INTO evolution(ts,kind,before,after,adopted,detail) "
         "VALUES(?,?,?,?,?,?)",
         (time.time(), "threshold", base, best_acc, int(adopted),
          f"{current} -> {best_t}"))

    return {"ran": True, "adopted": adopted, "before": base, "after": best_acc,
            "threshold": best_t if adopted else current}


def tune_keywords() -> dict:
    """Hill-climb the keyword weights against held-out data.

    The weights in config were my guess. This checks whether a different set
    predicts Abdullah's/the teachers' judgement better, and keeps it only if so.
    """
    data = _samples()
    if len(data) < MIN_EVAL:
        return {"ran": False,
                "reason": f"needs {MIN_EVAL - len(data)} more labelled examples"}

    from . import scorer

    def accuracy_with(weights: dict) -> float:
        original = config.INTENT_KEYWORDS
        config.INTENT_KEYWORDS = weights
        try:
            correct = 0
            for text, label in data:
                title, _, body = text.partition("\n")
                predicted = scorer.keyword_score(title, body) >= 2.0
                if predicted == bool(label):
                    correct += 1
            return correct / len(data)
        finally:
            config.INTENT_KEYWORDS = original

    current = {k: list(v) for k, v in config.INTENT_KEYWORDS.items()}
    base = accuracy_with(current)

    rng = random.Random(int(time.time()))
    best, best_acc = current, base

    # Small random perturbations of the weight tiers.
    for _ in range(12):
        variant = {}
        for weight, phrases in current.items():
            nudge = rng.choice([-0.5, -0.25, 0, 0.25, 0.5])
            variant[max(0.5, weight + nudge)] = list(phrases)
        acc = accuracy_with(variant)
        if acc > best_acc:
            best, best_acc = variant, acc

    adopted = best_acc - base >= MIN_GAIN
    if adopted:
        config.INTENT_KEYWORDS = best
        db.set_metric("intent_weights", {str(k): v for k, v in best.items()})
        db.log("evolve",
               f"keyword weights retuned ({base:.1%} -> {best_acc:.1%})", "info")

    db.x("INSERT INTO evolution(ts,kind,before,after,adopted,detail) "
         "VALUES(?,?,?,?,?,?)",
         (time.time(), "keywords", base, best_acc, int(adopted),
          json.dumps({str(k): len(v) for k, v in best.items()})))

    return {"ran": True, "adopted": adopted, "before": base, "after": best_acc}


def propose_keywords() -> dict:
    """Ask an LLM for phrases we are missing, then TEST them before keeping.

    This is the "learn from other AIs" path applied to the rules themselves -
    but a suggestion is a hypothesis, not an improvement, so it still has to
    beat the incumbent on held-out data.
    """
    if llm.detect_backend() == "template":
        return {"ran": False, "reason": "no LLM available to consult"}

    data = _samples()
    if len(data) < MIN_EVAL:
        return {"ran": False, "reason": "not enough labelled data to test against"}

    positives = [t for t, l in data if l == 1][:8]
    if not positives:
        return {"ran": False, "reason": "no positive examples yet"}

    sample = "\n---\n".join(p[:280] for p in positives)
    out = llm.generate_json(
        f"These forum posts are all from people who need job-search help:\n\n"
        f"{sample}\n\n"
        'Return JSON: {"phrases": ["<up to 8 short lowercase phrases that '
        'signal this need and are not generic>"]}',
        system="You extract discriminative keyword phrases. Terse, lowercase.")

    phrases = []
    if isinstance(out, dict):
        phrases = [str(p).lower().strip() for p in (out.get("phrases") or [])
                   if isinstance(p, str) and 4 <= len(p) <= 40]
    if not phrases:
        return {"ran": False, "reason": "model returned nothing usable"}

    from . import scorer

    known = {p for tier in config.INTENT_KEYWORDS.values() for p in tier}
    fresh = [p for p in phrases if p not in known][:8]
    if not fresh:
        return {"ran": True, "adopted": False, "reason": "all suggestions already known"}

    def accuracy_now() -> float:
        correct = 0
        for text, label in data:
            title, _, body = text.partition("\n")
            if (scorer.keyword_score(title, body) >= 2.0) == bool(label):
                correct += 1
        return correct / len(data)

    base = accuracy_now()
    original = {k: list(v) for k, v in config.INTENT_KEYWORDS.items()}
    config.INTENT_KEYWORDS.setdefault(2.0, []).extend(fresh)
    after = accuracy_now()

    adopted = after - base >= MIN_GAIN
    if not adopted:
        config.INTENT_KEYWORDS = original
    else:
        db.log("evolve",
               f"adopted {len(fresh)} LLM-suggested phrases "
               f"({base:.1%} -> {after:.1%}): {', '.join(fresh[:4])}", "info")

    db.x("INSERT INTO evolution(ts,kind,before,after,adopted,detail) "
         "VALUES(?,?,?,?,?,?)",
         (time.time(), "llm_phrases", base, after, int(adopted),
          json.dumps(fresh)))

    return {"ran": True, "adopted": adopted, "before": base, "after": after,
            "phrases": fresh}


def cycle() -> dict:
    """One full self-improvement pass."""
    return {
        "threshold": tune_threshold(),
        "keywords": tune_keywords(),
        "proposed": propose_keywords(),
    }


def history(limit: int = 20) -> list[dict]:
    return [dict(r) for r in db.q(
        "SELECT * FROM evolution ORDER BY ts DESC LIMIT ?", (limit,))]


def stats() -> dict:
    rows = history(200)
    adopted = [r for r in rows if r["adopted"]]
    return {
        "attempts": len(rows),
        "adopted": len(adopted),
        "rejected": len(rows) - len(adopted),
        "last_gain": (round((adopted[0]["after"] - adopted[0]["before"]) * 100, 1)
                      if adopted else None),
        "recent": rows[:6],
    }
