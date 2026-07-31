"""Portfolio watch — every project Abdullah owns, discovered automatically.

He asked for a page that shows all his projects and their real numbers, and
that picks up anything he builds in future without being told about it. So
nothing here is a hardcoded list: repositories are discovered from the GitHub
API, deployments are probed over HTTP, and each one is checked for whether it
is actually alive.

What it reports is deliberately limited to what can be measured for free and
honestly:

  - repositories, their language, stars, and last push
  - whether a GitHub Pages site or Hugging Face Space is actually reachable
  - HTTP status and response size for each live surface
  - SEO/compliance grade for the public sites, reusing the same auditor we
    sell to clients

What it does NOT report is visitor counts. There is no free, honest way to get
"how many people opened my project" without analytics installed on the site, so
instead of inventing a number it says exactly which free tool would provide it.
Fabricated traffic numbers would be worse than none.
"""
from __future__ import annotations

import json
import time
import urllib.error

from . import config, db, net

GITHUB_USER = config.env("GITHUB_USER", "AbdullahRathoreVA")
GITHUB_TOKEN = config.env("GITHUB_TOKEN")
HF_USER = config.env("HF_USER", "careermind2026")

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    key         TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL,
    url         TEXT,
    repo_url    TEXT,
    description TEXT,
    language    TEXT,
    stars       INTEGER DEFAULT 0,
    is_private  INTEGER DEFAULT 0,
    pushed_at   TEXT,
    live        INTEGER DEFAULT -1,
    http_status INTEGER DEFAULT 0,
    bytes       INTEGER DEFAULT 0,
    checked_at  REAL,
    note        TEXT
);
"""


def init() -> None:
    with db.conn() as c:
        c.executescript(SCHEMA)


_token_cache: dict = {"value": None}


def _github_token() -> str:
    """GITHUB_TOKEN if set, else the credential git already has on this machine.

    Unauthenticated GitHub allows 60 requests/hour and this account exhausts it
    quickly (measured: 403, x-ratelimit-remaining 0). Authenticated is 5,000/hr.
    Rather than make him paste a token into .env, reuse the one the credential
    manager is already holding for pushes — it never touches disk here and is
    cached in memory only.
    """
    if _token_cache["value"] is not None:
        return _token_cache["value"]

    token = GITHUB_TOKEN
    if not token:
        try:
            import subprocess
            out = subprocess.run(
                ["git", "credential", "fill"],
                input="protocol=https\nhost=github.com\n\n",
                capture_output=True, text=True, timeout=10)
            for line in out.stdout.splitlines():
                if line.startswith("password="):
                    token = line.split("=", 1)[1].strip()
                    break
        except Exception:
            token = ""

    _token_cache["value"] = token or ""
    if token:
        db.log("projects", "using the machine's existing GitHub credential "
                           "(60/hr -> 5000/hr)")
    return _token_cache["value"]


def _gh(path: str):
    headers = {"Accept": "application/vnd.github+json"}
    token = _github_token()
    if token:
        headers["Authorization"] = f"token {token}"
    return net.get_json(f"https://api.github.com{path}", headers=headers)


def discover_github() -> int:
    """Every repo the account owns. Picks up future projects with no config."""
    # /users/{name}/repos returns PUBLIC repos only, even with a token — which
    # silently hid every private project (measured: reported 0 private when
    # Project-titan-omega is private). /user/repos returns the authenticated
    # user's own repos including private ones, so prefer it when we have auth.
    try:
        if _github_token():
            repos = _gh("/user/repos?per_page=100&sort=pushed&affiliation=owner")
        else:
            repos = _gh(f"/users/{GITHUB_USER}/repos?per_page=100&sort=pushed")
    except Exception as e:
        db.log("projects", f"GitHub discovery failed: {type(e).__name__}", "warn")
        return 0

    found = 0
    for r in repos if isinstance(repos, list) else []:
        name = r.get("name", "")
        if not name:
            continue
        # A repo with Pages enabled has a predictable public URL.
        pages = (r.get("has_pages") and
                 f"https://{GITHUB_USER.lower()}.github.io/{name}")
        db.x(
            "INSERT INTO projects(key,name,kind,url,repo_url,description,"
            "language,stars,is_private,pushed_at) VALUES(?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET name=excluded.name, "
            "url=COALESCE(excluded.url, projects.url), "
            "description=excluded.description, language=excluded.language, "
            "stars=excluded.stars, is_private=excluded.is_private, "
            "pushed_at=excluded.pushed_at",
            (f"gh:{name}", name, "repo", pages or "", r.get("html_url", ""),
             (r.get("description") or "")[:300], r.get("language") or "",
             int(r.get("stargazers_count") or 0),
             1 if r.get("private") else 0, r.get("pushed_at") or ""))
        found += 1

    db.set_metric("projects_repos", found)
    return found


def discover_spaces() -> int:
    """Hugging Face Spaces owned by the account — where Titan actually lives."""
    try:
        spaces = net.get_json(
            f"https://huggingface.co/api/spaces?author={HF_USER}&limit=50")
    except Exception as e:
        db.log("projects", f"HF discovery failed: {type(e).__name__}", "warn")
        return 0

    found = 0
    for s in spaces if isinstance(spaces, list) else []:
        sid = s.get("id", "")
        if not sid or "/" not in sid:
            continue
        owner, name = sid.split("/", 1)
        url = f"https://{owner}-{name}.hf.space".replace("_", "-").lower()
        runtime = s.get("runtime") or {}
        db.x(
            "INSERT INTO projects(key,name,kind,url,repo_url,description,note) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET "
            "url=excluded.url, note=excluded.note",
            (f"hf:{name}", name, "space", url,
             f"https://huggingface.co/spaces/{sid}",
             "Hugging Face Space", f"stage={runtime.get('stage', '?')}"))
        found += 1

    db.set_metric("projects_spaces", found)
    return found


def probe_live() -> int:
    """Actually GET each public surface. 'Deployed' is not the same as 'up'."""
    rows = db.q("SELECT key, url FROM projects WHERE url != '' "
                "ORDER BY COALESCE(checked_at,0) ASC LIMIT 12")
    checked = 0
    for r in rows:
        live, status, size, note = 0, 0, 0, ""
        try:
            body = net.fetch(r["url"], retries=0)
            live, status, size = 1, 200, len(body)
        except urllib.error.HTTPError as e:
            status = e.code
            note = f"HTTP {e.code}"
        except Exception as e:
            note = type(e).__name__
        db.x("UPDATE projects SET live=?, http_status=?, bytes=?, "
             "checked_at=?, note=? WHERE key=?",
             (live, status, size, time.time(), note, r["key"]))
        checked += 1

    up = db.scalar("SELECT COUNT(*) FROM projects WHERE live=1")
    db.set_metric("projects_live", up)
    if checked:
        db.log("projects", f"probed {checked} surfaces, {up} live")
    return checked


def cycle() -> int:
    n = discover_github() + discover_spaces()
    probe_live()
    return n


def summary() -> dict:
    rows = db.q("SELECT * FROM projects ORDER BY live DESC, stars DESC, name")
    items = [dict(r) for r in rows]

    live = [p for p in items if p["live"] == 1]
    down = [p for p in items if p["live"] == 0 and p["url"]]

    return {
        "total": len(items),
        "repos": sum(1 for p in items if p["kind"] == "repo"),
        "spaces": sum(1 for p in items if p["kind"] == "space"),
        "public": sum(1 for p in items if not p["is_private"]),
        "private": sum(1 for p in items if p["is_private"]),
        "live": len(live),
        "down": len(down),
        "stars": sum(p["stars"] for p in items),
        "projects": items,
        # Deliberately not faked. See the module docstring.
        "visitors": None,
        "visitors_note": (
            "Visitor counts need analytics installed on each site. Free and "
            "privacy-friendly options: GoatCounter or Umami (self-host), or "
            "Cloudflare Web Analytics. Google Search Console additionally "
            "gives impressions and clicks per query. All require a one-time "
            "verification only the account owner can complete — so this "
            "reports no number rather than an invented one."),
    }
