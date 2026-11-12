"""LLM abstraction with graceful degradation.

Order of preference:
  1. Ollama on localhost      - free, private, works offline
  2. Groq free tier           - free, fast, needs a key
  3. Anthropic (Claude Fable)  - paid, best quality, capped by a daily budget
  4. Deterministic templates  - always available, costs nothing, never fails

Step 4 is what makes the "$0 and it never stops" claim honest: with no keys and
no Ollama, the engine still produces usable drafts, just less varied ones.
"""
from __future__ import annotations

import json
import random
import re
import time

from . import config, db, net

_backend_cache: str | None = None
_backend_checked: float = 0.0
# Re-detect this often so a model that finishes downloading is picked up without
# a restart, and a backend that dies is demoted on its own.
_BACKEND_TTL = 180.0

# Circuit breaker. A backend that fails three times running is benched for ten
# minutes, so a dead key or a retired model costs three calls, not hundreds.
_fails: dict[str, int] = {}
_benched: dict[str, float] = {}
_BENCH_AFTER, _BENCH_FOR = 3, 600.0
_groq_model = ""  # set when the configured model was retired and we recovered


def _alive(backend: str) -> bool:
    return time.time() >= _benched.get(backend, 0.0)


def _groq_recover() -> bool:
    """The configured Groq model was retired (404). Pick a live one."""
    global _groq_model
    try:
        data = net.get_json("https://api.groq.com/openai/v1/models",
                            headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"})
    except Exception:
        return False
    ids = [m.get("id", "") for m in data.get("data", [])]
    chat = [i for i in ids if not any(x in i for x in
            ("whisper", "guard", "orpheus", "tts", "embed"))]
    for want in ("gpt-oss-120b", "70b", "qwen", "gpt-oss-20b"):
        pick = next((i for i in chat if want in i), "")
        if pick and pick != (_groq_model or config.GROQ_MODEL):
            _groq_model = pick
            db.log("llm", f"Groq model {config.GROQ_MODEL} is gone; using {pick} "
                          f"(set GROQ_MODEL to choose)", "warn")
            return True
    return False


def _ollama_ready() -> bool:
    """True only if the server is up AND the configured model is pulled.

    Checking the server alone is not enough: while a model is still downloading
    the server responds fine but generation fails with 'model not found'. We
    verify the model is actually present so we do not thrash between Ollama and
    templates during a pull.
    """
    try:
        import json
        raw = net.fetch(f"{config.OLLAMA_URL}/api/tags", retries=0, timeout=2)
        tags = json.loads(raw)
    except Exception:
        return False

    names = [m.get("name", "") for m in tags.get("models", [])]
    want = config.OLLAMA_MODEL
    # Ollama tags carry a ':tag' suffix (llama3.2:latest); match either form.
    return any(n == want or n.split(":")[0] == want.split(":")[0] for n in names)


def detect_backend(force: bool = False) -> str:
    global _backend_cache, _backend_checked
    fresh = (time.time() - _backend_checked) < _BACKEND_TTL
    if _backend_cache and fresh and not force:
        return _backend_cache

    previous = _backend_cache

    # Order by OUTPUT QUALITY, not by where the model happens to live.
    #
    # A local model is not automatically better. Measured 2026-07-25: the local
    # llama3.2:1b accepted "current" as a job title and rejected "registered
    # nurse". Groq's free tier serves a 70B-class model that does not make that
    # class of mistake, costs nothing, needs no disk, and needs no GPU. On a
    # machine with integrated graphics and 14 GB free, the cloud model wins on
    # every axis except privacy.
    #
    # Set PREFER_LOCAL=1 to invert this (offline work, or private data).
    local_ok = _ollama_ready()

    if config.PREFER_LOCAL and local_ok and _alive("ollama"):
        backend = "ollama"
    elif (config.ANTHROPIC_API_KEY and _alive("anthropic")
          and claude_spent() < config.ANTHROPIC_DAILY_USD):
        backend = "anthropic"
    elif config.GROQ_API_KEY and _alive("groq"):
        backend = "groq"
    elif local_ok and _alive("ollama"):
        backend = "ollama"
    else:
        backend = "template"

    _backend_cache = backend
    _backend_checked = time.time()
    if previous and previous != backend:
        db.log("llm", f"backend changed: {previous} -> {backend}", "info")
    return backend


def _call(backend: str, prompt: str, system: str, max_tokens: int) -> str:
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
            {"model": _groq_model or config.GROQ_MODEL, "messages": msgs,
             "max_tokens": max_tokens, "temperature": 0.8},
            headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
        )
        return out["choices"][0]["message"]["content"].strip()

    if backend == "anthropic":
        return claude(prompt, system=system)

    return ""


