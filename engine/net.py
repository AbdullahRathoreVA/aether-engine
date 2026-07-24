"""Tiny stdlib HTTP client with polite rate limiting and retries.

Everything this engine touches is a documented public endpoint. We send a real
User-Agent, honour Retry-After, and self-throttle per host so we never become a
burden on a free service.
"""
from __future__ import annotations

import gzip
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from . import config

_host_lock = threading.Lock()
_last_hit: dict[str, float] = {}
MIN_GAP = 1.5  # default seconds between requests to the same host

# Reddit rate-limits unauthenticated clients hard and it is NOT purely a spacing
# problem: measured 4/6 subreddits failing at 1.5s and still 3/6 at 7s. Widening
# the gap helps only marginally, so treat anonymous Reddit as best-effort and set
# REDDIT_CLIENT_ID/SECRET for the official OAuth path when you want it reliable.
HOST_GAP = {
    "www.reddit.com": 7.0,
    "oauth.reddit.com": 1.2,   # 100 req/min authenticated - much more headroom
    "api.stackexchange.com": 2.5,
}


def _throttle(url: str) -> None:
    host = urllib.parse.urlparse(url).netloc
    min_gap = HOST_GAP.get(host, MIN_GAP)
    with _host_lock:
        gap = time.time() - _last_hit.get(host, 0.0)
        if gap < min_gap:
            time.sleep(min_gap - gap)
        _last_hit[host] = time.time()


def fetch(url: str, *, data: bytes | None = None, headers: dict | None = None,
          method: str | None = None, retries: int = 2) -> str:
    _throttle(url)
    hdrs = {
        "User-Agent": config.USER_AGENT,
        "Accept-Encoding": "gzip",
        "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
    }
    if headers:
        hdrs.update(headers)

    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    last_err: Exception | None = None

    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=config.HTTP_TIMEOUT) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return raw.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            last_err = e
            # 403 is NOT retried: measured on Reddit, in-burst retries never
            # succeed and cost ~90s of sleep per sweep. The next scheduled cycle
            # is the cheaper retry.
            if e.code == 429:
                wait = int(e.headers.get("Retry-After", "0") or 0) or (5 * (attempt + 1))
                time.sleep(min(wait, 60))
                continue
            if 500 <= e.code < 600 and attempt < retries:
                time.sleep(2 ** attempt)
                continue
            raise
        except Exception as e:  # network flake, DNS, timeout
            last_err = e
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            raise

    if last_err:
        raise last_err
    return ""


def get_json(url: str, *, headers: dict | None = None) -> Any:
    return json.loads(fetch(url, headers=headers))


def post_json(url: str, payload: dict, *, headers: dict | None = None) -> Any:
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    body = json.dumps(payload).encode("utf-8")
    return json.loads(fetch(url, data=body, headers=hdrs, method="POST"))


def online() -> bool:
    try:
        fetch("https://api.github.com/zen", retries=0)
        return True
    except Exception:
        return False
