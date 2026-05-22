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
import os
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


_SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://127.0.0.1:8888").rstrip("/")


async def _searxng_search(query: str, max_results: int = 4) -> list[dict]:
    """Hit a local SearXNG JSON endpoint.

    SearXNG aggregates Google/Brave/Bing/DDG/Wikipedia etc. and gives us
    clean JSON without per-engine scraping. Primary source. Returns
    [{"_error": ...}] on connection refused so caller falls back.

    Start one locally via `scripts/run_searxng.sh` (see README). Override
    URL with $SEARXNG_URL.
    """
    url = f"{_SEARXNG_URL}/search"
    params = {"q": query, "format": "json", "safesearch": "0"}
    headers = {"User-Agent": _UA, "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get(url, params=params, headers=headers)
        if r.status_code != 200:
            return [{"_error": f"searxng http {r.status_code}"}]
        data = r.json()
        out: list[dict] = []
        for item in (data.get("results") or [])[:max_results]:
            href = item.get("url") or ""
            if not href:
                continue
            out.append(
                {
                    "title": (item.get("title") or "").strip(),
                    "href": href,
                    "body": (item.get("content") or "").strip(),
                    "_engine": item.get("engine") or "searxng",
                }
            )
        return out
    except Exception as e:
        return [{"_error": f"searxng {type(e).__name__}: {str(e)[:120]}"}]


async def _bing_search(query: str, max_results: int = 4) -> list[dict]:
    """Hit Bing HTML and parse results. Returns [] on any failure.

    Force `mkt=en-US&setlang=en-US&cc=US` — without this Bing routes by
    server IP and returns garbage (Indian doctor pages, Spanish LinkedIn
    profiles) for short Chinese/news queries.
    """
    url = (
        f"https://www.bing.com/search?q={quote_plus(query)}"
        f"&count={max(max_results, 10)}"
        f"&setmkt=en-US&setlang=en-US&cc=US&mkt=en-US"
    )
    headers = {
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as c:
            r = await c.get(url, headers=headers)
        if r.status_code != 200:
            return [{"_error": f"bing http {r.status_code}"}]
        return _parse_bing(r.text, max_results)
    except Exception as e:
        return [{"_error": f"{type(e).__name__}: {str(e)[:120]}"}]


# DuckDuckGo html result link: <a class="result__a" href="URL">TITLE</a>
_DDG_LINK_RE = re.compile(
    r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
    re.DOTALL,
)
_DDG_SNIPPET_RE = re.compile(
    r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
    re.DOTALL,
)


async def _ddg_search(query: str, max_results: int = 4) -> list[dict]:
    """DuckDuckGo HTML POST endpoint. Used as fallback when Bing is dry.

    Tends to be more permissive on news/political queries but rate-limits
    aggressively (returns 202 on repeat). Best for English queries.
    """
    url = "https://html.duckduckgo.com/html/"
    headers = {
        "User-Agent": _UA,
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://html.duckduckgo.com",
        "Referer": "https://html.duckduckgo.com/",
    }
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as c:
            r = await c.post(url, headers=headers, data={"q": query})
        if r.status_code != 200:
            return [{"_error": f"ddg http {r.status_code}"}]
        links = _DDG_LINK_RE.findall(r.text)
        snippets = _DDG_SNIPPET_RE.findall(r.text)
        out: list[dict] = []
        for i, (href, title) in enumerate(links[:max_results]):
            # DDG wraps in //duckduckgo.com/l/?uddg=<url-encoded>
            href = _html.unescape(href)
            if "uddg=" in href:
                m = re.search(r"uddg=([^&]+)", href)
                if m:
                    href = unquote(m.group(1))
            if href.startswith("//"):
                href = "https:" + href
            body = _strip_tags(snippets[i]) if i < len(snippets) else ""
            out.append({"title": _strip_tags(title), "href": href, "body": body})
        return out
    except Exception as e:
        return [{"_error": f"{type(e).__name__}: {str(e)[:120]}"}]


# Hosts that are almost always noise for news/current-event queries
# (reference encyclopedias, navigation/landing pages, generic outlet hubs).
# Per-domain blacklist — keeps deep article URLs from these sites but kills
# bare landing pages.
_NOISE_LANDING_RE = re.compile(
    r"^https?://(www\.|edition\.|en\.)?("
    r"wikipedia\.org/wiki/(China|United_States|United_States_of_America|Donald_Trump|Xi_Jinping)$"
    r"|britannica\.com/(place|topic)/[^/]+/?$"
    r"|chinadaily\.com\.cn/?$"
    r"|chinadaily\.com\.cn/(china|world|business)/?$"
    r"|cnn\.com/(world/china|us|world)/?$"
    r"|bbc\.com/news(/world(/asia(/china)?)?)?/?$"
    r"|reuters\.com/(world(/china)?|business|markets)/?$"
    r"|apnews\.com/(us-news|world-news|hub/.*)?$"
    r"|scmp\.com/news/china/?$"
    r"|ft\.com/[a-z-]+/?$"
    r"|bloomberg\.com/[a-z-]*/?$"
    r"|politico\.com/?$"
    r"|cbsnews\.com/?$"
    r"|nytimes\.com/?$"
    r"|whitehouse\.gov/?$"
    r"|usa\.gov/?$"
    r"|usagov(\.gov)?/?$"
    r"|state\.gov/?$"
    r"|usatoday\.com/?$"
    r"|usnews\.com/?$"
    r"|google\.[a-z.]+/?$"
    r"|support\.google\.com.*"
    r"|outlook\.com/?$"
    r"|sendersupport\.olc\.protection\.outlook\.com.*"
    r"|microsoft\.com/.*"
    r"|linkedin\.com/pub/dir/.*"
    r"|prezi\.com/.*"
    r"|1library\.co/.*"
    r"|corporationwiki\.com/.*"
    r"|ustraveldocs\.com/?$"
    r"|justdial\.com.*"
    r"|apollo247\.com.*"
    r"|worldatlas\.com/.*"
    r"|ontheworldmap\.com/.*"
    r"|maps\.google\.[a-z.]+/.*"
    r")",
    re.IGNORECASE,
)


def _is_noise(href: str) -> bool:
    if not href:
        return True
    if _NOISE_LANDING_RE.match(href):
        return True
    return False


async def search(query: str, max_results: int = 4) -> list[dict]:
    """Cached text search. Returns list of {title, href, body}.

    Pipeline: Bing (primary) → noise filter → DDG fallback if dry.
    """
    query = (query or "").strip()
    if not query:
        return []
    key = f"{query}|{max_results}"
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]

    # Primary: local SearXNG aggregator (Google + Brave + DDG + Wikipedia ...)
    sx = await _searxng_search(query, max_results=max_results * 2)
    clean = [r for r in sx if "_error" not in r and not _is_noise(r.get("href", ""))]

    # Fallback chain: Bing → DDG, only if SearXNG returned nothing useful
    bing: list[dict] = []
    if len(clean) < 2:
        bing = await _bing_search(query, max_results=max_results * 2)
        bing_clean = [r for r in bing if "_error" not in r and not _is_noise(r.get("href", ""))]
        seen = {r["href"] for r in clean}
        for r in bing_clean:
            if r["href"] not in seen:
                clean.append(r)
                seen.add(r["href"])

    # Fallback to DDG if Bing returned nothing useful
    if len(clean) < 2:
        ddg = await _ddg_search(query, max_results=max_results * 2)
        ddg_clean = [r for r in ddg if "_error" not in r and not _is_noise(r.get("href", ""))]
        # Merge dedup by href
        seen = {r["href"] for r in clean}
        for r in ddg_clean:
            if r["href"] not in seen:
                clean.append(r)
                seen.add(r["href"])

    clean = clean[:max_results]
    if clean:
        _CACHE[key] = (now, clean)
        return clean
    # Surface errors so the agent (and our logs) can see what happened
    errs = [r for r in (sx or []) if "_error" in r] + [r for r in (bing or []) if "_error" in r]
    return errs or []


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
    # Build a compact persona hint — the first 400 chars of agent_system
    # are usually their role definition / stance.
    persona_hint = (agent_system or "").strip()[:400]

    prompt = (
        f"你是辩论参与者「{agent_name}」,正准备发言反驳/推进观点。\n\n"
        f"【你的角色定位 / 立场】\n{persona_hint}\n\n"
        f"【辩论议题】{topic}\n"
        f"【主持人最新问题】{moderator_question or '(无)'}\n\n"
        f"【最近发言(含对手论据)】\n{transcript_tail[-1600:]}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"在发言之前,你要主动联网搜索 0-2 条 query,目的是**为你的立场找弹药、为反驳对手找硬证据**。\n"
        f"把搜索当成辩论武器,不是百科查询。\n\n"
        f"🎯 **三类高价值搜索方向**(按优先级):\n"
        f"  A. **支撑己方**:找数据/报告/案例直接印证你的立场。\n"
        f"     例:批判者面对「AI 推动生产力」议题 → `AI productivity paradox MIT study 2025`\n"
        f"  B. **反驳对手**:对手刚提出某论据/数据/案例,搜它的反例、过时性、错误。\n"
        f"     例:对手引「某公司用 AI 降本 40%」→ `<that company> AI layoffs failure 2026`\n"
        f"  C. **核实事实**:涉及具体数字、日期、价格、人物现状,必须搜确认。\n\n"
        f"⚠️ **query 硬规则**:\n"
        f"  1. **必须英文**(en-US 区域才返真新闻;中文 query 会拿到垃圾)。\n"
        f"  2. **必须具体**:加年份/月份 + 具体事件/公司/人物 + 必要时 `site:` 限定。\n"
        f"     ✅ `Trump China tariff May 2026 Reuters`\n"
        f"     ✅ `US semiconductor export ban Nvidia 2026 site:ft.com`\n"
        f"     ✅ `EV subsidy phaseout China 2026 statistics`\n"
        f"     ❌ `China US relations` / `AI productivity` / `中美贸易`(全宽泛,只返 Wikipedia)\n"
        f"  3. **角色立场要带进 query**:\n"
        f"     - 现实主义者搜「decoupling cost / structural conflict」类\n"
        f"     - 理想主义者搜「cooperation success climate agreement」类\n"
        f"     - 批判者搜「failure / collapse / contradiction」类\n"
        f"  4. **如果对手最近一轮抛了具体数据/案例,优先搜反例**,这是最高 ROI。\n\n"
        f"⚠️ **必搜场景**(出现任一即必须返回 ≥1 条 query):\n"
        f"  - 议题/问题/最近发言含「最新/今天/此刻/2025/2026/最近新闻」等时效词\n"
        f"  - 对手发言里出现具体公司名、人名、数字、日期、报告名 → 你应该搜来核实或找反例\n"
        f"  - 用户明确让你「搜一下/测试联网/找证据」\n"
        f"  - 议题本身是事实性而非纯哲学(几乎所有现实议题都是)\n"
        f"只有当议题是**100% 抽象哲学**(如「自由意志是否存在」)且对手也没扔具体数据时,才能返回空。\n\n"
        f"严格只输出一个 JSON 对象,形如:\n"
        f'{{\"queries\": [\"specific english query 1\", \"specific english query 2\"]}}\n'
        f"queries 最多 2 条,每条 4-12 个词,英文,具体可检索。不要解释,不要 markdown。"
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
        "⚠️ 重要:**只能引用下面列出的 URL/标题/摘要里实际出现的内容**。"
        "如果结果与主持人的问题明显无关(比如问 A 却返回 B),你必须明说「搜索结果与问题无关,我无法用它佐证」,**绝对不要编造**任何数字、日期、价格、人名来填补空白。",
        "⏰ 关于「现在几点 / 今天日期」:**忽略搜索结果**,只看 system 中的「服务器当前时间」块。",
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
