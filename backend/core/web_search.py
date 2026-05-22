"""Web search via DuckDuckGo (ddgs package) — free, no API key, no quota.

Two helpers:
- `decide_queries(...)`: ask the agent's own LLM whether/what to search.
- `search(...)`: run DuckDuckGo text search (offloaded to a thread, ddgs is sync).

Both return cheap/graceful fallbacks on error so a flaky network never kills a turn.
"""
from __future__ import annotations
import asyncio
import json
import re
import time
from functools import lru_cache

from .llm import chat

# Tiny in-memory cache: query → (ts, results). 10-minute TTL.
_CACHE: dict[str, tuple[float, list[dict]]] = {}
_CACHE_TTL = 600.0


async def _ddg_search_sync(query: str, max_results: int = 4) -> list[dict]:
    """Run ddgs in a thread (it's a sync library)."""
    def _run():
        try:
            from ddgs import DDGS
            with DDGS() as d:
                return list(d.text(query, max_results=max_results, region="wt-wt"))
        except Exception as e:
            return [{"_error": str(e)}]
    return await asyncio.to_thread(_run)


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
    results = await _ddg_search_sync(query, max_results=max_results)
    # Filter errors out of cache but still return them once
    clean = [r for r in results if "_error" not in r]
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
    # Be forgiving: pull first {...} block
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
    lines = ["", "【🌐 你刚刚的联网搜索结果(可作为发言依据,但要批判性使用)】"]
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
