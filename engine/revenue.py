"""Revenue ingestion.

PAKISTAN PAYMENT REALITY (verified 2026-07-24 - do not "optimise" this back):
Gumroad, Payhip, Lemon Squeezy, Etsy and Buy Me a Coffee pay out only through
PayPal or Stripe direct-bank. Pakistan is restricted on both. You can make sales
on those platforms and be unable to withdraw a single dollar. Gumroad's own docs
say that if a country has neither method, "there is no way to pay you out."

What works from Pakistan:
  - Dodo Payments  - Merchant of Record, pays out to Payoneer / Wise. Handles US
                     sales tax and EU VAT for you. Treated by FBR as IT-services
                     export. This is the default here.
  - Payoneer Checkout - your own payment link, funds land in Payoneer.
  - Local PK buyers   - Easypaisa / JazzCash direct, logged manually below.

With no key configured this module is inert and the dashboard shows zero. It
never invents numbers.
"""
from __future__ import annotations

import time

from . import config, db, net

DODO_API_KEY = config.env("DODO_API_KEY")
DODO_BASE = config.env("DODO_BASE", "https://live.dodopayments.com")


def _record(source: str, external_id: str, cents: int, currency: str,
            product: str, ts: float | None = None) -> bool:
    return bool(db.x(
        "INSERT OR IGNORE INTO revenue"
        "(ts,source,external_id,amount_cents,currency,product) VALUES(?,?,?,?,?,?)",
        (ts or time.time(), source, external_id, cents, currency, product),
    ))


def sync_dodo() -> int:
    """Pull recent payments from Dodo. Schema-tolerant: their payload has moved
    before, so we read defensively rather than assume exact field names."""
    if not DODO_API_KEY:
        return 0

    try:
        data = net.get_json(
            f"{DODO_BASE.rstrip('/')}/payments?page_size=100",
            headers={"Authorization": f"Bearer {DODO_API_KEY}"},
        )
    except Exception as e:
        db.log("revenue", f"Dodo sync failed: {type(e).__name__}", "warn")
        return 0

    items = data if isinstance(data, list) else (
        data.get("items") or data.get("data") or data.get("payments") or [])

    new = 0
    for it in items:
        if not isinstance(it, dict):
            continue
        status = str(it.get("status", "")).lower()
        if status and status not in ("succeeded", "success", "paid", "completed"):
            continue

        pid = str(it.get("payment_id") or it.get("id") or "")
        if not pid:
            continue

        # Dodo reports minor units; fall back to a float amount if that changes.
        amount = it.get("total_amount", it.get("amount"))
        try:
            cents = int(amount) if isinstance(amount, int) else int(float(amount) * 100)
        except (TypeError, ValueError):
            continue

        if _record("dodo", pid, cents,
                   str(it.get("currency", "USD")).upper(),
                   str(it.get("product_name") or it.get("product_id")
                       or config.PRODUCT_NAME)):
            new += 1

    if new:
        db.log("revenue", f"{new} new Dodo sale(s)", "info")
    return new


def log_manual(amount: float, currency: str = "PKR", product: str = "",
               note: str = "") -> bool:
    """Record a direct sale (Easypaisa / JazzCash / bank). Zero fees, so these
    are worth tracking properly rather than guessing at month end."""
    ext = f"manual-{int(time.time() * 1000)}"
    ok = _record("manual", ext, int(round(amount * 100)), currency.upper(),
                 product or config.PRODUCT_NAME)
    if ok:
        db.log("revenue", f"manual sale logged: {amount:.0f} {currency} {note}".strip())
        _recompute()
    return ok


def sync_sales() -> int:
    """Called by the LEDGER agent each cycle."""
    n = sync_dodo()
    _recompute()
    return n


def _recompute() -> None:
    db.set_metric("revenue_total_cents",
                  db.scalar("SELECT COALESCE(SUM(amount_cents),0) FROM revenue"))
    db.set_metric("revenue_month_cents", db.scalar(
        "SELECT COALESCE(SUM(amount_cents),0) FROM revenue WHERE ts > ?",
        (time.time() - 30 * 86400,)))
    db.set_metric("revenue_count", db.scalar("SELECT COUNT(*) FROM revenue"))


def summary() -> dict:
    _recompute()
    by_source = {r["source"]: r["n"] for r in db.q(
        "SELECT source, COUNT(*) AS n FROM revenue GROUP BY source")}
    return {
        "total_cents": db.get_metric("revenue_total_cents", 0),
        "month_cents": db.get_metric("revenue_month_cents", 0),
        "sales": db.get_metric("revenue_count", 0),
        "connected": bool(DODO_API_KEY),
        "processor": "dodo" if DODO_API_KEY else "none",
        "by_source": by_source,
    }
