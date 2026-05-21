"""Debate orchestrator — moderator-driven roundtable.

The moderator decides who speaks next via a `[NEXT: <role>]` directive on the
last line of its message. Analysts only speak when explicitly called.

Termination:
- moderator outputs `[END]`  → final verdict
- max_turns reached          → forced final verdict
- cancelled                  → bail
"""
from __future__ import annotations
import asyncio
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from .llm import chat_stream
from .config_store import load_config
from ..agents.personas import PERSONAS
from ..api.market import fetch_market, format_market_summary


# directive parsing — matches  [NEXT: bull]  or  [END]  on its own line.
_DIRECTIVE_RE = re.compile(
    r"^\s*\[\s*(NEXT\s*:\s*([a-zA-Z_]+)|END)\s*\]\s*$",
    re.MULTILINE,
)


@dataclass
class DebateMessage:
    id: str
    role: str
    name: str
    emoji: str
    color: str
    content: str
    round: int
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "role": self.role, "name": self.name,
            "emoji": self.emoji, "color": self.color, "content": self.content,
            "round": self.round, "ts": self.ts,
        }


def _persona(role: str) -> dict:
    return PERSONAS.get(role, {"name": role, "emoji": "❓", "color": "#999", "system": ""})


def _build_msg(role: str, content: str, rnd: int) -> DebateMessage:
    p = _persona(role)
    return DebateMessage(
        id=str(uuid.uuid4()),
        role=role, name=p.get("name", role), emoji=p.get("emoji", "❓"),
        color=p.get("color", "#888"), content=content, round=rnd,
    )


def _parse_directive(text: str) -> tuple[str, Optional[str]]:
    """Return (kind, role). kind ∈ {'next','end','none'}."""
    matches = list(_DIRECTIVE_RE.finditer(text))
    if not matches:
        return "none", None
    last = matches[-1]
    body = last.group(1).strip().upper()
    if body == "END":
        return "end", None
    if body.startswith("NEXT"):
        role = (last.group(2) or "").strip().lower()
        return "next", role or None
    return "none", None


def _strip_directive(text: str) -> str:
    """Remove the trailing directive line from displayed content."""
    return _DIRECTIVE_RE.sub("", text).rstrip()


