"""Central configuration. Everything is env-overridable; nothing here is secret."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "state"
# GitHub Pages only serves from the repo root or /docs - not an arbitrary
# folder - so the generated site lives in docs/ to be publishable as-is.
SITE_DIR = ROOT / "docs"
PATCH_DIR = ROOT / "state" / "patches"
DB_PATH = STATE_DIR / "aether.db"

for _d in (STATE_DIR, SITE_DIR, PATCH_DIR):
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
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_dotenv()


def env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def env_int(key: str, default: int) -> int:
    try:
        return int(env(key, str(default)))
    except ValueError:
        return default


def env_bool(key: str, default: bool = False) -> bool:
    return env(key, "1" if default else "0").lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------- product ----
# The funnel terminates here. Until CHECKOUT_URL is real, the engine generates
# traffic into a dead end — it will warn loudly on every boot.
#
# PAYMENTS, PAKISTAN: Gumroad / Payhip / Lemon Squeezy / Etsy / Buy Me a Coffee
# all pay out ONLY via PayPal or Stripe direct-bank, and Pakistan is restricted
# on both — you can make sales and never withdraw them. Verified 2026-07-24.
# Use Dodo Payments (Merchant of Record -> Payoneer/Wise) or Payoneer Checkout.
PRODUCT_NAME = env("PRODUCT_NAME", "AI Job-Search Toolkit")
PRODUCT_PRICE = env("PRODUCT_PRICE", "$14")
CHECKOUT_URL = env("CHECKOUT_URL", "")
SITE_BASE_URL = env("SITE_BASE_URL", "")  # e.g. https://user.github.io/repo
CONTACT_WHATSAPP = env("CONTACT_WHATSAPP", "")

# ---------------------------------------------------- second, high-ticket ----
# A $14 download and a high-ticket service funnel from the same traffic. The
# cheap product converts strangers; the service converts the few who need more.
# One visitor can only buy the toolkit; one visitor in a thousand hires you.
SHOWCASE_NAME = env("SHOWCASE_NAME", "Titan Omega")
SHOWCASE_URL = env("SHOWCASE_URL",
                   "https://careermind2026-project-titan-omega.hf.space")
SHOWCASE_TAGLINE = env(
    "SHOWCASE_TAGLINE",
    "an autonomous AI command centre with 12 agent divisions, "
    "built and deployed solo")
SERVICE_NAME = env("SERVICE_NAME", "AI automation build-outs")
SERVICE_PRICE_FROM = env("SERVICE_PRICE_FROM", "$400")
SERVICE_CONTACT = env("SERVICE_CONTACT", "")  # email, Upwork, or WhatsApp link
SHOWCASE_ENABLED = env_bool("SHOWCASE_ENABLED", True)

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
GROQ_MODEL = env("GROQ_MODEL", "llama-3.3-70b-versatile")
ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = env("ANTHROPIC_MODEL", "claude-sonnet-5")
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

SUBREDDITS = [
    s.strip() for s in env(
        "SUBREDDITS",
        "jobs,resumes,cscareerquestions,careerguidance,GetEmployed,jobsearchhacks",
    ).split(",") if s.strip()
]

INTENT_KEYWORDS = {
    # weight: phrases that signal someone actively hurting in a way we solve
    3.0: ["ats rejec", "resume not getting", "no callback", "no interviews",
          "applied to 100", "applied to 200", "ghosted by recruit",
          "resume black hole", "auto rejected"],
    2.0: ["ats friendly", "resume template", "rewrite my resume", "resume help",
          "cover letter help", "optimize my linkedin", "linkedin profile help",
          "how to tailor resume", "keyword optimize"],
    1.0: ["job search", "applying for jobs", "interview prep", "career change",
          "laid off", "job hunt", "application tracker"],
}

NEGATIVE_KEYWORDS = [
    "hiring", "we are looking for", "job posting", "[hiring]", "salary thread",
    "offer letter signed", "i got the job", "accepted the offer",
]

# Long-tail programmatic SEO matrix. Real search demand, zero ToS risk,
# compounds while the laptop sleeps.
SEO_ROLES = [
    "software engineer", "data analyst", "project manager", "accountant",
    "nurse", "marketing manager", "customer support", "sales representative",
    "graphic designer", "mechanical engineer", "teacher", "business analyst",
    "product manager", "devops engineer", "financial analyst", "hr generalist",
]

SEO_TEMPLATES = [
    ("ats-resume-keywords-for-{role}", "ATS Resume Keywords for {Role} (2026 List)"),
    ("resume-summary-examples-{role}", "15 Resume Summary Examples for {Role}"),
    ("cover-letter-template-{role}", "Cover Letter Template for {Role} That Passes ATS"),
    ("linkedin-headline-examples-{role}", "LinkedIn Headline Examples for {Role}"),
    ("ai-prompts-for-{role}-job-search", "10 AI Prompts for a {Role} Job Search"),
]
