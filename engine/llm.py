"""LLM abstraction with graceful degradation.

Order of preference:
  1. Ollama on localhost      - free, private, works offline
  2. Groq free tier           - free, fast, needs a key
  3. Anthropic                - paid, best quality, needs a key
  4. Deterministic templates  - always available, costs nothing, never fails

Step 4 is what makes the "$0 and it never stops" claim honest: with no keys and
no Ollama, the engine still produces usable drafts, just less varied ones.
"""
from __future__ import annotations

import json
import random
import re

from . import config, db, net

_backend_cache: str | None = None


def detect_backend(force: bool = False) -> str:
    global _backend_cache
    if _backend_cache and not force:
        return _backend_cache

    backend = "template"
    try:
        net.fetch(f"{config.OLLAMA_URL}/api/tags", retries=0)
        backend = "ollama"
    except Exception:
        if config.GROQ_API_KEY:
            backend = "groq"
        elif config.ANTHROPIC_API_KEY:
            backend = "anthropic"

    _backend_cache = backend
    return backend


def generate(prompt: str, *, system: str = "", max_tokens: int | None = None) -> str:
    """Return model text, or '' if every backend fails (caller falls back)."""
    backend = detect_backend()
    max_tokens = max_tokens or config.LLM_MAX_TOKENS

    try:
        if backend == "ollama":
            out = net.post_json(
                f"{config.OLLAMA_URL}/api/generate",
                {
                    "model": config.OLLAMA_MODEL,
                    "prompt": prompt,
                    "system": system,
                    "stream": False,
                    "options": {"num_predict": max_tokens, "temperature": 0.8},
                },
            )
            return (out.get("response") or "").strip()

        if backend == "groq":
            msgs = ([{"role": "system", "content": system}] if system else [])
            msgs.append({"role": "user", "content": prompt})
            out = net.post_json(
                "https://api.groq.com/openai/v1/chat/completions",
                {"model": config.GROQ_MODEL, "messages": msgs,
                 "max_tokens": max_tokens, "temperature": 0.8},
                headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
            )
            return out["choices"][0]["message"]["content"].strip()

        if backend == "anthropic":
            out = net.post_json(
                "https://api.anthropic.com/v1/messages",
                {"model": config.ANTHROPIC_MODEL, "max_tokens": max_tokens,
                 "system": system or "You are a helpful assistant.",
                 "messages": [{"role": "user", "content": prompt}]},
                headers={"x-api-key": config.ANTHROPIC_API_KEY,
                         "anthropic-version": "2023-06-01"},
            )
            return "".join(b.get("text", "") for b in out.get("content", [])).strip()

    except Exception as e:
        db.log("llm", f"{backend} failed ({type(e).__name__}), falling back", "warn")
        # Re-detect next call: Ollama may have been started, key may have expired.
        globals()["_backend_cache"] = None

    return ""


def generate_json(prompt: str, *, system: str = "") -> dict | list | None:
    """Ask for JSON and survive models that wrap it in prose or code fences."""
    raw = generate(prompt, system=system + "\nRespond with JSON only.")
    if not raw:
        return None
    fenced = re.search(r"```(?:json)?\s*(.+?)```", raw, re.S)
    if fenced:
        raw = fenced.group(1)
    start = min((i for i in (raw.find("{"), raw.find("[")) if i != -1), default=-1)
    if start == -1:
        return None
    for end in range(len(raw), start, -1):
        try:
            return json.loads(raw[start:end])
        except Exception:
            continue
    return None


# ------------------------------------------------------- template fallback ---
_HOOKS = [
    "Most resumes die in the ATS, not in front of a human.",
    "The gap is almost never your experience. It's the keywords.",
    "Applying to 200 jobs with one resume is 200 rejections in a trench coat.",
    "Recruiters spend about 7 seconds on a resume. Design for those 7 seconds.",
    "If your resume isn't mirroring the job description's language, it isn't being read.",
]

_ANGLES = [
    "Paste the job description into an AI and ask it to extract the top 15 hard skills. "
    "Those exact strings belong in your resume - verbatim, where you actually have them.",
    "Rewrite every bullet as: action verb + what you did + a number. No number means no bullet.",
    "Your LinkedIn headline should read like a search query a recruiter would type, "
    "not like a job title your last employer invented.",
    "Tailor one resume per role family, not per job. Three good versions beat fifty rushed ones.",
    "Track every application in a sheet with the date, the keywords you targeted, and the "
    "outcome. Patterns show up within two weeks.",
]


def template_post(topic: str, seed: int = 0) -> str:
    r = random.Random(seed or hash(topic) & 0xFFFF)
    hook = r.choice(_HOOKS)
    angle = r.choice(_ANGLES)
    return f"{hook}\n\n{angle}\n\nThat's the whole idea behind {topic}."