class DebateEngine:
    """One instance per active debate room."""

    def __init__(self, topic: str, queue: asyncio.Queue):
        self.topic = topic
        self.queue = queue
        self.history: list[DebateMessage] = []
        # Pending interjections waiting for moderator to dispatch
        self.pending_user_msgs: list[dict] = []
        self.cancelled = False

        cfg = load_config()
        self.roles: list[str] = cfg["agents"].get("enabled_roles", ["bull", "bear", "risk"])
        # Re-purpose old max_rounds as a soft turn budget.
        # 1 round ~= len(roles)+1 turns; default 3 rounds → ~ (n+1)*3 turns
        max_rounds = int(cfg["agents"].get("max_rounds", 3))
        self.max_turns: int = max(8, (len(self.roles) + 1) * max_rounds)
        self.role_llm: dict = cfg["agents"].get("role_llm", {}) or {}
        self.data_cfg = cfg.get("data_source", {})

        self.turn_count = 0  # counts both moderator and analyst turns

    # ------------------------------------------------------------------
    # plumbing
    # ------------------------------------------------------------------
    def _provider_for(self, role: str) -> Optional[str]:
        p = self.role_llm.get(role, "")
        return p or None

    async def _emit(self, msg: DebateMessage):
        self.history.append(msg)
        await self.queue.put({"type": "message", "data": msg.to_dict()})

    async def _emit_status(self, text: str):
        await self.queue.put({"type": "status", "data": {"text": text, "ts": time.time()}})

    async def _emit_thinking(self, role: str, on: bool):
        p = _persona(role)
        await self.queue.put({
            "type": "thinking",
            "data": {"role": role, "on": on, "name": p["name"],
                     "emoji": p["emoji"], "color": p["color"], "ts": time.time()},
        })

    def add_user_message(self, text: str):
        msg = _build_msg("user", text, rnd=-1)
        self.history.append(msg)
        self.pending_user_msgs.append({"id": msg.id, "text": text, "consumed": False})
        asyncio.create_task(self.queue.put({"type": "message", "data": msg.to_dict()}))
        asyncio.create_task(self.queue.put({
            "type": "status",
            "data": {"text": "⚡ 用户插话已广播,主持人将下一轮安排回应", "ts": time.time()},
        }))

    # ------------------------------------------------------------------
    # context builders
    # ------------------------------------------------------------------
    def _build_transcript(self, last_n: int = 30) -> str:
        recent = self.history[-last_n:]
        lines = []
        for m in recent:
            if m.role == "user":
                tag = "[👤 用户插话]"
            elif m.role == "moderator":
                tag = "[🎤 主持人]"
            else:
                tag = f"[{m.emoji} {m.name}]"
            lines.append(f"{tag} {m.content}")
        return "\n".join(lines) if lines else "(刚开始)"

    def _roles_menu(self) -> str:
        return ", ".join(f"{r}({_persona(r)['name']})" for r in self.roles)

    # ------------------------------------------------------------------
    # streaming speak
    # ------------------------------------------------------------------
    async def _stream_speak(
        self, role: str, messages: list, rnd: int,
        temperature: float, strip_directive: bool = False,
    ) -> str:
        """Stream tokens. Returns the full raw buffer (caller may parse directives)."""
        p = _persona(role)
        msg_id = str(uuid.uuid4())
        await self.queue.put({
            "type": "stream_start",
            "data": {"id": msg_id, "role": role, "name": p["name"], "emoji": p["emoji"],
                     "color": p["color"], "round": rnd, "ts": time.time()},
        })

        buf = ""
        # If we need to strip a trailing directive line, hold back any text
        # after the last newline so we don't flicker `[NEXT:` into the UI.
        # Simpler approach: stream everything; on stream_end send cleaned content.
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
            await self.queue.put({"type": "stream_chunk", "data": {"id": msg_id, "delta": err}})

        full = buf.strip()
        display = _strip_directive(full) if strip_directive else full
        msg = DebateMessage(
            id=msg_id, role=role, name=p["name"], emoji=p["emoji"],
            color=p["color"], content=display, round=rnd, ts=time.time(),
        )
        self.history.append(msg)
        await self.queue.put({
            "type": "stream_end",
            "data": {"id": msg_id, "content": display, "ts": time.time()},
        })
        return full  # caller sees raw text including directive

    # ------------------------------------------------------------------
    # turn implementations
    # ------------------------------------------------------------------
    async def _moderator_turn(self, market_summary: str, kickoff: bool) -> tuple[str, Optional[str]]:
        """Run one moderator turn. Returns (kind, next_role)."""
        await self._emit_thinking("moderator", True)
        transcript = self._build_transcript(last_n=30)

        unconsumed = [m for m in self.pending_user_msgs if not m["consumed"]]
        user_block = ""
        if unconsumed:
            joined = "\n".join(f"  - 「{m['text']}」" for m in unconsumed)
            user_block = (
                f"\n\n🚨【用户刚刚插话,你必须把球递给最合适的角色优先回应,并在指令前点明问题】\n{joined}"
            )

        if kickoff:
            user_prompt = (
                f"辩论议题:**{self.topic}**\n\n"
                f"【最新市场快照】\n{market_summary}\n\n"
                f"参与角色:{self._roles_menu()}\n"
                f"轮次预算:{self.max_turns} 个发言\n\n"
                f"现在请你做开场:\n"
                f"1. 用 1-2 句介绍议题、市场关键数据点\n"
                f"2. 选定**第一位**发言者,给 ta 一个具体问题(比如:先请技术分析师给出当前结构判断)\n"
                f"3. 最后一行严格输出 `[NEXT: <role>]`,role 从 {self.roles} 中选\n"
            )
        else:
            user_prompt = (
                f"辩论议题:**{self.topic}**\n\n"
                f"【市场快照】\n{market_summary}\n\n"
                f"【目前为止的圆桌发言】\n{transcript}\n"
                f"{user_block}\n\n"
                f"现在轮到你主持:\n"
                f"1. 简短点评上一位(1 句,可省略)\n"
                f"2. 指定下一位发言并给一个具体问题(尽量推动分歧/反驳/追问/补数据)\n"
                f"3. 候选角色:{self.roles};若觉得讨论已充分,输出 `[END]`\n"
                f"4. 最后一行严格输出 `[NEXT: <role>]` 或 `[END]`\n"
            )
        messages = [
            {"role": "system", "content": _persona("moderator")["system"]},
            {"role": "user", "content": user_prompt},
        ]
        try:
            full = await self._stream_speak("moderator", messages, rnd=self.turn_count + 1,
                                            temperature=0.5, strip_directive=True)
        finally:
            await self._emit_thinking("moderator", False)

        # mark all pending user msgs as consumed by the moderator (the next
        # analyst will see them in transcript and the moderator already directed
        # the response)
        for m in self.pending_user_msgs:
            m["consumed"] = True

        kind, role = _parse_directive(full)
        return kind, role

    async def _analyst_turn(self, role: str, market_summary: str):
        if self.cancelled:
            return
        await self._emit_thinking(role, True)
        transcript = self._build_transcript(last_n=30)
        p = _persona(role)
        # find the moderator's most recent prompt — that's what this analyst must answer
        mod_question = ""
        for m in reversed(self.history):
            if m.role == "moderator":
                mod_question = m.content
                break

        user_prompt = (
            f"辩论议题:**{self.topic}**\n\n"
            f"【市场快照】\n{market_summary}\n\n"
            f"【目前为止的圆桌发言】\n{transcript}\n\n"
            f"🎤【主持人刚刚的提问 — 你必须直接回答】\n{mod_question or '(请就你的角色给出观点)'}\n\n"
            f"现在轮到你({p['name']})发言。开场第一句话就直接切入主持人的问题,"
            f"控制在 2-4 句中文,引用具体数据/价位。"
        )
        messages = [
            {"role": "system", "content": p["system"]},
            {"role": "user", "content": user_prompt},
        ]
        try:
            await self._stream_speak(role, messages, rnd=self.turn_count + 1,
                                     temperature=0.8, strip_directive=False)
        finally:
            await self._emit_thinking(role, False)

    async def _final_verdict(self, market_summary: str):
        transcript = self._build_transcript(last_n=60)
        messages = [
            {"role": "system", "content": _persona("moderator")["system"]},
            {
                "role": "user",
                "content": (
                    f"辩论已结束。议题:{self.topic}\n\n"
                    f"完整记录:\n{transcript}\n\n"
                    f"请综合所有观点给出最终决议,严格按以下 markdown 格式输出(**不要再输出 [NEXT] 或 [END]**):\n"
                    f"**最终决议**: <Buy / Hold / Sell>\n"
                    f"**理由**: 2-3 句\n"
                    f"**关键风险**: 1-2 个最需要警惕的点\n"
                    f"**建议关注的关键 level**: 列 2-3 个具体价位"
                ),
            },
        ]
        try:
            await self._stream_speak("moderator", messages, rnd=self.turn_count + 1,
                                     temperature=0.4, strip_directive=True)
        except Exception as e:
            await self._emit(_build_msg("moderator", f"(最终决议生成失败:{e})", self.turn_count + 1))

    # ------------------------------------------------------------------
    # main loop
    # ------------------------------------------------------------------
    async def run(self):
        try:
            await self._emit_status("拉取市场数据...")
            market = await fetch_market(
                self.data_cfg.get("primary", "binance"),
                self.data_cfg.get("symbol", "BTCUSDT"),
            )
            market_summary = format_market_summary(market)
            await self._emit_status(f"市场数据已获取 · {market['source']}")

            # === turn 1: moderator kickoff ===
            self.turn_count += 1
            await self._emit_status("🎤 主持人开场...")
            kind, next_role = await self._moderator_turn(market_summary, kickoff=True)

            # === driven loop ===
            while not self.cancelled and self.turn_count < self.max_turns:
                if kind == "end":
                    break
                if kind != "next" or not next_role:
                    # moderator failed to produce a directive — fall back to first enabled role
                    next_role = self.roles[0] if self.roles else None
                if not next_role or next_role not in self.roles:
                    # moderator picked an invalid role — pick first enabled one
                    next_role = self.roles[0] if self.roles else None
                if not next_role:
                    break

                self.turn_count += 1
                await self._emit_status(f"{_persona(next_role)['name']} 发言中...")
                await self._analyst_turn(next_role, market_summary)
                await asyncio.sleep(0.2)
                if self.cancelled:
                    break

                self.turn_count += 1
                await self._emit_status("🎤 主持人调度下一位...")
                kind, next_role = await self._moderator_turn(market_summary, kickoff=False)

            if not self.cancelled:
                await self._emit_status("生成最终决议...")
                await self._final_verdict(market_summary)
                await self._emit_status("辩论结束 ✅")
                await self.queue.put({"type": "done", "data": {}})
        except Exception as e:
            await self.queue.put({"type": "error", "data": {"text": str(e)}})

    def cancel(self):
        self.cancelled = True
