# Aether Engine

An autonomous distribution engine for a digital product. It runs on a laptop,
costs nothing to operate, and does the one thing that actually compounds:
publishes useful long-tail content that funnels to a checkout link, 24/7.

```
HARVEST ──► SCORE ──► SYNTHESISE ──► APPROVE ──► PUBLISH ──► REVENUE
   │           │           │            │            │           │
 public     intent      drafts +     one click    own site    Gumroad
  APIs     ranking     SEO pages     (social)    (unattended)   API
```

## What it does

**Harvest** — pulls new posts from Hacker News, StackExchange, RSS and Reddit.
All documented public endpoints, politely rate-limited.

> **Reddit caveat, measured not assumed:** anonymous Reddit access is unreliable.
> It 403s `/.json` outright, and rate-limits even the public `.rss` feed — 3 to 5
> of 6 subreddits fail per sweep, and widening the delay barely helps (4/6 failed
> at 1.5s, 3/6 at 7s). Failed subreddits are picked up next cycle rather than
> retried in-burst, which never works. For reliable Reddit, create a free
> "script" app at <https://www.reddit.com/prefs/apps> and set `REDDIT_CLIENT_ID`
> / `REDDIT_CLIENT_SECRET` — the engine then uses the official OAuth API (100
> req/min). **HN, StackExchange and RSS are unaffected and supply the bulk of
> signals either way.**

**Score** — ranks each post by how well it matches a problem the product solves.
Keyword weighting first (free, instant); an LLM re-ranks the survivors if one is
available.

**Synthesise** — drafts genuinely useful replies to high-intent posts, plus
programmatic SEO pages across a role × topic matrix.

**Approve** — social drafts land in a queue in the dashboard. One click to
approve, one to copy. They are never auto-posted (see *Boundaries* below).

**Publish** — SEO pages go to `site/` unattended, with `index.html`,
`sitemap.xml` and `robots.txt` regenerated each time. Push to GitHub Pages and
they're live and indexable.

**Heal** — any subsystem that throws is logged under a stable signature,
backed off exponentially, and optionally patched by an LLM (backed up and
syntax-checked before anything is written). The daemon does not crash.

## Quick start

```powershell
cd C:\Users\ABDULLAH\Downloads\aether-engine
Copy-Item .env.example .env
.\scripts\run.ps1
```

The dashboard opens at <http://127.0.0.1:7717>.

It runs with zero configuration and zero keys — but **it earns $0 until
`CHECKOUT_URL` is set**, because every buy button points nowhere. The dashboard
shows a banner until you fix that.

## The 40 minutes only you can do

1. Create a free Gumroad account and upload the toolkit as a product.
2. Copy the product URL into `.env` as `CHECKOUT_URL`.
3. `.\scripts\deploy.ps1 -Repo aether-engine` → publishes `site/` to GitHub Pages.
4. Put the Pages URL into `.env` as `SITE_BASE_URL`, restart.

Steps 1–2 need your CNIC/identity. No software can do them for you — not this
engine, not any agent. That is the entire gap between $0 and a first sale.

## Boundaries

Social posting is queue-and-click, not automatic. This is a deliberate design
choice, not a limitation: posting through unofficial endpoints is the fastest
way to get every account you own permanently banned, which ends the traffic and
therefore the income. A two-second click per post is cheap insurance.

Likewise, everything harvested comes from endpoints that are published for
public consumption. Nothing here logs into anything, evades a block, or resells
data it doesn't own.

## Cost

| Component | Cost |
|---|---|
| Runtime (Python stdlib only) | $0 |
| Sources (Reddit/HN/StackExchange/RSS) | $0 |
| LLM (Ollama local, or Groq free tier, or built-in templates) | $0 |
| Hosting (GitHub Pages) | $0 |
| Payments (Gumroad) | ~10% per sale, $0 fixed |

## Layout

```
engine/
  main.py       scheduler daemon
  sources.py    harvesting
  scorer.py     intent ranking
  generator.py  replies, posts, SEO pages
  publisher.py  site output + approval queue
  healer.py     error capture, backoff, LLM patching
  revenue.py    Gumroad sync
  server.py     dashboard HTTP + SSE
  db.py         SQLite
dashboard/      three.js control room
site/           generated, published to Pages
scripts/        run.ps1, deploy.ps1
```

## Tuning

Everything lives in `.env` — intervals, subreddits, port, LLM backend. Edit
`SEO_ROLES` and `SEO_TEMPLATES` in `engine/config.py` to change the page matrix
(currently 16 roles × 5 templates = 80 pages before it repeats).

## Verified

Tested end to end on 2026-07-24: 269 signals harvested live (HN 119,
StackExchange 50, Reddit 100, RSS 25), 17 scored hot, drafts queued, 3 SEO pages
generated and published, approve endpoint working, `.gitignore` confirmed to
exclude `.env` and `state/`, and self-healing confirmed to absorb a crash and
back off without killing the daemon.

Known limitation: anonymous Reddit harvesting is partial (see the caveat above).
Everything else runs clean.
