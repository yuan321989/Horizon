"""Daily discovery: ArXiv papers + Hugging Face trending models.
Sends results to Telegram via WEBHOOK_URL.

Usage:
    WEBHOOK_URL=... uv run python scripts/daily_discover.py
"""

import json
import os
import re
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone


# ── Config ──────────────────────────────────────────────────────────
ARXIV_KEYWORDS = [
    # MCP / tool calling
    "MCP", "model context protocol",
    "tool calling", "tool use", "tool-using",
    "function calling",
    "tool learning",
    # LLM / 大模型
    "large language model", "LLM",
    "reasoning model", "chain-of-thought",
    # 新架构
    "transformer architecture", "attention mechanism",
    "mixture of experts", "MoE",
    "state space model", "Mamba",
    "efficient inference",
    # Agent
    "AI agent", "LLM agent",
    "multi-agent",
]
ARXIV_MAX = 10

HF_MODEL_CATEGORIES = [
    "text-generation-inference",
    "transformers",
    "automatic-speech-recognition",
]
HF_MAX_PER_CATEGORY = 3
HF_MAX_TOTAL = 10

WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")


# ── Helpers ─────────────────────────────────────────────────────────

def send_telegram(text: str) -> None:
    """Send markdown text as a Telegram message via the bot API."""
    if not WEBHOOK_URL:
        print("⚠️ WEBHOOK_URL not set, skipping send")
        return
    base_url = WEBHOOK_URL.split("?")[0]
    chat_id = WEBHOOK_URL.split("chat_id=")[-1].split("&")[0]
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
    }).encode("utf-8")
    req = urllib.request.Request(
        base_url,
        data=payload,
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
    """Split text into ~max_len chunks at line breaks."""
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


def send_multipart(text: str, first_caption: str = "") -> None:
    """Send text as one or more Telegram messages, chunked at line breaks."""
    chunks = chunk_lines(text)
    for i, chunk in enumerate(chunks):
        if i == 0 and first_caption:
            chunk = f"{first_caption}\n\n{chunk}"
        send_telegram(chunk)


def strip_html(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html).strip()


# ── ArXiv ───────────────────────────────────────────────────────────

def fetch_arxiv() -> list[dict]:
    """Search ArXiv for recent papers matching keywords."""
    query = "+OR+".join(
        f'all:"{kw}"' for kw in ARXIV_KEYWORDS
    )
    params = urllib.parse.urlencode({
        "search_query": query,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": ARXIV_MAX,
    })
    url = f"http://export.arxiv.org/api/query?{params}"
    print(f"📄 ArXiv query: {url[:120]}...")

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Horizon/1.0"})
        resp = urllib.request.urlopen(req, timeout=20)
        body = resp.read().decode("utf-8")
    except Exception as e:
        print(f"⚠️ ArXiv fetch error: {e}")
        return []

    papers = []
    # Simple XML parsing — entry blocks
    entries = re.split(r"</?entry[^>]*>", body)
    for entry in entries:
        entry = entry.strip()
        if not entry or entry.startswith("<?xml"):
            continue
        title_match = re.search(r"<title[^>]*>(.*?)</title>", entry, re.DOTALL)
        summary_match = re.search(r"<summary[^>]*>(.*?)</summary>", entry, re.DOTALL)
        id_match = re.search(r"<id[^>]*>(.*?)</id>", entry, re.DOTALL)
        author_matches = re.findall(r"<author[^>]*>.*?<name[^>]*>(.*?)</name>", entry, re.DOTALL)

        title = strip_html(title_match.group(1) if title_match else "")
        summary = strip_html(summary_match.group(1) if summary_match else "")
        paper_id = id_match.group(1).strip() if id_match else ""
        authors = [strip_html(a) for a in author_matches[:3]]
        author_str = ", ".join(authors) + ("…" if len(author_matches) > 3 else "")

        if not title:
            continue

        papers.append({
            "title": title,
            "summary": summary[:300] + ("…" if len(summary) > 300 else ""),
            "url": paper_id,
            "authors": author_str,
        })

    return papers


# ── Hugging Face ────────────────────────────────────────────────────

def fetch_hf_models() -> list[dict]:
    """Fetch trending models from Hugging Face."""
    models = []
    seen_ids = set()

    for category in HF_MODEL_CATEGORIES:
        url = (
            f"https://huggingface.co/api/models"
            f"?sort=trendingScore&direction=-1"
            f"&limit={HF_MAX_PER_CATEGORY}"
            f"&search={urllib.parse.quote(category)}"
        )
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Horizon/1.0"})
            resp = urllib.request.urlopen(req, timeout=15)
            data = json.loads(resp.read())
        except Exception as e:
            print(f"⚠️ HF {category} error: {e}")
            continue

        for m in data:
            model_id = m.get("id", "")
            if model_id in seen_ids:
                continue
            seen_ids.add(model_id)
            models.append({
                "id": model_id,
                "likes": m.get("likes", 0),
                "downloads": m.get("downloads", 0),
                "tags": m.get("pipeline_tag", ""),
            })
            if len(models) >= HF_MAX_TOTAL:
                break
        if len(models) >= HF_MAX_TOTAL:
            break

    return models


# ── Main ────────────────────────────────────────────────────────────

def build_message(arxiv_papers: list[dict], hf_models: list[dict]) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [f"# 🔬 今日发现 - {today}\n"]

    if arxiv_papers:
        lines.append("## 📄 ArXiv 论文\n")
        for i, p in enumerate(arxiv_papers, 1):
            lines.append(f"**{i}. [{p['title']}]({p['url']})**")
            lines.append(f"   *{p['authors']}*")
            lines.append(f"   {p['summary']}\n")

    if hf_models:
        lines.append("---\n## 🤗 Hugging Face 热门模型\n")
        for m in hf_models:
            lines.append(
                f"- **[{m['id']}](https://huggingface.com/{m['id']})**"
                f"  ❤️{m['likes']} ⬇️{m['downloads']:,}"
                f"  `{m['tags']}`"
            )

    if not arxiv_papers and not hf_models:
        lines.append("今日暂无新发现。")

    return "\n".join(lines)


def main():
    print("🔬 Daily Discovery — fetching…")

    arxiv_papers = fetch_arxiv()
    print(f"  ArXiv: {len(arxiv_papers)} papers")

    hf_models = fetch_hf_models()
    print(f"  HF: {len(hf_models)} models")

    text = build_message(arxiv_papers, hf_models)
    print(f"\n--- message ({len(text)} chars) ---")
    print(text[:500])
    print("...")

    send_multipart(text, "🌤 午间加急：今日 MCP / 工具调用相关发现")


if __name__ == "__main__":
    main()
