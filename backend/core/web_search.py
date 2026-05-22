"""Web search via Bing HTML scraping — free, no API key, no quota.

ddgs package proved unreliable (DuckDuckGo returns 202 anti-bot, fallback
backends route to unreachable domains). Bing's public search page works fine
with a plain httpx GET + regex parsing, so we hit it directly.

Public API:
- `decide_queries(...)`: ask the agent's own LLM whether/what to search.
- `search(...)`: run Bing text search (async via httpx).
- `format_results_for_prompt(...)`: render results as a markdown block.

All paths swallow exceptions and return graceful empties so a flaky network
never kills a debate turn.
"""
from __future__ import annotations
import asyncio
import html as _html
import json
import re
import time
from urllib.parse import quote_plus, unquote

import httpx

from .llm import chat

# Tiny in-memory cache: query → (ts, results). 10-minute TTL.
_CACHE: dict[str, tuple[float, list[dict]]] = {}
_CACHE_TTL = 600.0

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Bing result block: <li class="b_algo">...<h2><a href="URL">TITLE</a></h2>...<p>SNIPPET</p>...
_BLOCK_RE = re.compile(r'<li class="b_algo".*?</li>', re.DOTALL)
_TITLE_RE = re.compile(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>\s*</h2>', re.DOTALL)
# Bing wraps real URLs in /ck/a?...&u=a1<base64>&...  — sometimes
_BING_REDIR_RE = re.compile(r'/ck/a\?.*?&u=a1([^&]+)')
# Snippet candidates (Bing markup varies)
_SNIPPET_RES = [
    re.compile(r'<p class="b_lineclamp[^"]*"[^>]*>(.*?)</p>', re.DOTALL),
    re.compile(r'<p class="b_paractl[^"]*"[^>]*>(.*?)</p>', re.DOTALL),
    re.compile(r'<div class="b_caption"[^>]*>.*?<p[^>]*>(.*?)</p>', re.DOTALL),
    re.compile(r'<p[^>]*>(.*?)</p>', re.DOTALL),
]
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(s: str) -> str:
    return _html.unescape(_TAG_RE.sub("", s or "")).strip()


def _decode_bing_url(href: str) -> str:
    """Bing sometimes wraps result URLs in /ck/a?u=a1<b64>. Unwrap when present."""
    if not href:
        return ""
    href = _html.unescape(href)  # &amp; -> &
    m = _BING_REDIR_RE.search(href)
    if not m:
        return href
    import base64
    raw = m.group(1)
    # Bing's b64 may be padded oddly; try a few variants
    for candidate in (raw, raw + "=", raw + "==", raw + "==="):
        try:
            decoded = base64.urlsafe_b64decode(candidate).decode("utf-8", "ignore")
            if decoded.startswith("http"):
                return decoded
        except Exception:
            continue
    return href


def _parse_bing(html: str, max_results: int) -> list[dict]:
    out: list[dict] = []
    for block in _BLOCK_RE.findall(html):
        tm = _TITLE_RE.search(block)
        if not tm:
            continue
        href = _decode_bing_url(tm.group(1))
        title = _strip_tags(tm.group(2))
        snippet = ""
        for rx in _SNIPPET_RES:
            sm = rx.search(block)
            if sm:
                snippet = _strip_tags(sm.group(1))
                if snippet:
                    break
        if not title or not href:
            continue
        out.append({"title": title, "href": href, "body": snippet})
        if len(out) >= max_results:
            break
    return out


async def _bing_search(query: str, max_results: int = 4) -> list[dict]:
    """Hit Bing HTML and parse results. Returns [] on any failure."""
    url = f"https://www.bing.com/search?q={quote_plus(query)}&count={max(max_results, 10)}"
    headers = {
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
    }
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as c:
            r = await c.get(url, headers=headers)
        if r.status_code != 200:
            return [{"_error": f"bing http {r.status_code}"}]
        return _parse_bing(r.text, max_results)
    except Exception as e:
        return [{"_error": f"{type(e).__name__}: {str(e)[:120]}"}]


async def search(query: str, max_results: int = 4) -> list[dict]:
    """Cached text search. Returns list of {title, href, body}."""
    query = (query or "").strip()
    if not query:
        return []
    key = f"{query}|{max_results}"
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    results = await _bing_search(query, max_results=max_results)
    # Cache only clean results; still return errors once for diagnosis
    clean = [r for r in results if "_error" not in r]
    if clean:
        _CACHE[key] = (now, clean)
    return results


_JSON_RE = re.compile(r"\{[^{}]*\}", re.DOTALL)


async def decide_queries(
    agent_name: str,
    agent_system: str,
    topic: str,
    moderator_question: str,
    transcript_tail: str,
    llm_id: str | None = None,
) -> list[str]:
    """Tiny LLM call: ask the agent if it wants to web-search, and what for.

    Returns a list of 0-2 short queries. Never raises — returns [] on failure.
    """
    prompt = (
        f"你扮演辩论参与者「{agent_name}」。下一步你要发言。\n"
        f"辩论议题:{topic}\n"
        f"主持人刚刚的问题:{moderator_question or '(暂无具体问题)'}\n\n"
        f"【最近发言节选】\n{transcript_tail[-1200:]}\n\n"
        f"在发言之前,你可以选择联网搜索 0-2 条简短查询(英文或中文皆可),用来:\n"
        f"- 核实事实/数据/最新事件\n"
        f"- 找具体案例或权威观点支撑你的角色立场\n"
        f"如果不需要搜索(纯观点辩论、议题不依赖事实)就返回空列表。\n\n"
        f"严格只输出一个 JSON 对象,形如:\n"
        f'{{\"queries\": [\"...\", \"...\"]}}\n'
        f"queries 最多 2 条,每条 ≤ 10 个词,要具体可检索。不要解释,不要 markdown。"
    )
    try:
        raw = await chat(
            [{"role": "user", "content": prompt}],
            llm_id=llm_id,
            temperature=0.2,
        )
    except Exception:
        return []
    m = _JSON_RE.search(raw or "")
    if not m:
        return []
    try:
        obj = json.loads(m.group(0))
        qs = obj.get("queries") or []
        out = []
        for q in qs:
            if isinstance(q, str):
                q = q.strip()
                if q and len(q) < 200:
                    out.append(q)
        return out[:2]
    except Exception:
        return []


def format_results_for_prompt(query_to_results: dict[str, list[dict]]) -> str:
    """Turn {query: [hits]} into a compact markdown block for the system prompt."""
    if not query_to_results:
        return ""
    lines = [
        "",
        "【🌐 你刚刚通过 Bing 联网搜索的结果(可作为发言依据,要批判性使用)】",
        "你**已经联网获取了以下实时信息**,请基于它发言,不要再说「无法联网」之类的话。",
    ]
    for q, hits in query_to_results.items():
        lines.append(f"\n▸ 搜索:「{q}」")
        if not hits:
            lines.append("  (无结果)")
            continue
        for h in hits[:4]:
            if "_error" in h:
                lines.append(f"  (搜索失败: {h['_error']})")
                continue
            title = (h.get("title") or "").strip()
            body = (h.get("body") or "").strip().replace("\n", " ")
            href = (h.get("href") or "").strip()
            if len(body) > 240:
                body = body[:240] + "…"
            lines.append(f"  • {title} — {body} ({href})")
    lines.append("")
    return "\n".join(lines)
