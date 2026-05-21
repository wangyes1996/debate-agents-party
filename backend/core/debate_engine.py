"""Debate orchestrator - coordinates moderator + multiple agents over rounds.

Uses asyncio.Queue to publish events to the WebSocket layer.
Supports user interjection mid-debate (5A from product spec).
"""
from __future__ import annotations
import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import AsyncIterator

from .llm import chat
from .config_store import load_config
from ..agents.personas import PERSONAS
from ..api.market import fetch_market, format_market_summary


@dataclass
class DebateMessage:
    id: str
    role: str  # moderator | bull | bear | tech | news | risk | user | system
    name: str
    emoji: str
    color: str
    content: str
    round: int
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "role": self.role,
            "name": self.name,
            "emoji": self.emoji,
            "color": self.color,
            "content": self.content,
            "round": self.round,
            "ts": self.ts,
        }


def _persona(role: str) -> dict:
    return PERSONAS.get(role, {"name": role, "emoji": "❓", "color": "#999", "system": ""})


def _build_msg(role: str, content: str, rnd: int) -> DebateMessage:
    p = _persona(role)
    return DebateMessage(
        id=str(uuid.uuid4()),
        role=role,
        name=p.get("name", role),
        emoji=p.get("emoji", "❓"),
        color=p.get("color", "#888"),
        content=content,
        round=rnd,
    )


class DebateEngine:
    """One instance per active debate room."""

    def __init__(self, topic: str, queue: asyncio.Queue):
        self.topic = topic
        self.queue = queue
        self.history: list[DebateMessage] = []
        self.user_interjections: list[str] = []  # pending user messages
        self.cancelled = False
        cfg = load_config()
        self.roles: list[str] = cfg["agents"].get("enabled_roles", ["bull", "bear", "risk"])
        self.max_rounds: int = int(cfg["agents"].get("max_rounds", 3))
        self.data_cfg = cfg.get("data_source", {})

    async def _emit(self, msg: DebateMessage):
        self.history.append(msg)
        await self.queue.put({"type": "message", "data": msg.to_dict()})

    async def _emit_status(self, text: str):
        await self.queue.put({"type": "status", "data": {"text": text, "ts": time.time()}})

    def add_user_message(self, text: str):
        msg = _build_msg("user", text, rnd=-1)
        self.history.append(msg)
        self.user_interjections.append(text)
        # also broadcast immediately so all clients see it
        asyncio.create_task(self.queue.put({"type": "message", "data": msg.to_dict()}))

    def _build_context(self, last_n: int = 12) -> str:
        recent = self.history[-last_n:]
        lines = []
        for m in recent:
            tag = f"[{m.name}]" if m.role != "user" else "[👤 用户]"
            lines.append(f"{tag} {m.content}")
        return "\n".join(lines)

    async def _agent_speak(self, role: str, market_summary: str, rnd: int):
        if self.cancelled:
            return
        p = _persona(role)
        ctx = self._build_context()
        user_note = ""
        if self.user_interjections:
            interj = "\n".join(f"- {t}" for t in self.user_interjections)
            user_note = f"\n\n【用户刚刚的发言,请认真考虑】\n{interj}"
        messages = [
            {"role": "system", "content": p["system"]},
            {
                "role": "user",
                "content": (
                    f"辩论议题:{self.topic}\n\n"
                    f"【最新市场数据】\n{market_summary}\n\n"
                    f"【目前为止的辩论记录】\n{ctx or '(刚开始)'}"
                    f"{user_note}\n\n"
                    f"现在轮到你({p['name']})发言。这是第 {rnd}/{self.max_rounds} 轮。"
                ),
            },
        ]
        try:
            content = await chat(messages, temperature=0.8)
        except Exception as e:
            content = f"(发言失败:{e})"
        await self._emit(_build_msg(role, content.strip(), rnd))

    async def _moderator_intro(self, market_summary: str):
        roles_intro = ", ".join(_persona(r)["emoji"] + _persona(r)["name"] for r in self.roles)
        intro = (
            f"📣 欢迎来到加密辩论室!今天的议题是:**{self.topic}**\n"
            f"参与者:{roles_intro}\n"
            f"轮次:{self.max_rounds} 轮 | 数据源:{self.data_cfg.get('primary')}\n\n"
            f"📊 当前市场快照:{market_summary}\n\n"
            f"请各位分析师依次发言。第 1 轮开始 ⚔️"
        )
        await self._emit(_build_msg("moderator", intro, 0))

    async def _moderator_summarize(self, rnd: int):
        ctx = self._build_context(last_n=20)
        messages = [
            {"role": "system", "content": _persona("moderator")["system"]},
            {
                "role": "user",
                "content": (
                    f"以下是第 {rnd} 轮的发言记录,请做 2-3 句中立小结,"
                    f"并提出下一轮的焦点问题。如果有用户发言务必关注。\n\n{ctx}"
                ),
            },
        ]
        try:
            content = await chat(messages, temperature=0.5)
        except Exception as e:
            content = f"(主持人总结失败:{e})"
        await self._emit(_build_msg("moderator", content.strip(), rnd))
        # clear interjections that have been consumed by the moderator
        self.user_interjections.clear()

    async def _final_verdict(self):
        ctx = self._build_context(last_n=40)
        messages = [
            {"role": "system", "content": _persona("moderator")["system"]},
            {
                "role": "user",
                "content": (
                    f"辩论已结束。议题:{self.topic}\n\n"
                    f"完整记录:\n{ctx}\n\n"
                    f"请综合所有观点给出最终决议,严格按以下格式:\n"
                    f"**最终决议**: <Buy / Hold / Sell>\n"
                    f"**理由**: 2-3 句\n"
                    f"**关键风险**: 1-2 个最需要警惕的点\n"
                    f"**建议关注的关键 level**: 列 2-3 个具体价位"
                ),
            },
        ]
        try:
            content = await chat(messages, temperature=0.4)
        except Exception as e:
            content = f"(最终决议生成失败:{e})"
        await self._emit(_build_msg("moderator", "🏁 **最终决议**\n\n" + content.strip(), self.max_rounds + 1))

    async def run(self):
        try:
            await self._emit_status("拉取市场数据...")
            market = await fetch_market(self.data_cfg.get("primary", "binance"), self.data_cfg.get("symbol", "BTCUSDT"))
            market_summary = format_market_summary(market)
            await self._emit_status(f"市场数据已获取 · {market['source']}")

            await self._moderator_intro(market_summary)

            for rnd in range(1, self.max_rounds + 1):
                if self.cancelled:
                    break
                await self._emit_status(f"第 {rnd} 轮辩论中...")
                for role in self.roles:
                    if self.cancelled:
                        break
                    await self._emit_status(f"{_persona(role)['name']} 发言中...")
                    await self._agent_speak(role, market_summary, rnd)
                    await asyncio.sleep(0.3)
                if not self.cancelled:
                    await self._emit_status(f"主持人小结第 {rnd} 轮...")
                    await self._moderator_summarize(rnd)

            if not self.cancelled:
                await self._emit_status("生成最终决议...")
                await self._final_verdict()
                await self._emit_status("辩论结束 ✅")
                await self.queue.put({"type": "done", "data": {}})
        except Exception as e:
            await self.queue.put({"type": "error", "data": {"text": str(e)}})

    def cancel(self):
        self.cancelled = True