def generate(prompt: str, *, system: str = "", max_tokens: int | None = None,
             _retry: bool = True) -> str:
    """Return model text, or '' if every backend fails (caller falls back)."""
    backend = detect_backend()
    max_tokens = max_tokens or config.LLM_MAX_TOKENS

    try:
        out = _call(backend, prompt, system, max_tokens)
        _fails[backend] = 0
        return out
    except Exception as e:
        # A bare "HTTPError" is undiagnosable. Surface the status and any
        # rate-limit headers: 12 consecutive silent failures during a bulk
        # rebuild looked identical to a dead key, when it was almost certainly
        # a tokens-per-minute cap that would have cleared on its own.
        detail = type(e).__name__
        code = getattr(e, "code", None)
        if code:
            detail = f"HTTP {code}"
            try:
                hdrs = {k.lower(): v for k, v in e.headers.items()}
                retry = hdrs.get("retry-after")
                remaining = (hdrs.get("x-ratelimit-remaining-tokens")
                             or hdrs.get("x-ratelimit-remaining-requests"))
                if code == 429:
                    detail = (f"rate limited (429"
                              + (f", retry after {retry}s" if retry else "")
                              + (f", remaining {remaining}" if remaining else "")
                              + ")")
            except Exception:
                pass
        if backend == "groq" and code == 404 and _retry and _groq_recover():
            return generate(prompt, system=system, max_tokens=max_tokens, _retry=False)
        db.log("llm", f"{backend} failed: {detail} - falling back", "warn")
        _fails[backend] = _fails.get(backend, 0) + 1
        if _fails[backend] >= _BENCH_AFTER:
            _benched[backend] = time.time() + _BENCH_FOR
            _fails[backend] = 0
            db.log("llm", f"{backend} benched for {_BENCH_FOR / 60:.0f} min after "
                          f"{_BENCH_AFTER} failures in a row", "warn")
        # Re-detect next call: Ollama may have been started, key may have expired.
        globals()["_backend_cache"] = None

    return ""


# Fable 5.2 list price per 1M tokens. Fallback models are cheaper, so billing
# every call at this rate can only over-count - the budget never leaks.
_USD_IN, _USD_OUT = 8.0, 40.0


def claude_spent() -> float:
    return float(db.get_metric("claude_usd_" + time.strftime("%Y-%m-%d"), 0) or 0)


def claude(prompt: str, *, system: str = "", effort: str = "") -> str:
    """One Claude call. Raises when the daily budget is spent; '' on refusal."""
    spent = claude_spent()
    if spent >= config.ANTHROPIC_DAILY_USD:
        raise RuntimeError(f"daily Claude budget ${config.ANTHROPIC_DAILY_USD:g} spent")
    out = net.post_json(
        "https://api.anthropic.com/v1/messages",
        {"model": config.ANTHROPIC_MODEL,
         # Thinking is always on and counts toward max_tokens: a tight cap
         # would be spent thinking and return no text. Length is set by prompt.
         "max_tokens": 16000,
         "output_config": {"effort": effort or config.ANTHROPIC_EFFORT},
         "fallbacks": "default",  # a refusal is retried on a suitable model
         "system": system or "You are a helpful assistant.",
         "messages": [{"role": "user", "content": prompt}]},
        headers={"x-api-key": config.ANTHROPIC_API_KEY,
                 "anthropic-version": "2023-06-01",
                 "anthropic-beta": "server-side-fallback-2026-07-01"},
        timeout=180,
    )
    u = out.get("usage") or {}
    cost = (u.get("input_tokens", 0) * _USD_IN + u.get("output_tokens", 0) * _USD_OUT) / 1e6
    db.set_metric("claude_usd_" + time.strftime("%Y-%m-%d"), round(spent + cost, 6))
    if out.get("stop_reason") == "refusal":
        return ""
    return "".join(b.get("text", "") for b in out.get("content", [])
                   if b.get("type") == "text").strip()


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
def template_post(topic: str, seed: int = 0) -> str:
    """A draft assembled from the profile's own hooks and angles.

    '' when the profile supplies none: with no model and no template text the
    honest output is nothing, not filler about someone else's niche.
    """
    hooks, angles = config.TEMPLATE_HOOKS, config.TEMPLATE_ANGLES
    if not (hooks and angles):
        return ""
    r = random.Random(seed or hash(topic) & 0xFFFF)
    return f"{r.choice(hooks)}\n\n{r.choice(angles)}\n\nThat's the whole idea behind {topic}."
