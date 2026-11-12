"""The product profile: one JSON file that points the engine at any product.

Everything niche-specific lives in `aether.json` in the workspace: what is being
sold, who it is for, which phrases signal a buyer, where to listen, and how
pages are framed. The engine itself carries no niche. With no profile the
read-only commands still run and say what is missing.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

FILENAME = "aether.json"

STARTER = {
    "product": {
        "name": "",
        "description": "",
        "audience": "",
        "price": "",
        "checkout_url": "",
    },
    "site": {
        "base_url": "",
        "dir": "site",
        "title": "",
        "lede": "",
        "author": "",
        "track_ref": True,
    },
    "intent": {
        "strong": [],
        "medium": [],
        "weak": [],
        "negative": [],
        "first_person": [],
    },
    "sources": {
        "hn_queries": [],
        "subreddits": [],
        "stackexchange": [],
        "rss": [],
    },
    "matrix": {"subjects": [], "templates": []},
    "voice": {"reply_persona": "", "page_persona": ""},
    "cta": {"title": "", "body": "", "button": ""},
    "second_offer": {},
}

_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SUBREDDIT = re.compile(r"^[A-Za-z0-9_]{2,21}$")
_SE_SITE = re.compile(r"^[a-z0-9.\-]{2,60}$")


def is_http_url(value: str) -> bool:
    return bool(re.match(r"^https?://[^\s<>\"']+$", value or ""))


def path_in(root: Path) -> Path:
    return Path(root) / FILENAME


def load(root: Path) -> dict:
    """The profile merged over the starter shape. {} sections when absent."""
    data: dict = {}
    p = path_in(root)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8-sig"))
        except (ValueError, OSError):
            data = {"_invalid": True}
        if not isinstance(data, dict):
            data = {"_invalid": True}
    out = json.loads(json.dumps(STARTER))
    for section, default in STARTER.items():
        got = data.get(section)
        if isinstance(default, dict) and isinstance(got, dict):
            out[section].update(got)
    out["_exists"] = p.exists()
    out["_invalid"] = bool(data.get("_invalid"))
    return out


def _strs(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if isinstance(v, (str, int, float))
            and str(v).strip()]


def validate(p: dict) -> dict:
    """{"errors": [...], "warnings": [...]}. Errors block writing commands."""
    errors: list[str] = []
    warnings: list[str] = []
    if not p.get("_exists"):
        return {"errors": [f"no {FILENAME} in the workspace - run `init`, then fill it in"],
                "warnings": []}
    if p.get("_invalid"):
        return {"errors": [f"{FILENAME} is not valid JSON (must be one object)"],
                "warnings": []}

    prod, site = p["product"], p["site"]
    if not str(prod.get("name", "")).strip():
        errors.append("product.name is empty")
    if not str(prod.get("audience", "")).strip():
        warnings.append("product.audience is empty - briefs and drafts will be generic")
    url = str(prod.get("checkout_url", "")).strip()
    if not url:
        warnings.append("product.checkout_url is empty - pages publish, but the "
                        "buy button goes nowhere")
    elif not is_http_url(url):
        errors.append("product.checkout_url must start with http:// or https://")
    base = str(site.get("base_url", "")).strip()
    if not base:
        warnings.append("site.base_url is empty - no sitemap, canonical URLs are relative")
    elif not is_http_url(base):
        errors.append("site.base_url must start with http:// or https://")
    d = str(site.get("dir", "site")).strip()
    if not d or Path(d).is_absolute() or ".." in Path(d).parts:
        errors.append("site.dir must be a relative folder inside the workspace")

    intent = p["intent"]
    if not (_strs(intent.get("strong")) or _strs(intent.get("medium"))):
        warnings.append("intent.strong / intent.medium are empty - `score` cannot "
                        "tell a buyer from a bystander")

    src = p["sources"]
    for s in _strs(src.get("subreddits")):
        if not _SUBREDDIT.match(s):
            errors.append(f"sources.subreddits: {s!r} is not a subreddit name")
    for s in src.get("stackexchange") or []:
        site_name = s.get("site") if isinstance(s, dict) else s
        if not _SE_SITE.match(str(site_name or "")):
            errors.append(f"sources.stackexchange: {site_name!r} is not a site name")
    for s in _strs(src.get("rss")):
        if not is_http_url(s):
            errors.append(f"sources.rss: {s!r} is not an http(s) URL")
    if not any(_strs(src.get(k)) or src.get(k) for k in
               ("hn_queries", "subreddits", "stackexchange", "rss")):
        warnings.append("sources is empty - `harvest` has nowhere to listen")

    for t in p["matrix"].get("templates") or []:
        if not isinstance(t, dict):
            errors.append("matrix.templates entries must be objects")
            continue
        slug = str(t.get("slug", ""))
        if "{subject}" not in slug or not _SLUG.match(slug.replace("{subject}", "x")):
            errors.append(f"matrix template slug {slug!r} must be lowercase-hyphen "
                          "and contain {subject}")
        if not str(t.get("title", "")).strip():
            errors.append(f"matrix template {slug!r} has no title")

    so = p.get("second_offer") or {}
    for key in ("url", "contact"):
        v = str(so.get(key, "")).strip()
        if v and not (is_http_url(v) or (key == "contact" and v.startswith("mailto:"))):
            errors.append(f"second_offer.{key} must be an http(s) URL"
                          + (" or mailto:" if key == "contact" else ""))
    return {"errors": errors, "warnings": warnings}


def write_starter(root: Path) -> Path:
    p = path_in(root)
    if p.exists():
        raise FileExistsError(f"{FILENAME} already exists")
    p.write_text(json.dumps(STARTER, indent=2) + "\n", encoding="utf-8")
    return p
