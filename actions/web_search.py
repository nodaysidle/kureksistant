"""
actions/web_search.py — Live web search & synthesis for Kurek.
Auto-discovered by core/action_loader.py.

Uses DuckDuckGo (ddgs) & Google News RSS with Gemini / DeepSeek synthesis.
Automatically resolves locale (Slovenia / CEST) for timing & local inquiries.
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = _get_base_dir()
USER_LOCALE_CONTEXT = "User Location: Slovenia (Central European Time / CEST, UTC+2)."


def _clean_html(t: str) -> str:
    if not t:
        return ""
    return re.sub(r"<[^<]+?>", "", html.unescape(t)).strip()


def _get_api_key() -> str:
    from memory.config_manager import get_gemini_key, load_api_keys
    key = get_gemini_key()
    if key:
        return key
    keys = load_api_keys()
    return keys.get("gemini_api_key") or keys.get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY", "")


def _clean_query(raw_query: str) -> str:
    """Strip conversational filler words so search engines get precise keywords."""
    q = raw_query.strip()
    # Replace locale phrases with Slovenia / CEST context
    q = re.sub(r"(?i)\b(in my locale|my locale|in my timezone|my timezone|here)\b", "Slovenia CEST Central European Time", q)
    # Remove question prefixes
    q = re.sub(r"(?i)^(when does|can you tell me|can you find|what is|tell me|find out|search for|google)\s+", "", q)
    return q.strip()


def _ddg_text_search(query: str, max_results: int = 6) -> list[dict]:
    """Queries live DuckDuckGo text search."""
    results = []
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            DDGS = None

    if DDGS:
        try:
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=max_results):
                    results.append({
                        "title": r.get("title", ""),
                        "snippet": r.get("body", ""),
                        "url": r.get("href", ""),
                        "source": "Web",
                    })
        except Exception as e:
            print(f"[WebSearch] DDGS text search error: {e}")

    return results


def _ddg_news_search(query: str, max_results: int = 5) -> list[dict]:
    """Queries live DuckDuckGo news search."""
    results = []
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            DDGS = None

    if DDGS:
        try:
            with DDGS() as ddgs:
                for r in ddgs.news(query, max_results=max_results):
                    results.append({
                        "title": r.get("title", ""),
                        "snippet": r.get("body", ""),
                        "url": r.get("url", ""),
                        "source": r.get("source", "News"),
                    })
        except Exception as e:
            print(f"[WebSearch] DDGS news search error: {e}")

    return results


def _fetch_rss_news(query: str, max_results: int = 5) -> list[dict]:
    """Fetch live news from Google News RSS as fallback."""
    results = []
    clean_q = _clean_query(query) or "world news"
    url = f"https://news.google.com/rss/search?q={urllib.parse.quote(clean_q)}&hl=en-US&gl=US&ceid=US:en"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            root = ET.fromstring(resp.read())
            for it in root.findall(".//item")[:max_results]:
                title = _clean_html(it.find("title").text if it.find("title") is not None else "")
                link = it.find("link").text if it.find("link") is not None else ""
                pub = it.find("pubDate").text if it.find("pubDate") is not None else ""
                if title:
                    results.append({
                        "title": title,
                        "snippet": f"Published: {pub}",
                        "url": link,
                        "source": "Google News",
                    })
    except Exception as e:
        print(f"[WebSearch] RSS error: {e}")

    return results


def _format_results(query: str, snippets: list[dict]) -> str:
    """Formats real web snippets into clear, grounded context for the AI assistant."""
    if not snippets:
        return f"No live search results found for '{query}'."

    lines = [f"Web search results for '{query}':"]
    for i, s in enumerate(snippets[:6], 1):
        title = s.get("title", "").strip()
        body = s.get("snippet", "").strip()
        src = s.get("source", "Web")
        url = s.get("url", "").strip()
        if title or body:
            entry = f"{i}. [{src}] {title}"
            if body:
                entry += f"\n   {body}"
            if url:
                entry += f"\n   Source: {url}"
            lines.append(entry)

    return "\n\n".join(lines)


def web_search(parameters: dict, player=None, session_memory=None) -> str:
    """Main tool handler for web searches."""
    params = parameters or {}
    query = params.get("query", "").strip()
    if not query:
        return "Please specify what you would like to search for."

    print(f"[WebSearch] 🌐 Searching web for: '{query}'", flush=True)

    cleaned = _clean_query(query)

    # 1. Primary: DuckDuckGo text search with cleaned query
    results = _ddg_text_search(cleaned, max_results=6)

    # 2. Secondary: If few results, try original query or news search
    if len(results) < 2 and cleaned != query:
        extra = _ddg_text_search(query, max_results=4)
        results.extend(extra)

    if len(results) < 2:
        news_results = _ddg_news_search(cleaned, max_results=4)
        results.extend(news_results)

    # 3. Third fallback: Google News RSS
    if not results:
        results = _fetch_rss_news(query, max_results=6)

    return _format_results(query, results)


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "web_search",
    "description": (
        "Searches the live web and news in real time. Use this whenever the user asks about current events, "
        "sports schedules (MotoGP, F1, football), start times, news, real-time facts, or anything requiring live internet data."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "query": {
                "type": "STRING",
                "description": "Search query keywords (e.g. 'motogp schedule today start time', 'current weather in Maribor', 'latest tech news')."
            }
        },
        "required": [
            "query"
        ]
    },
    "handler": web_search
}
