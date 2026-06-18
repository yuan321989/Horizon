"""GitHub trending repos — top starred repos created in the past 48h.
Sends results to Telegram via WEBHOOK_URL.

Usage:
    WEBHOOK_URL=... GITHUB_TOKEN=... uv run python scripts/trending_repos.py
"""

import json
import os
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta


# ── Config ──────────────────────────────────────────────────────────
LANGUAGES = [
    ("All", ""),
    ("Python", "python"),
    ("TypeScript", "typescript"),
    ("Rust", "rust"),
    ("Go", "go"),
    ("Swift/Kotlin", "swift,kotlin"),
]

MAX_PER_LANG = 5
MAX_TOTAL = 25
HOURS_BACK = 48

WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")


# ── Helpers ─────────────────────────────────────────────────────────

def send_telegram(text: str) -> None:
    if not WEBHOOK_URL:
        print("⚠️ WEBHOOK_URL not set, skipping send")
        return
    base_url = WEBHOOK_URL.split("?")[0]
    chat_id = WEBHOOK_URL.split("chat_id=")[-1].split("&")[0]
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }).encode("utf-8")
    req = urllib.request.Request(
        base_url, data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        result = json.loads(resp.read())
        if result.get("ok"):
            print("📨 Telegram sent OK")
        else:
            print(f"❌ Telegram error: {result.get('description', '')}")
    except Exception as e:
        print(f"❌ Telegram send failed: {e}")


def chunk_lines(text: str, max_len: int = 3900) -> list[str]:
    lines = text.split("\n")
    chunks = []
    cur = ""
    for line in lines:
        candidate = f"{cur}\n{line}" if cur else line
        if len(candidate) > max_len and cur:
            chunks.append(cur)
            cur = line
        else:
            cur = candidate
    if cur:
        chunks.append(cur)
    return chunks


def send_multipart(text: str) -> None:
    chunks = chunk_lines(text)
    for i, chunk in enumerate(chunks):
        payload = {
            "chat_id": WEBHOOK_URL.split("chat_id=")[-1].split("&")[0],
            "text": chunk,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        if i > 0:
            payload["disable_notification"] = True
        base_url = WEBHOOK_URL.split("?")[0]
        req = urllib.request.Request(
            base_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            resp = urllib.request.urlopen(req, timeout=15)
            print(f"  ✅ Chunk {i+1}/{len(chunks)} sent")
        except Exception as e:
            print(f"  ❌ Chunk {i+1}/{len(chunks)}: {e}")


def fetch_trending(language: str, since: str) -> list[dict]:
    """Fetch top repos created in time window via GitHub Search API."""
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "Horizon-Trending/1.0",
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"

    query_parts = [f"created:>={since}"]
    if language:
        query_parts.append(f"language:{language}")
    query = "+".join(query_parts)
    url = (
        f"https://api.github.com/search/repositories"
        f"?q={urllib.parse.quote(query)}"
        f"&sort=stars&order=desc&per_page={MAX_PER_LANG}"
    )

    try:
        req = urllib.request.Request(url, headers=headers)
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"  ⚠️ GitHub API error ({language}): {e.code} {e.reason}")
        return []
    except Exception as e:
        print(f"  ⚠️ GitHub fetch error ({language}): {e}")
        return []

    repos = []
    for r in data.get("items", []):
        repos.append({
            "name": r["full_name"],
            "url": r["html_url"],
            "stars": r["stargazers_count"],
            "forks": r["forks_count"],
            "description": (r.get("description") or "")[:120],
            "lang": r.get("language") or "unknown",
            "owner_avatar": r.get("owner", {}).get("avatar_url", ""),
        })
    return repos


# ── Main ────────────────────────────────────────────────────────────

def format_number(n: int) -> str:
    if n >= 1000:
        return f"{n/1000:.1f}k"
    return str(n)


def main():
    since = (datetime.now(timezone.utc) - timedelta(hours=HOURS_BACK)).strftime("%Y-%m-%d")
    print(f"⭐ GitHub Trending (since {since})...")

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    all_repos = []

    for label, lang in LANGUAGES:
        print(f"  Fetching {label}...")
        repos = fetch_trending(lang, since)
        print(f"    → {len(repos)} repos")
        all_repos.append((label, repos))

    # Build message
    lines = [f"# ⭐ GitHub 热门仓库 - {today}"]
    lines.append(f"> 近 48h 新增，按 Star 排序\n")

    total = 0
    for label, repos in all_repos:
        if not repos:
            continue
        emoji = "🔥"
        lines.append(f"\n## {emoji} {label}\n")
        for r in repos:
            desc = f" — {r['description']}" if r['description'] else ""
            lines.append(
                f"- **[{r['name']}]({r['url']})** "
                f"⭐{format_number(r['stars'])} 🍴{format_number(r['forks'])}"
                f"{desc}"
            )
            total += 1
            if total >= MAX_TOTAL:
                break
        if total >= MAX_TOTAL:
            break

    if total == 0:
        lines.append("\n_(暂无数据)_")

    text = "\n".join(lines)
    print(f"\n📝 Message: {len(text)} chars, {total} repos")

    send_multipart(text)


if __name__ == "__main__":
    main()
