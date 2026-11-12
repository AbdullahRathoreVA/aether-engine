---
name: aether-engine
description: Autonomous distribution engine for a digital product. Harvests buying-intent posts from Hacker News, StackExchange, RSS and Reddit, ranks them with a self-training Naive Bayes model, drafts helpful replies, and builds long-tail SEO pages (schema, FAQ, internal links, sitemap) that funnel to your checkout link. Use when the user wants to find people asking for what they sell, generate programmatic SEO pages, audit published pages for orphans, or review outreach drafts. Pure Python stdlib, no API keys required.
---

# Aether Engine

Run every command from the directory that contains this `SKILL.md`. Each prints
JSON on stdout (logs go to stderr) and exits non-zero on failure.

```
python -m engine.cli <command> [n|id]
```

| Command | What it does |
|---|---|
| `status` | counts of signals, pages, drafts; which LLM backend is active |
| `harvest` | pull new posts from HN, StackExchange, RSS, Reddit |
| `score [n]` | rank up to n unscored posts by buying intent |
| `top [n]` | the n highest-intent posts |
| `seo [n]` | build n new long-tail SEO pages |
| `publish` | write pages to `docs/` with index, sitemap, robots.txt |
| `audit` | measured site health: orphans, schema, FAQ, internal links |
| `queue [n]` | reply drafts awaiting a human decision |
| `approve <id>` / `reject <id>` | decide a draft; each decision trains the intent model |
| `train` | training samples so far and cross-validated accuracy |

## Workflow

1. `status` — if `CHECKOUT_URL` is unset, tell the user first: pages will
   publish, but buy buttons go nowhere until it is set in `.env`.
2. `harvest` → `score` → `top 5` → show the user the posts worth answering.
3. `queue` → show drafts. Approve or reject **only on the user's say-so**;
   never post anything yourself. Posting is the user's click, by design.
4. `seo 5` → `publish` → `audit`. If `audit` reports orphans, say so.

## Configuration

Copy `.env.example` to `.env`. Only `CHECKOUT_URL` matters for revenue.
Optional: `SITE_BASE_URL` (enables sitemap), `REDDIT_CLIENT_ID`/`SECRET`
(reliable Reddit), `ANTHROPIC_API_KEY` (Claude Fable 5.2, best output, hard
daily cap `ANTHROPIC_DAILY_USD`, default $2), `OLLAMA_URL` or `GROQ_API_KEY`
(free; without any, deterministic templates are used). Set `SITE_AUTHOR` for the
schema.org author; `SHOWCASE_*` / `SERVICE_*` add a second "hire me" offer
to every page and are off by default. Edit `SEO_ROLES` / `SEO_TEMPLATES` in
`engine/config.py` to retarget the page matrix at a different product.

## Network and data

Harvesting makes read-only requests to public, documented endpoints:
`hn.algolia.com`, `api.stackexchange.com`, `hnrss.org`, `www.reddit.com`
(`oauth.reddit.com` only if Reddit keys are set) and any RSS feeds in `.env`.
`api.github.com/zen` is pinged as an online check. Post text is sent to an
LLM only if you configure one: local Ollama, `api.groq.com` (`GROQ_API_KEY`)
or `api.anthropic.com` (`ANTHROPIC_API_KEY`). It never logs in to a site,
never posts, and sends nothing else anywhere. State lives in `state/aether.db`
(SQLite); generated pages in `docs/`. A source that fails is skipped, not
retried in a burst.

Requires Python 3.10+. No `pip install`.
