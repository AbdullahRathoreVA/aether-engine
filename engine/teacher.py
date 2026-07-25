"""Learning without waiting for Abdullah — distillation from AI teachers.

The classifier in brain.py learns from his approve/reject clicks. That works,
but it has a cold start: no decisions, no model. He does not want to be the
teacher. So this module replaces him with the models themselves.

How it works (this is knowledge distillation / LLM-as-annotator, a standard
technique, not a metaphor):

  1. Take real posts the engine already harvested from the live internet.
  2. Ask every LLM backend available - local Ollama, hosted Groq, Anthropic -
     to judge each one. That is the "learn from other AIs" part, done for real.
  3. Where teachers AGREE, keep the label. Where they disagree, throw it away:
     disagreement means the example is ambiguous and would teach noise.
  4. Train the fast stdlib classifier on those labels.

The result: brain.py gets a trained model in minutes without a single click
from him, and the classifier ends up encoding the teachers' judgement while
running in microseconds with no API cost per signal.

Two honesty constraints kept from brain.py:
  - a distilled label is tagged as such, so accuracy from teacher labels is
    never presented as if he had confirmed it.
  - his own decisions, when they exist, OUTWEIGH the teachers. He is the ground
    truth for what he wants; the teachers are a bootstrap.
"""
from __future__ import annotations

import json
import re
import time

from . import brain, config, db, llm, net

# A teacher only counts if it can actually answer. Ordered strongest first.
TEACHER_SYSTEM = (
    "You judge whether a forum post describes someone who would genuinely "
    "benefit from a job-search product (ATS resume templates, AI prompts for "
    "job hunting, LinkedIn optimisation, an application tracker). You are "
    "sceptical. People venting, celebrating a new job, posting job ads, or "
    "discussing unrelated topics score LOW. People actively struggling to get "
    "interviews or past resume filters score HIGH."
)

TEACHER_PROMPT = (
    "Post:\n{text}\n\n"
    'Reply with JSON only: {{"score": <0-10>, "reason": "<8 words>"}}'
)


def _ask(backend: str, text: str) -> float | None:
    """Get one teacher's 0-10 verdict, or None if it cannot answer."""
    prompt = TEACHER_PROMPT.format(text=text[:1500])

    try:
        if backend == "ollama":
            out = net.post_json(
                f"{config.OLLAMA_URL}/api/generate",
                {"model": config.OLLAMA_MODEL, "prompt": prompt,
                 "system": TEACHER_SYSTEM, "stream": False,
                 "options": {"num_predict": 120, "temperature": 0.2}},
            ).get("response", "")
        elif backend == "groq" and config.GROQ_API_KEY:
            out = net.post_json(
                "https://api.groq.com/openai/v1/chat/completions",
                {"model": config.GROQ_MODEL, "max_tokens": 120,
                 "temperature": 0.2,
                 "messages": [{"role": "system", "content": TEACHER_SYSTEM},
                              {"role": "user", "content": prompt}]},
                headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
            )["choices"][0]["message"]["content"]
        elif backend == "anthropic" and config.ANTHROPIC_API_KEY:
            out = "".join(b.get("text", "") for b in net.post_json(
                "https://api.anthropic.com/v1/messages",
                {"model": config.ANTHROPIC_MODEL, "max_tokens": 120,
                 "system": TEACHER_SYSTEM,
                 "messages": [{"role": "user", "content": prompt}]},
                headers={"x-api-key": config.ANTHROPIC_API_KEY,
                         "anthropic-version": "2023-06-01"},
            ).get("content", []))
        else:
            return None
    except Exception:
        return None

    return _parse_score(out)


def _parse_score(raw: str) -> float | None:
    if not raw:
        return None
    fenced = re.search(r"```(?:json)?\s*(.+?)```", raw, re.S)
    if fenced:
        raw = fenced.group(1)
    # Try JSON first, then fall back to the first number in range.
    start = raw.find("{")
    if start != -1:
        for end in range(len(raw), start, -1):
            try:
                obj = json.loads(raw[start:end])
                if isinstance(obj, dict) and "score" in obj:
                    return max(0.0, min(10.0, float(obj["score"])))
            except Exception:
                continue
    m = re.search(r"\b(10|\d(?:\.\d)?)\b", raw)
    return max(0.0, min(10.0, float(m.group(1)))) if m else None


