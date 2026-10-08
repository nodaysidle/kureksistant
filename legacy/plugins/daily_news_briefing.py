"""
Drop-in JARVIS plugin: Slovenian, AI & macOS App News.
Fetches real-time headlines from RTV SLO, 24ur, and Google News tech feeds.
"""
import html
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

PLUGIN = {
    "name": "daily_news_briefing",
    "description": (
        "Provides daily news briefings. Specialized for: "
        "1) Slovenian daily news (RTV SLO, 24ur, Delo), "
        "2) AI & LLM news (from Reddit r/LocalLLaMA, frontier models, AI releases), "
        "3) macOS apps & Mac developer news. "
        "Use this whenever the user asks for Slovenian news, AI news, or Mac app news."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "category": {
                "type": "STRING",
                "description": "Category to brief: 'slovenia' | 'ai' | 'macos' | 'all' (default: slovenia)"
            },
            "language": {
                "type": "STRING",
                "description": "Language of the summary: 'slovenian' | 'english' (default: english)"
            }
        },
        "required": []
    }
}

def _clean(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"<[^<]+?>", "", html.unescape(text)).strip()

def _fetch_rss(url: str, max_items: int = 5, source_label: str = "") -> list[dict]:
    items = []
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            root = ET.fromstring(resp.read())
            for it in root.findall(".//item")[:max_items]:
                items.append({
                    "title": _clean(it.find("title").text if it.find("title") is not None else ""),
                    "snippet": _clean(it.find("description").text if it.find("description") is not None else ""),
                    "url": it.find("link").text if it.find("link") is not None else "",
                    "source": source_label or "News",
                })
    except Exception as e:
        print(f"[NewsPlugin] ⚠️ RSS error ({url[:40]}): {e}")
    return items

def run(parameters: dict, player=None, session_memory=None) -> str:
    params = parameters or {}
    cat = (params.get("category") or "slovenia").lower().strip()
    lang = (params.get("language") or "english").lower().strip()

    if player:
        player.write_log(f"[NewsBriefing] Category: {cat} ({lang})")

    articles = []

    if cat in ("slovenia", "all"):
        articles.extend(_fetch_rss("https://www.rtvslo.si/feeds/00.xml", max_items=5, source_label="RTV SLO"))
        si_gnews = "https://news.google.com/rss/search?q=Slovenija&hl=sl&gl=SI&ceid=SI:sl"
        articles.extend(_fetch_rss(si_gnews, max_items=4, source_label="Google News SI"))

    if cat in ("ai", "all"):
        ai_q = 'AI LLM OR LocalLLaMA OR "artificial intelligence" news'
        ai_url = f"https://news.google.com/rss/search?q={urllib.parse.quote(ai_q)}&hl=en-US&gl=US&ceid=US:en"
        articles.extend(_fetch_rss(ai_url, max_items=5, source_label="AI News"))

    if cat in ("macos", "all"):
        mac_q = 'macOS apps OR "Mac app" release OR "MacRumors"'
        mac_url = f"https://news.google.com/rss/search?q={urllib.parse.quote(mac_q)}&hl=en-US&gl=US&ceid=US:en"
        articles.extend(_fetch_rss(mac_url, max_items=5, source_label="Mac News"))

    if not articles:
        return "Sir, I was unable to fetch current headlines right now. Please verify internet connectivity."

    context = "\n".join(
        f"{i}. {a['title']} [{a['source']}]\n   {a['snippet'][:140]}"
        for i, a in enumerate(articles[:10], 1)
    )

    prompt = (
        f"Here are current headlines for category '{cat}':\n\n{context}\n\n"
        f"Summarize the most important updates in 3 to 4 concise bullet points. "
        f"The user wants the briefing in {lang.upper()}. Cite source name in brackets."
    )

    # 1. Try OpenRouter if configured
    try:
        from core.llm_client import query_openrouter
        or_summary = query_openrouter(prompt)
        if or_summary:
            if player:
                player.show_content(f"NEWS: {cat.upper()}", or_summary)
            return or_summary
    except Exception:
        pass

    # 2. Try Gemini flash direct
    try:
        from google import genai
        cfg_path = Path(__file__).resolve().parent.parent / "config" / "api_keys.json"
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        client = genai.Client(api_key=cfg.get("gemini_api_key"))
        resp = client.models.generate_content(model="gemini-flash-latest", contents=prompt)
        if resp.text:
            text = resp.text.strip()
            if player:
                player.show_content(f"NEWS: {cat.upper()}", text)
            return text
    except Exception as e:
        print(f"[NewsPlugin] ⚠️ Gemini summary error: {e}")

    # 3. Clean fallback
    lines = [f"Headlines for {cat.upper()}:"]
    for i, a in enumerate(articles[:5], 1):
        lines.append(f"{i}. {a['title']} [{a['source']}]")
    out = "\n".join(lines)
    if player:
        player.show_content(f"NEWS: {cat.upper()}", out)
    return out
