"""Central configuration. Everything is env-overridable; nothing here is secret."""
from __future__ import annotations

import os
from pathlib import Path

# Two layouts. Run from the repo (daemon, dashboard): everything lives beside
# the code. Run as an installed skill: AETHER_HOME is the user's project folder,
# so state and output never land inside the skill directory.
PKG_ROOT = Path(__file__).resolve().parent.parent
_home = os.environ.get("AETHER_HOME", "").strip()
WORKSPACE_MODE = bool(_home)
ROOT = Path(_home).expanduser().resolve() if _home else PKG_ROOT
STATE_DIR = ROOT / (".aether" if WORKSPACE_MODE else "state")
PATCH_DIR = STATE_DIR / "patches"
DB_PATH = STATE_DIR / "aether.db"

if not WORKSPACE_MODE:  # a workspace is only written to by `init` and later
    for _d in (STATE_DIR, PATCH_DIR):
        _d.mkdir(parents=True, exist_ok=True)


def _load_dotenv() -> None:
    """Minimal .env reader so we never need python-dotenv."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        # A real value must beat a blank placeholder, whichever comes first in
        # the file. setdefault() alone let an empty `KEY=` line permanently mask
        # a real value appended later — a silent, very confusing failure.
        if val or key not in os.environ:
            os.environ[key] = val


_load_dotenv()


def env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def env_int(key: str, default: int) -> int:
    try:
        return int(env(key, str(default)))
    except ValueError:
        return default


def env_float(key: str, default: float) -> float:
    try:
        return float(env(key, str(default)))
    except ValueError:
        return default


def env_bool(key: str, default: bool = False) -> bool:
    return env(key, "1" if default else "0").lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------- product ----
# Everything niche-specific comes from aether.json (see profile.py). A value set
# in the environment wins, so one profile can be deployed to several places.
from . import profile as _profile  # noqa: E402

PROFILE = _profile.load(ROOT)
_prod, _site = PROFILE["product"], PROFILE["site"]
_offer = PROFILE.get("second_offer") or {}


def _pick(key: str, value, default: str = "") -> str:
    return env(key) or str(value or "").strip() or default


PRODUCT_NAME = _pick("PRODUCT_NAME", _prod.get("name"))
PRODUCT_PRICE = _pick("PRODUCT_PRICE", _prod.get("price"))
PRODUCT_DESCRIPTION = str(_prod.get("description") or "").strip()
PRODUCT_AUDIENCE = str(_prod.get("audience") or "").strip()
CHECKOUT_URL = _pick("CHECKOUT_URL", _prod.get("checkout_url"))
if not _profile.is_http_url(CHECKOUT_URL):
    CHECKOUT_URL = ""  # never render a javascript: or relative buy link
SITE_BASE_URL = _pick("SITE_BASE_URL", _site.get("base_url"))
if not _profile.is_http_url(SITE_BASE_URL):
    SITE_BASE_URL = ""
SITE_TITLE = str(_site.get("title") or "").strip()
SITE_LEDE = str(_site.get("lede") or "").strip()
# schema.org author on every page; blank omits it.
SITE_AUTHOR = _pick("SITE_AUTHOR", _site.get("author"))
# Tag checkout links with the page they came from, so the seller's own checkout
# analytics show which page earned each sale.
TRACK_REF = bool(_site.get("track_ref", True))
CONTACT_WHATSAPP = env("CONTACT_WHATSAPP", "")

# GitHub Pages only serves from the repo root or /docs, so the repo layout keeps
# docs/; a workspace defaults to site/.
_dir = str(_site.get("dir") or "").strip() if PROFILE["_exists"] else ""
if not _dir or Path(_dir).is_absolute() or ".." in Path(_dir).parts:
    _dir = "site" if WORKSPACE_MODE else "docs"
SITE_DIR = ROOT / _dir

CTA_TITLE = str(PROFILE["cta"].get("title") or "").strip()
CTA_BODY = str(PROFILE["cta"].get("body") or "").strip()
CTA_BUTTON = str(PROFILE["cta"].get("button") or "").strip()
REPLY_PERSONA = str(PROFILE["voice"].get("reply_persona") or "").strip()
PAGE_PERSONA = str(PROFILE["voice"].get("page_persona") or "").strip()
TEMPLATE_HOOKS = _profile._strs(PROFILE["voice"].get("template_hooks"))
TEMPLATE_ANGLES = _profile._strs(PROFILE["voice"].get("template_angles"))

# ---------------------------------------------------- second, optional offer --
# A second, higher-priced offer under every page (a service, a bigger product).
# Off unless the profile or environment supplies one: a page never advertises
# anything its owner did not put there.
SHOWCASE_NAME = _pick("SHOWCASE_NAME", _offer.get("name"))
SHOWCASE_URL = _pick("SHOWCASE_URL", _offer.get("url"))
SHOWCASE_TAGLINE = _pick("SHOWCASE_TAGLINE", _offer.get("tagline"))
SERVICE_NAME = _pick("SERVICE_NAME", _offer.get("service"))
SERVICE_PRICE_FROM = _pick("SERVICE_PRICE_FROM", _offer.get("price_from"))
SERVICE_CONTACT = _pick("SERVICE_CONTACT", _offer.get("contact"))
SHOWCASE_ENABLED = (env_bool("SHOWCASE_ENABLED", bool(_offer.get("url")))
                    and _profile.is_http_url(SHOWCASE_URL))

# ---------------------------------------------------------------- cadence ----
# Seconds between each subsystem's runs. Defaults are deliberately polite:
# every source we hit is a free public API and we stay far under its limits.
INTERVAL_HARVEST = env_int("INTERVAL_HARVEST", 900)      # 15 min
INTERVAL_SCORE = env_int("INTERVAL_SCORE", 120)          # 2 min
INTERVAL_GENERATE = env_int("INTERVAL_GENERATE", 600)    # 10 min
INTERVAL_SEO = env_int("INTERVAL_SEO", 3600)             # 1 hr
INTERVAL_REVENUE = env_int("INTERVAL_REVENUE", 1800)     # 30 min
INTERVAL_HEAL = env_int("INTERVAL_HEAL", 300)            # 5 min

HTTP_TIMEOUT = env_int("HTTP_TIMEOUT", 20)
USER_AGENT = env(
    "USER_AGENT",
    "aether-engine/1.0 (personal research bot; contact via repo issues)",
)

# ------------------------------------------------------------------- llm -----
# Resolution order: Ollama (local, free) -> hosted free tier -> templates.
# The template path means the engine runs fully with zero keys and zero cost.
OLLAMA_URL = env("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = env("OLLAMA_MODEL", "llama3.2")
# Off by default: a small local model is usually WORSE than a free hosted one.
# Turn on for offline use or when the text must not leave the machine.
PREFER_LOCAL = env_bool("PREFER_LOCAL", False)
GROQ_API_KEY = env("GROQ_API_KEY")
# If this model is retired, llm.py picks a live one from Groq's model list.
GROQ_MODEL = env("GROQ_MODEL", "openai/gpt-oss-120b")
ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = env("ANTHROPIC_MODEL", "claude-fable-5-2")
# low | medium | high: how hard Claude thinks. Thinking is billed as output.
ANTHROPIC_EFFORT = env("ANTHROPIC_EFFORT", "medium")
# Hard daily spend cap. Over it, the engine drops to Groq/Ollama/templates.
ANTHROPIC_DAILY_USD = env_float("ANTHROPIC_DAILY_USD", 2.0)
LLM_MAX_TOKENS = env_int("LLM_MAX_TOKENS", 1200)

# ---------------------------------------------------------------- server -----
HOST = env("HOST", "127.0.0.1")
PORT = env_int("PORT", 7717)

# ---------------------------------------------------------------- agents -----
# Each subsystem runs as a named agent so the dashboard can show who is doing
# what, right now, rather than an anonymous log.
AGENTS = [
    # HUNTER first: it works the lane that pays in weeks, not months.
    ("hunter",    "HUNTER",    "Service Lead Finder", "leads"),
    ("prospector","PROSPECTOR","Opportunity Scout", "prospector"),
    ("oracle",    "ORACLE",    "Learning Model",    "brain"),
    ("mentor",    "MENTOR",    "AI Teacher",        "teacher"),
    ("evolver",   "EVOLVER",   "Self-Improvement",  "evolve"),
    ("sentinel",  "SENTINEL",  "Reach Verifier",    "indexing"),
    ("portfolio", "PORTFOLIO", "Project Watch",     "projects"),
    ("scout",     "SCOUT",     "Signal Harvester",  "sources"),
    ("analyst",   "ANALYST",   "Intent Analyst",    "scorer"),
    ("scribe",    "SCRIBE",    "Content Writer",    "generator"),
    ("architect", "ARCHITECT", "SEO Page Builder",  "generator"),
    ("herald",    "HERALD",    "Publisher",         "publisher"),
    ("ledger",    "LEDGER",    "Revenue Tracker",   "revenue"),
    ("medic",     "MEDIC",     "Self-Repair",       "healer"),
]

INTERVAL_LEADS = env_int("INTERVAL_LEADS", 1200)     # 20 min
INTERVAL_PROSPECT = env_int("INTERVAL_PROSPECT", 5400)  # 90 min
INTERVAL_DISTILL = env_int("INTERVAL_DISTILL", 2700)    # 45 min
INTERVAL_EVOLVE = env_int("INTERVAL_EVOLVE", 7200)      # 2 hr
DISTILL_BATCH = env_int("DISTILL_BATCH", 10)
INTERVAL_INDEX = env_int("INTERVAL_INDEX", 3600)        # 1 hr
INTERVAL_PROJECTS = env_int("INTERVAL_PROJECTS", 7200)  # 2 hr

# ------------------------------------------------------------------ safety ---
# Self-healing writes to disk. It always backs up, always syntax-checks, and
# only applies automatically when explicitly enabled.
AUTO_PATCH = env_bool("AUTO_PATCH", False)
MAX_PATCH_ATTEMPTS = env_int("MAX_PATCH_ATTEMPTS", 3)

# Publishing boundary. Social drafts NEVER auto-post: they queue for one-click
# approval. Only our own static site publishes unattended.
AUTO_PUBLISH_SITE = env_bool("AUTO_PUBLISH_SITE", True)
AUTO_PUBLISH_SOCIAL = False  # intentionally not env-overridable

# --------------------------------------------------------------- harvest -----
# Optional. Anonymous Reddit access is rate-limited to a partial sweep; a free
# "script" app at https://www.reddit.com/prefs/apps makes it reliable.
REDDIT_CLIENT_ID = env("REDDIT_CLIENT_ID")
REDDIT_CLIENT_SECRET = env("REDDIT_CLIENT_SECRET")

_src, _intent, _matrix = PROFILE["sources"], PROFILE["intent"], PROFILE["matrix"]

SUBREDDITS = ([x.strip() for x in env("SUBREDDITS").split(",") if x.strip()]
              or _profile._strs(_src.get("subreddits")))
HN_QUERIES = _profile._strs(_src.get("hn_queries"))
RSS_FEEDS = _profile._strs(_src.get("rss"))
# [{"site": "workplace", "tagged": "resume"}] - a bare string is a site name.
STACKEXCHANGE = [
    {"site": str((x.get("site") if isinstance(x, dict) else x) or "").strip(),
     "tagged": str((x.get("tagged") if isinstance(x, dict) else "") or "").strip()}
    for x in (_src.get("stackexchange") or [])
]

# weight: phrases that signal someone actively hurting in a way the product solves
INTENT_KEYWORDS = {
    3.0: [x.lower() for x in _profile._strs(_intent.get("strong"))],
    2.0: [x.lower() for x in _profile._strs(_intent.get("medium"))],
    1.0: [x.lower() for x in _profile._strs(_intent.get("weak"))],
}
NEGATIVE_KEYWORDS = [x.lower() for x in _profile._strs(_intent.get("negative"))]
FIRST_PERSON = [" i ", "i'm ", "im ", " my ", "we're ", " our "] + [
    x.lower() for x in _profile._strs(_intent.get("first_person"))]

# Optional programmatic matrix: every subject x every template is one page.
SEO_ROLES = _profile._strs(_matrix.get("subjects"))
MATRIX_TEMPLATES = [t for t in (_matrix.get("templates") or [])
                    if isinstance(t, dict) and "{subject}" in str(t.get("slug", ""))]
SEO_TEMPLATES = [(str(t["slug"]).replace("{subject}", "{role}"),
                  str(t.get("title", "")).replace("{Subject}", "{Role}")
                  .replace("{subject}", "{role}")) for t in MATRIX_TEMPLATES]
