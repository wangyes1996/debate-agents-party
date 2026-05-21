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

from .llm import chat, chat_stream
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
        # Each pending interjection: {"id": str, "text": str, "addressed_by": set[str]}
        self.pending_user_msgs: list[dict] = []
        self.cancelled = False
        cfg = load_config()
        self.roles: list[str] = cfg["agents"].get("enabled_roles", ["bull", "bear", "risk"])
        self.max_rounds: int = int(cfg["agents"].get("max_rounds", 3))
        self.role_llm: dict = cfg["agents"].get("role_llm", {}) or {}
        self.data_cfg = cfg.get("data_source", {})

    def _provider_for(self, role: str) -> str | None:
        """Resolve which llm_id to use for a role; None = use default."""
        p = self.role_llm.get(role, "")
        return p or None

    async def _emit(self, msg: DebateMessage):
        self.history.append(msg)
        await self.queue.put({"type": "message", "data": msg.to_dict()})

    async def _emit_status(self, text: str):
        await self.queue.put({"type": "status", "data": {"text": text, "ts": time.time()}})

    def add_user_message(self, text: str):
        msg = _build_msg("user", text, rnd=-1)
        self.history.append(msg)
        # Track as pending — every agent must address it once
        pending = {"id": msg.id, "text": text, "addressed_by": set()}
        self.pending_user_msgs.append(pending)
        # Broadcast user message immediately so all clients see it
        asyncio.create_task(self.queue.put({"type": "message", "data": msg.to_dict()}))
        # Broadcast a high-visibility status so UI knows agents are about to respond
        n_pending = len([r for r in self.roles if r not in pending["addressed_by"]])
        asyncio.create_task(self.queue.put({
            "type": "status",
            "data": {"text": f"⚡ 用户插话已广播,{n_pending} 位待回应", "ts": time.time()},
        }))

    def _unaddressed_for(self, role: str) -> list[dict]:
        return [m for m in self.pending_user_msgs if role not in m["addressed_by"]]

    def _mark_addressed(self, role: str):
        for m in self.pending_user_msgs:
            m["addressed_by"].add(role)

    def _build_context(self, last_n: int = 12) -> str:
        recent = self.history[-last_n:]
        lines = []
        for m in recent:
            if m.role == "user":
                tag = "[👤 用户]"
            else:
                tag = f"[第{m.round}轮·{m.name}]" if m.round > 0 else f"[{m.name}]"
            lines.append(f"{tag} {m.content}")
        return "\n".join(lines)

    def _prior_speakers_summary(self, current_role: str) -> str:
        """List who has spoken before (this debate), with their last 1-line takeaway."""
        seen: dict[str, "DebateMessage"] = {}
        for m in self.history:
            if m.role in ("system", "user", "moderator"):
                continue
            if m.role == current_role:
                continue
            seen[m.role] = m  # keep the latest one per role
        if not seen:
            return ""
        lines = []
        for r, m in seen.items():
            snippet = m.content.strip().replace("\n", " ")
            if len(snippet) > 80:
                snippet = snippet[:80] + "..."
            lines.append(f"- {m.emoji} {m.name}(第{m.round}轮):「{snippet}」")
        return "\n".join(lines)

    async def _emit_thinking(self, role: str, on: bool):
        p = _persona(role)
        await self.queue.put({
            "type": "thinking",
            "data": {
                "role": role,
                "on": on,
                "name": p["name"],
                "emoji": p["emoji"],
                "color": p["color"],
                "ts": time.time(),
            },
        })

    async def _stream_speak(self, role: str, messages: list, rnd: int, temperature: float = 0.8) -> str:
        """Stream tokens as they arrive. Emits stream_start / stream_chunk / stream_end."""
        p = _persona(role)
        msg_id = str(uuid.uuid4())
        # announce start so client can create an empty bubble
        await self.queue.put({
            "type": "stream_start",
            "data": {
                "id": msg_id,
                "role": role,
                "name": p["name"],
                "emoji": p["emoji"],
                "color": p["color"],
                "round": rnd,
                "ts": time.time(),
            },
        })
        buf = ""
        try:
            async for delta in chat_stream(messages, llm_id=self._provider_for(role), temperature=temperature):
                if self.cancelled:
                    break
                buf += delta
                await self.queue.put({
                    "type": "stream_chunk",
                    "data": {"id": msg_id, "delta": delta},
                })
        except Exception as e:
            err = f"\n\n(发言失败:{e})"
            buf += err
            await self.queue.put({
                "type": "stream_chunk",
                "data": {"id": msg_id, "delta": err},
            })

        # Persist into history as a normal message and notify end
        full = buf.strip()
        msg = DebateMessage(
            id=msg_id,
            role=role,
            name=p["name"],
            emoji=p["emoji"],
            color=p["color"],
            content=full,
            round=rnd,
            ts=time.time(),
        )
        self.history.append(msg)
        await self.queue.put({
            "type": "stream_end",
            "data": {"id": msg_id, "content": full, "ts": time.time()},
        })
        return full

    async def _agent_speak(self, role: str, market_summary: str, rnd: int):
        if self.cancelled:
            return
        p = _persona(role)
        # short pre-stream "thinking" indicator (cleared by stream_start on the client)
        await self._emit_thinking(role, True)
        ctx = self._build_context(last_n=20)
        user_note = ""
        unaddressed = self._unaddressed_for(role)
        if unaddressed:
            interj = "\n".join(f"- 「{m['text']}」" for m in unaddressed)
            user_note = (
                f"\n\n🚨🚨🚨【最高优先级·用户刚刚插话,你必须先回应】🚨🚨🚨\n"
                f"{interj}\n"
                f"⚠️ 在你发言的**第一句**就要直接 @用户 回应这条插话(同意/反驳/解答疑问/补充信息),"
                f"然后再继续辩论。不要忽略,不要敷衍。"
            )

        prior = self._prior_speakers_summary(current_role=role)
        roundtable_hint = ""
        if prior:
            roundtable_hint = (
                f"\n\n【🪑 圆桌上其他人最近的观点(可以 @点名 任何一位回应/反驳/追问)】\n"
                f"{prior}\n"
                f"💡 你必须 @点名至少 1 位、推荐 2 位,具体引用 ta 的关键词,不要泛泛说「同意」。"
            )
        else:
            roundtable_hint = "\n\n(你是第一位发言者,直接亮明你({})的观点,后续大家会回应你。)".format(p["name"])

        messages = [
            {"role": "system", "content": p["system"]},
            {
                "role": "user",
                "content": (
                    f"辩论议题:{self.topic}\n\n"
                    f"【最新市场数据】\n{market_summary}\n\n"
                    f"【目前为止的辩论记录】\n{ctx or '(刚开始)'}"
                    f"{user_note}"
                    f"{roundtable_hint}\n\n"
                    f"现在轮到你({p['name']})发言。这是第 {rnd}/{self.max_rounds} 轮。"
                ),
            },
        ]
        try:
            await self._stream_speak(role, messages, rnd, temperature=0.8)
        finally:
            await self._emit_thinking(role, False)
        # mark this role as having addressed all currently-pending user msgs
        self._mark_addressed(role)
        # GC: drop interjections that all enabled roles have addressed
        self.pending_user_msgs = [
            m for m in self.pending_user_msgs
            if not all(r in m["addressed_by"] for r in self.roles)
        ]

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
        await self._emit_thinking("moderator", True)
        ctx = self._build_context(last_n=20)
        messages = [
            {"role": "system", "content": _persona("moderator")["system"]},
            {
                "role": "user",
                "content": (
                    f"以下是第 {rnd} 轮的圆桌发言。请按以下结构做小结(共 3-4 句):\n"
                    f"1. **共识点**:点名「@A 和 @B 都认同 X」\n"
                    f"2. **分歧点**:点名「@C 和 @D 在 Y 问题上有冲突,C 认为...,D 认为...」\n"
                    f"3. **下一轮焦点问题**:针对最大分歧抛一个具体问题,@点名希望谁先回答。\n"
                    f"如果有用户发言务必融入提问。\n\n{ctx}"
                ),
            },
        ]
        try:
            await self._stream_speak("moderator", messages, rnd, temperature=0.5)
        finally:
            await self._emit_thinking("moderator", False)
        # moderator also "addresses" pending user msgs (acknowledged in summary)
        self._mark_addressed("moderator")

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
            await self._stream_speak("moderator", messages, self.max_rounds + 1, temperature=0.4)
        except Exception as e:
            await self._emit(_build_msg("moderator", f"(最终决议生成失败:{e})", self.max_rounds + 1))

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