def available_teachers() -> list[str]:
    """Which teachers to actually consult.

    A weak teacher is worse than no second teacher. Measured 2026-07-25 on live
    data: groq (70B) and ollama (llama3.2:1b) disagreed on 14 of 18 posts, and
    adding the 1B model's labels dropped downstream accuracy from 65% to 53%.
    An ensemble only denoises when its members are individually competent -
    otherwise the agreement filter just discards almost everything and what
    survives is arbitrary.

    So: if a strong teacher is available, use only strong teachers. The local
    model stays as a generation fallback (llm.py) but is not trusted to label.
    """
    strong = []
    if config.ANTHROPIC_API_KEY:
        strong.append("anthropic")
    if config.GROQ_API_KEY:
        strong.append("groq")
    if strong:
        return strong

    # Nothing strong configured - a weak teacher beats no teacher at all, but
    # the caller is warned and MIN_TRAIN still guards the downstream model.
    return ["ollama"] if llm._ollama_ready() else []


def teacher_weight(backend: str) -> float:
    """Not all teachers are equal. Weight verdicts by demonstrated capability.

    Measured on this machine: llama3.2:1b called "current" a job title and
    rejected "registered nurse". It is useful as a second opinion but should not
    outvote a 70B model, so its verdict carries less weight in the average.
    """
    return {"anthropic": 1.0, "groq": 1.0, "ollama": 0.45}.get(backend, 0.6)


def distill(limit: int = 25, agreement_band: float = 2.5) -> dict:
    """Label harvested posts using every available teacher, keep the agreed ones.

    agreement_band: with 2+ teachers, verdicts must fall within this many points
    of each other or the example is discarded as ambiguous.
    """
    teachers = available_teachers()
    if not teachers:
        return {"labelled": 0, "skipped": 0, "teachers": [],
                "note": "no LLM backend reachable - nothing to learn from"}

    # Label quality is capped by teacher quality. With only llama3.2:1b as the
    # teacher, measured downstream accuracy sat around 67% - better than chance
    # but not strong. A second, larger teacher (Groq free tier) both improves
    # the labels and enables the agreement filter below, which is what actually
    # removes noise. One teacher means no disagreement can ever be detected.
    if len(teachers) == 1 and teachers[0] == "ollama":
        db.log("teacher",
               "only a small local teacher is configured; labels will be noisy. "
               "Add GROQ_API_KEY (free) for materially better labels.", "warn")

    # Only distil posts we have not already taught from.
    rows = db.q(
        "SELECT id, title, body FROM signals "
        "WHERE id NOT IN (SELECT COALESCE(signal_id,-1) FROM training) "
        "AND length(COALESCE(body,'')) > 120 "
        "ORDER BY harvested_at DESC LIMIT ?", (limit,))

    labelled = skipped = disputed = 0
    for row in rows:
        text = f"{row['title']}\n{row['body'] or ''}"
        graded = [(t, _ask(t, text)) for t in teachers]
        verdicts = [(t, v) for t, v in graded if v is not None]
        if not verdicts:
            skipped += 1
            continue

        scores = [v for _, v in verdicts]
        # Multiple teachers must broadly agree, or we learn nothing from it.
        # This filter is the whole point of an ensemble - with one teacher it
        # can never fire, which is why a second one matters so much.
        if len(scores) > 1 and (max(scores) - min(scores)) > agreement_band:
            disputed += 1
            skipped += 1
            continue

        # Capability-weighted average rather than a naive mean.
        wsum = sum(teacher_weight(t) for t, _ in verdicts)
        avg = sum(v * teacher_weight(t) for t, v in verdicts) / wsum

        # Only teach from confident verdicts; the middle is genuinely unclear.
        if 4.0 < avg < 6.0:
            skipped += 1
            continue

        brain.record("reply", 1 if avg >= 6.0 else 0, text,
                     source=f"teacher:{'+'.join(t for t, _ in verdicts)}",
                     signal_id=row["id"])
        labelled += 1

    if labelled:
        brain.train("reply", force=True)
        note = f"distilled {labelled} labels from {'+'.join(teachers)}"
        if disputed:
            note += f" ({disputed} discarded — teachers disagreed)"
        elif skipped:
            note += f" ({skipped} discarded — too ambiguous)"
        db.log("teacher", note)

    db.set_metric("last_distill", time.time())
    return {"labelled": labelled, "skipped": skipped, "disputed": disputed,
            "teachers": teachers}


def stats() -> dict:
    total = db.scalar("SELECT COUNT(*) FROM training")
    taught = db.scalar(
        "SELECT COUNT(*) FROM training WHERE source LIKE 'teacher:%'")
    human = total - taught
    return {
        "teachers": available_teachers(),
        "from_ai": taught,
        "from_you": human,
        "total": total,
        # His own clicks are ground truth; teacher labels are a bootstrap.
        "source_label": (f"{taught} learned from AI teachers"
                         + (f", {human} confirmed by you" if human else
                            " — no clicks needed")),
    }
