"""Orchestrator daemon.

A single scheduler thread runs every subsystem on its own interval, each call
wrapped in the healing guard. Any subsystem can fail permanently without taking
down the rest. Ctrl-C exits cleanly.
"""
from __future__ import annotations

import signal
import sys
import time
import webbrowser

from . import (agents, brain, config, db, generator, healer, leads, llm,
               prospector, publisher, revenue, scorer, server, sources)

_running = True


def _stop(*_):
    global _running
    _running = False
    print("\nshutting down...", flush=True)


class Task:
    def __init__(self, name: str, fn, interval: int, *, jitter: float = 0.1):
        self.name = name
        self.fn = fn
        self.interval = interval
        self.jitter = jitter
        self.next_run = 0.0

    def due(self, now: float) -> bool:
        return now >= self.next_run and not healer.blocked(self.name)

    def schedule(self, now: float) -> None:
        import random
        drift = self.interval * self.jitter
        self.next_run = now + self.interval + random.uniform(-drift, drift)


def preflight() -> None:
    db.log("boot", "=" * 58)
    db.log("boot", f"AETHER ENGINE  |  product: {config.PRODUCT_NAME}")
    db.log("boot", f"LLM backend: {llm.detect_backend()}")
    db.log("boot", f"sources: reddit({len(config.SUBREDDITS)}), hn, stackexchange, rss")

    if not config.CHECKOUT_URL:
        db.log("boot", "!" * 58, "warn")
        db.log("boot", "CHECKOUT_URL is empty. The engine will harvest, score, "
                       "write and publish normally - but every page's buy button "
                       "goes nowhere, so revenue stays exactly $0.", "warn")
        db.log("boot", "Fix: create a Dodo Payments product, put its URL in .env, "
                       "restart. NOT Gumroad - it cannot pay out to Pakistan.", "warn")
        db.log("boot", "!" * 58, "warn")

    if not config.SITE_BASE_URL:
        db.log("boot", "SITE_BASE_URL empty - sitemap.xml will be skipped until "
                       "you deploy (scripts/deploy.ps1 sets this up)", "warn")

    if llm.detect_backend() == "template":
        db.log("boot", "No LLM reachable - running on deterministic templates. "
                       "Install Ollama or set GROQ_API_KEY for varied output.", "warn")


def build_tasks() -> list[Task]:
    """Each cycle runs under a named agent so the dashboard can attribute work."""

    def lead_cycle():
        agents.run("hunter", "leads", "hunting paid service leads",
                   leads.harvest_leads)
        agents.run("hunter", "leads", "drafting outreach to the best leads",
                   leads.draft_outreach)

    def prospect_cycle():
        agents.run("prospector", "prospector",
                   "mining live posts for niches we do not cover yet",
                   prospector.expand_targets)
        agents.run("oracle", "brain", "retraining on your latest decisions",
                   lambda: brain.train("reply", force=True) is not None)

    def harvest_cycle():
        agents.run("scout", "sources", "sweeping public sources for new signals",
                   sources.harvest_all)

    def score_cycle():
        agents.run("analyst", "scorer", "ranking signals by buying intent",
                   scorer.score_pending)

    def generate_cycle():
        agents.run("scribe", "generator", "drafting replies to high-intent posts",
                   generator.generate_replies)
        agents.run("scribe", "generator", "writing a standalone value post",
                   generator.generate_social_post)

    def seo_cycle():
        agents.run("architect", "generator", "building long-tail SEO pages",
                   generator.generate_seo_pages, count=2)
        if config.AUTO_PUBLISH_SITE:
            agents.run("herald", "publisher", "publishing pages + sitemap",
                       publisher.publish_pages)

    def revenue_cycle():
        agents.run("ledger", "revenue", "checking for new sales",
                   revenue.sync_sales)

    def heal_cycle():
        agents.run("medic", "healer", "scanning for faults to repair",
                   healer.attempt_patch)
        db.prune()

    return [
        Task("leads",      lead_cycle,     config.INTERVAL_LEADS),
        Task("prospector", prospect_cycle, config.INTERVAL_PROSPECT),
        Task("sources",   harvest_cycle,  config.INTERVAL_HARVEST),
        Task("scorer",    score_cycle,    config.INTERVAL_SCORE),
        Task("generator", generate_cycle, config.INTERVAL_GENERATE),
        Task("publisher", seo_cycle,      config.INTERVAL_SEO),
        Task("revenue",   revenue_cycle,  config.INTERVAL_REVENUE),
        Task("healer",    heal_cycle,     config.INTERVAL_HEAL),
    ]


def main(open_browser: bool = True) -> int:
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    db.init()
    brain.init()
    agents.init()
    db.set_metric("started_at", time.time())
    preflight()

    server.serve_forever()
    if open_browser:
        try:
            webbrowser.open(f"http://{config.HOST}:{config.PORT}")
        except Exception:
            pass

    tasks = build_tasks()

    # Stagger the first run so we do not hit four APIs in the same second.
    now = time.time()
    for i, t in enumerate(tasks):
        t.next_run = now + i * 3

    db.log("boot", "engine running - Ctrl-C to stop")

    while _running:
        now = time.time()
        for task in tasks:
            if not _running:
                break
            if task.due(now):
                healer.guard(task.name, task.fn)
                task.schedule(time.time())
        time.sleep(1)

    db.log("boot", "stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main(open_browser="--no-browser" not in sys.argv))
