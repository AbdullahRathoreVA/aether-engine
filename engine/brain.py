"""Machine learning that actually learns - from Abdullah's own decisions.

This is a real classifier, not a decorative one. Every time he approves or
rejects a draft, that becomes a labelled training example. The model learns
which posts HE thinks are worth answering, and starts ranking new signals by
predicted approval rather than by my hand-written keyword weights.

Why Naive Bayes and not a neural net:
  - it trains usefully on tens of examples, not tens of thousands. He will
    generate maybe 20 decisions a week, so anything data-hungry would never
    leave its cold start.
  - pure stdlib, no numpy, no torch, no install, no cost.
  - the weights are inspectable, so the dashboard can honestly show WHY a post
    scored high instead of gesturing at a black box.

Honesty rules baked in:
  - below MIN_TRAIN examples it refuses to predict and says so. A model trained
    on four samples is noise wearing a lab coat.
  - it reports its own accuracy measured by cross-validation on held-out data,
    never on the data it trained on.
  - predictions blend with the keyword baseline until the model has earned
    confidence, so a bad early model cannot wreck the pipeline.
"""
from __future__ import annotations

import math
import re
import time
from collections import Counter, defaultdict

from . import db

MIN_TRAIN = 12          # below this we do not pretend to know anything
CONFIDENT_AT = 40       # examples needed before the model fully outranks keywords
STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "is", "are", "was", "were", "be",
    "been", "to", "of", "in", "on", "for", "with", "at", "by", "from", "as",
    "it", "its", "this", "that", "these", "those", "i", "im", "ive", "my",
    "me", "you", "your", "we", "our", "they", "them", "he", "she", "his",
    "her", "have", "has", "had", "do", "does", "did", "not", "no", "so",
    "if", "then", "than", "there", "here", "what", "which", "who", "how",
    "can", "will", "would", "could", "should", "just", "any", "all", "some",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS training (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL NOT NULL,
    kind       TEXT NOT NULL,
    label      INTEGER NOT NULL,
    text       TEXT NOT NULL,
    source     TEXT,
    signal_id  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_training_kind ON training(kind);
"""


def init() -> None:
    with db.conn() as c:
        c.executescript(SCHEMA)


# ------------------------------------------------------------- features -----
def tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z']{3,}", (text or "").lower())
    out = [w for w in words if w not in STOPWORDS]
    # Bigrams carry intent that single words lose: "not hearing" vs "hearing".
    out += [f"{a}_{b}" for a, b in zip(out, out[1:])]
    return out


def record(kind: str, label: int, text: str, source: str = "",
           signal_id: int | None = None) -> None:
    """Log one human decision. label: 1 = approved/good, 0 = rejected/bad."""
    db.x("INSERT INTO training(ts,kind,label,text,source,signal_id) "
         "VALUES(?,?,?,?,?,?)",
         (time.time(), kind, int(bool(label)), (text or "")[:4000],
          source, signal_id))


# -------------------------------------------------------------- training ----
class Model:
    """Multinomial Naive Bayes with Laplace smoothing."""

    def __init__(self) -> None:
        self.counts: dict[int, Counter] = {0: Counter(), 1: Counter()}
        self.totals: dict[int, int] = {0: 0, 1: 0}
        self.docs: dict[int, int] = {0: 0, 1: 0}
        self.vocab: set[str] = set()

    def fit(self, samples: list[tuple[str, int]]) -> None:
        for text, label in samples:
            toks = tokens(text)
            self.counts[label].update(toks)
            self.totals[label] += len(toks)
            self.docs[label] += 1
            self.vocab.update(toks)

    def predict(self, text: str) -> float:
        """P(approve). 0.5 when it has nothing to go on."""
        n = self.docs[0] + self.docs[1]
        if n == 0:
            return 0.5
        v = max(len(self.vocab), 1)
        logp = {}
        for label in (0, 1):
            # Prior, smoothed so a missing class does not produce log(0).
            logp[label] = math.log((self.docs[label] + 1) / (n + 2))
            denom = self.totals[label] + v
            for tok in tokens(text):
                logp[label] += math.log((self.counts[label][tok] + 1) / denom)

        # Softmax of two log-odds, done stably.
        hi = max(logp.values())
        e0, e1 = math.exp(logp[0] - hi), math.exp(logp[1] - hi)
        return e1 / (e0 + e1)

    def top_signals(self, label: int = 1, n: int = 10) -> list[tuple[str, float]]:
        """The tokens most predictive of a class - what the model actually learned."""
        other = 1 - label
        scored = []
        for tok in self.vocab:
            a = self.counts[label][tok]
            if a < 2:
                continue  # a single appearance is not a pattern
            b = self.counts[other][tok]
            ratio = ((a + 1) / (self.totals[label] + len(self.vocab))) / \
                    ((b + 1) / (self.totals[other] + len(self.vocab)))
            scored.append((tok.replace("_", " "), round(ratio, 2)))
        scored.sort(key=lambda x: -x[1])
        return scored[:n]


_cache: dict = {"model": None, "kind": None, "trained_at": 0.0, "n": 0}


def samples(kind: str) -> list[tuple[str, int]]:
    return [(r["text"], r["label"]) for r in
            db.q("SELECT text,label FROM training WHERE kind=?", (kind,))]


def train(kind: str = "reply", force: bool = False) -> Model | None:
    """Fit on all decisions of this kind. Cached for 60s."""
    if (not force and _cache["model"] and _cache["kind"] == kind
            and time.time() - _cache["trained_at"] < 60):
        return _cache["model"]

    data = samples(kind)
    if len(data) < MIN_TRAIN:
        return None

    m = Model()
    m.fit(data)
    _cache.update({"model": m, "kind": kind, "trained_at": time.time(),
                   "n": len(data)})
    return m


def accuracy(kind: str = "reply", folds: int = 5) -> float | None:
    """Honest accuracy: k-fold cross-validation, never scored on training data."""
    data = samples(kind)
    if len(data) < MIN_TRAIN:
        return None

    # Interleave so folds are not ordered by time (all approvals then all rejects).
    data = sorted(data, key=lambda d: hash(d[0]) & 0xFFFF)
    correct = total = 0
    for f in range(folds):
        test = [d for i, d in enumerate(data) if i % folds == f]
        train_set = [d for i, d in enumerate(data) if i % folds != f]
        if not test or not train_set:
            continue
        m = Model()
        m.fit(train_set)
        for text, label in test:
            if (m.predict(text) >= 0.5) == bool(label):
                correct += 1
            total += 1
    return round(correct / total, 3) if total else None


def score(text: str, baseline: float, kind: str = "reply") -> tuple[float, str]:
    """Blend the learned model with the keyword baseline.

    Returns (score_0_to_10, explanation). While the model is young its opinion
    is weighted down, so early noise cannot hijack the queue.
    """
    m = train(kind)
    if m is None:
        n = len(samples(kind))
        return baseline, (f"keyword rules only - the model needs "
                          f"{MIN_TRAIN - n} more of your decisions to start learning")

    p = m.predict(text)
    n = _cache["n"]
    trust = min(n / CONFIDENT_AT, 1.0)          # 0 -> 1 as evidence accumulates
    learned = p * 10
    blended = baseline * (1 - trust) + learned * trust

    return round(blended, 2), (
        f"model predicts {p * 100:.0f}% you'd approve this "
        f"(trained on {n} of your decisions, weighted {trust * 100:.0f}%)")


def stats(kind: str = "reply") -> dict:
    data = samples(kind)
    pos = sum(1 for _, l in data if l == 1)
    m = train(kind)
    acc = accuracy(kind)

    return {
        "examples": len(data),
        "approved": pos,
        "rejected": len(data) - pos,
        "ready": len(data) >= MIN_TRAIN,
        "needs": max(0, MIN_TRAIN - len(data)),
        "trust_pct": round(min(len(data) / CONFIDENT_AT, 1.0) * 100),
        "accuracy": acc,
        "accuracy_label": (f"{acc * 100:.0f}% on held-out data" if acc
                           else "not enough data to measure honestly"),
        "learned_positive": m.top_signals(1, 8) if m else [],
        "learned_negative": m.top_signals(0, 6) if m else [],
        "vocab": len(m.vocab) if m else 0,
    }
