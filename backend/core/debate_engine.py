"""Debate orchestrator — moderator-driven roundtable, generic topics.

Driven by a Room: {topic, moderator_id, agent_ids, max_turns}.
Agents are looked up from config.agents[] by id.

The moderator decides who speaks next via a `[NEXT: <agent_id>]` directive
on the last line. `[END]` finalizes.

Termination:
- moderator outputs `[END]`         → final verdict
- max_turns reached                 → forced final verdict
- cancelled                         → bail
"""
from __future__ import annotations
import asyncio
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from .llm import chat_stream
from .config_store import load_config, get_room, get_agent
from . import db as db
from . import web_search as ws
from ..agents.personas import ANALYST_OBEDIENCE_RULE


# directive parsing — matches `[NEXT: agent_id]` or `[END]` on its own line.
# agent ids allow lowercase, digits, _, - (hex slugs work too).
_DIRECTIVE_RE = re.compile(
    r"^\s*\[\s*(NEXT\s*:\s*([a-zA-Z0-9_\-]+)|END)\s*\]\s*$",
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


def _parse_directive(text: str) -> tuple[str, Optional[str]]:
    matches = list(_DIRECTIVE_RE.finditer(text))
    if not matches:
        return "none", None
    last = matches[-1]
    body = last.group(1).strip().upper()
    if body == "END":
        return "end", None
    if body.startswith("NEXT"):
        role = (last.group(2) or "").strip()
        return "next", (role.lower() or None)
    return "none", None


def _strip_directive(text: str) -> str:
    return _DIRECTIVE_RE.sub("", text).rstrip()


class DebateEngine:
    """One instance per active debate room."""

    def __init__(self, room_id: str, queue: asyncio.Queue, topic_override: str | None = None,
                 session_id: str | None = None):
        self.queue = queue
        self.history: list[DebateMessage] = []
        self.pending_user_msgs: list[dict] = []
        self.cancelled = False
        self.finalize_requested = False
        self._user_msg_event = asyncio.Event()
        self.extension_per_user_msg = 4  # 每次用户插话给的额外轮次预算

        cfg = load_config()
        room = get_room(cfg, room_id)
        if room is None:
            raise RuntimeError(f"房间不存在: {room_id}")
        self.room = room
        self.topic = (topic_override or room.get("topic") or "").strip() or "(无议题)"

        # session: 复用 (resume) 或新建
        if session_id:
            sess = db.get_session(session_id)
            if sess is None or sess["room_id"] != room_id:
                raise RuntimeError(f"会话不存在或不属于该房间: {session_id}")
            self.session_id = session_id
            self.topic = sess["topic"]
            # 把已有消息回灌到 history (供 transcript 构建)
            for m in db.get_messages(session_id):
                self.history.append(DebateMessage(
                    id=m["id"], role=m["role"], name=m["name"], emoji=m["emoji"] or "",
                    color=m["color"] or "", content=m["content"], round=int(m["round"] or 0),
                    ts=float(m["ts"]),
                ))
            db.update_session_status(session_id, "running")
        else:
            sess = db.create_session(room_id=room_id, topic=self.topic)
            self.session_id = sess["id"]

        moderator = get_agent(cfg, room.get("moderator_id", ""))
        if moderator is None or not moderator.get("is_moderator"):
            # try to find any moderator-capable agent
            moderator = next((a for a in cfg["agents"] if a.get("is_moderator")), None)
            if moderator is None:
                raise RuntimeError("房间没有指定主持人,且配置中无可用主持人")
        self.moderator = moderator

        self.agents: list[dict] = []
        for aid in room.get("agent_ids", []):
            a = get_agent(cfg, aid)
            if a is not None:
                self.agents.append(a)
        if not self.agents:
            raise RuntimeError("房间没有参与的 agent")

        self.max_turns: int = int(room.get("max_turns", 16))
        self.turn_count = 0

    # ------------------------------------------------------------------
    # plumbing
    # ------------------------------------------------------------------
    def _persona_for_role(self, role_id: str) -> dict:
        """role_id is the agent.id; falls back to a stub for special 'user' role."""
        if role_id == "user":
            return {"id": "user", "name": "你", "emoji": "🙋", "color": "#58a6ff", "system": "", "llm_id": ""}
        if role_id == self.moderator["id"]:
            return self.moderator
        for a in self.agents:
            if a["id"] == role_id:
                return a
        return {"id": role_id, "name": role_id, "emoji": "❓", "color": "#888", "system": "", "llm_id": ""}

    async def _emit(self, msg: DebateMessage):
        self.history.append(msg)
        db.append_message(self.session_id, msg.to_dict())
        await self.queue.put({"type": "message", "data": msg.to_dict()})

    async def _emit_status(self, text: str):
        await self.queue.put({"type": "status", "data": {"text": text, "ts": time.time()}})

    async def _emit_thinking(self, role_id: str, on: bool):
        p = self._persona_for_role(role_id)
        await self.queue.put({
            "type": "thinking",
            "data": {"role": role_id, "on": on, "name": p["name"],
                     "emoji": p["emoji"], "color": p["color"], "ts": time.time()},
        })

    def add_user_message(self, text: str):
        p = self._persona_for_role("user")
        msg = DebateMessage(
            id=str(uuid.uuid4()), role="user", name=p["name"], emoji=p["emoji"],
            color=p["color"], content=text, round=-1,
        )
        self.history.append(msg)
        db.append_message(self.session_id, msg.to_dict())
        self.pending_user_msgs.append({"id": msg.id, "text": text, "consumed": False})
        # 用户插话:额外赠送 N 轮预算,让讨论可以继续
        self.max_turns += self.extension_per_user_msg
        self._user_msg_event.set()
        asyncio.create_task(self.queue.put({"type": "message", "data": msg.to_dict()}))
        asyncio.create_task(self.queue.put({
            "type": "status",
            "data": {"text": f"⚡ 用户插话已广播,辩论延长 {self.extension_per_user_msg} 轮", "ts": time.time()},
        }))

    def request_finalize(self):
        """用户主动请求结束辩论 → 进入终局总结。"""
        self.finalize_requested = True
        self._user_msg_event.set()

    # ------------------------------------------------------------------
    # context builders
    # ------------------------------------------------------------------
    def _build_transcript(self, last_n: int = 30) -> str:
        recent = self.history[-last_n:]
        lines = []
        for m in recent:
            if m.role == "user":
                tag = "[👤 用户插话]"
            elif m.role == self.moderator["id"]:
                tag = f"[🎤 {m.name}]"
            else:
                tag = f"[{m.emoji} {m.name}]"
            lines.append(f"{tag} {m.content}")
        return "\n".join(lines) if lines else "(刚开始)"

    def _roles_menu(self) -> str:
        return ", ".join(f"{a['id']}=@{a['name']}" for a in self.agents)

    def _valid_role_ids(self) -> list[str]:
        return [a["id"] for a in self.agents]

    def _roster_zh(self) -> str:
        return "、".join(f"@{a['name']}" for a in self.agents)

    # ------------------------------------------------------------------
    # streaming speak
    # ------------------------------------------------------------------
    async def _stream_speak(
        self, role_id: str, messages: list, rnd: int,
        temperature: float, strip_directive: bool = False,
    ) -> str:
        p = self._persona_for_role(role_id)
        msg_id = str(uuid.uuid4())
        await self.queue.put({
            "type": "stream_start",
            "data": {"id": msg_id, "role": role_id, "name": p["name"], "emoji": p["emoji"],
                     "color": p["color"], "round": rnd, "ts": time.time()},
        })

        buf = ""
        try:
            async for delta in chat_stream(messages, llm_id=p.get("llm_id") or None, temperature=temperature):
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
            id=msg_id, role=role_id, name=p["name"], emoji=p["emoji"],
            color=p["color"], content=display, round=rnd, ts=time.time(),
        )
        self.history.append(msg)
        db.append_message(self.session_id, msg.to_dict())
        await self.queue.put({
            "type": "stream_end",
            "data": {"id": msg_id, "content": display, "ts": time.time()},
        })
        return full

    # ------------------------------------------------------------------
    # turn implementations
    # ------------------------------------------------------------------
    async def _moderator_turn(self, kickoff: bool) -> tuple[str, Optional[str]]:
        mid = self.moderator["id"]
        await self._emit_thinking(mid, True)
        transcript = self._build_transcript(last_n=30)

        unconsumed = [m for m in self.pending_user_msgs if not m["consumed"]]
        user_block = ""
        if unconsumed:
            joined = "\n".join(f"  - 「{m['text']}」" for m in unconsumed)
            user_block = (
                f"\n\n🚨【用户刚刚插话,你必须把球递给最合适的角色优先回应,并在指令前点明问题】\n{joined}"
            )

        role_ids = self._valid_role_ids()
        if kickoff:
            user_prompt = (
                f"辩论议题:**{self.topic}**\n\n"
                f"参与角色(用 id 引用):{self._roles_menu()}\n"
                f"轮次预算:{self.max_turns} 个发言\n\n"
                f"现在请你做开场:\n"
                f"1. 用 1-3 句中文铺垫议题(为什么值得辩论、关键张力是什么)\n"
                f"2. 选定**第一位**发言者,给 ta 一个具体的、能开启对抗的问题\n"
                f"3. 最后一行严格输出 `[NEXT: <agent_id>]`,id 必须从这些里选:{role_ids}\n"
            )
        else:
            user_prompt = (
                f"辩论议题:**{self.topic}**\n\n"
                f"【目前为止的圆桌发言】\n{transcript}\n"
                f"{user_block}\n\n"
                f"现在轮到你主持:\n"
                f"1. 简短点评上一位(1 句,可省略)\n"
                f"2. 指定下一位发言并给一个具体问题(推动分歧/反驳/追问/补论据)\n"
                f"3. 候选 agent id:{role_ids};若讨论已充分,输出 `[END]`\n"
                f"4. 最后一行严格输出 `[NEXT: <agent_id>]` 或 `[END]`\n"
            )
        messages = [
            {"role": "system", "content": self.moderator.get("system", "")},
            {"role": "user", "content": user_prompt},
        ]
        try:
            full = await self._stream_speak(mid, messages, rnd=self.turn_count + 1,
                                            temperature=0.5, strip_directive=True)
        finally:
            await self._emit_thinking(mid, False)

        for m in self.pending_user_msgs:
            m["consumed"] = True

        kind, role = _parse_directive(full)
        return kind, role

    async def _analyst_turn(self, role_id: str):
        if self.cancelled:
            return
        await self._emit_thinking(role_id, True)
        transcript = self._build_transcript(last_n=30)
        p = self._persona_for_role(role_id)
        mod_question = ""
        for m in reversed(self.history):
            if m.role == self.moderator["id"]:
                mod_question = m.content
                break

        # --- 联网搜索阶段(每个 agent 默认开启;agent 可通过 web_search=False 关闭) ---
        search_block = ""
        if p.get("web_search", True) is not False and not self.cancelled:
            try:
                await self._emit_status(f"🔎 {p['name']} 正在判断是否需要联网搜索...")
                queries = await ws.decide_queries(
                    agent_name=p["name"],
                    agent_system=p.get("system", ""),
                    topic=self.topic,
                    moderator_question=mod_question,
                    transcript_tail=transcript,
                    llm_id=p.get("llm_id") or None,
                )
                if queries:
                    await self.queue.put({
                        "type": "search",
                        "data": {"role": role_id, "name": p["name"], "emoji": p["emoji"],
                                 "color": p["color"], "queries": queries, "ts": time.time()},
                    })
                    await self._emit_status(f"🌐 {p['name']} 联网搜索:{' / '.join(queries)}")
                    results = {}
                    for q in queries:
                        results[q] = await ws.search(q, max_results=4)
                    search_block = ws.format_results_for_prompt(results)
            except Exception as e:
                # 搜索失败不应阻塞辩论
                await self._emit_status(f"(联网搜索失败,跳过:{e})")
                search_block = ""

        user_prompt = (
            f"辩论议题:**{self.topic}**\n\n"
            f"【在场队友】{self._roster_zh()}\n"
            f"(若要 @点名,只能用上面这些**中文名**,不要用英文 id)\n\n"
            f"【目前为止的圆桌发言】\n{transcript}\n\n"
            f"🎤【主持人刚刚的提问 — 你必须直接回答】\n{mod_question or '(请就你的角色给出观点)'}\n\n"
            f"现在轮到你({p['name']})发言。开场第一句话就直接切入主持人的问题。"
        )
        system = (p.get("system") or "") + ANALYST_OBEDIENCE_RULE + search_block
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ]
        try:
            await self._stream_speak(role_id, messages, rnd=self.turn_count + 1,
                                     temperature=0.85, strip_directive=False)
        finally:
            await self._emit_thinking(role_id, False)

    async def _final_verdict(self):
        transcript = self._build_transcript(last_n=80)
        messages = [
            {"role": "system", "content": self.moderator.get("system", "")},
            {
                "role": "user",
                "content": (
                    f"辩论已结束。议题:{self.topic}\n\n"
                    f"完整记录:\n{transcript}\n\n"
                    f"请以**中立主持人**身份给出最终总结,严格按以下 markdown 格式输出"
                    f"(**禁止再输出 [NEXT] 或 [END]**):\n\n"
                    f"## 🏁 辩论总结\n\n"
                    f"**核心分歧**: 2-3 条,各方在哪些点上无法和解\n\n"
                    f"**已达共识**: 1-2 条,哪些观点全场都接受\n\n"
                    f"**最有说服力的论点(主持人评)**: 点名 1-2 位 agent 及其关键论据\n\n"
                    f"**未被充分讨论的盲区**: 1-2 个值得后续展开的方向\n\n"
                    f"**给观察者的一句话**: 这场辩论之后,你应该带走什么"
                ),
            },
        ]
        try:
            await self._stream_speak(self.moderator["id"], messages, rnd=self.turn_count + 1,
                                     temperature=0.4, strip_directive=True)
        except Exception as e:
            p = self.moderator
            msg = DebateMessage(
                id=str(uuid.uuid4()), role=p["id"], name=p["name"], emoji=p["emoji"],
                color=p["color"], content=f"(最终总结生成失败:{e})", round=self.turn_count + 1,
            )
            self.history.append(msg)
            db.append_message(self.session_id, msg.to_dict())

    # ------------------------------------------------------------------
    # main loop
    # ------------------------------------------------------------------
    async def run(self):
        try:
            await self._emit_status(f"🎤 {self.moderator['name']} 开场...")
            # === turn 1: moderator kickoff ===
            self.turn_count += 1
            kind, next_role = await self._moderator_turn(kickoff=True)

            while not self.cancelled:
                # 上限/[END] → 进入暂停态等用户决定
                if self.finalize_requested:
                    break
                if kind == "end" or self.turn_count >= self.max_turns:
                    await self._emit_status(
                        "⏸ 辩论暂停 — 你可以继续插话延长讨论,或点「结束辩论」生成总结"
                    )
                    await self.queue.put({"type": "paused", "data": {"turn_count": self.turn_count, "max_turns": self.max_turns}})
                    self._user_msg_event.clear()
                    await self._user_msg_event.wait()
                    if self.cancelled or self.finalize_requested:
                        break
                    # 用户插话后继续,重置 kind 让主持人接管
                    kind = "next"

                valid = set(self._valid_role_ids())
                if kind != "next" or not next_role or next_role not in valid:
                    next_role = self._valid_role_ids()[0]

                self.turn_count += 1
                await self._emit_status(f"{self._persona_for_role(next_role)['name']} 发言中...")
                await self._analyst_turn(next_role)
                await asyncio.sleep(0.15)
                if self.cancelled:
                    break

                self.turn_count += 1
                await self._emit_status(f"🎤 {self.moderator['name']} 调度下一位...")
                kind, next_role = await self._moderator_turn(kickoff=False)

            if not self.cancelled:
                await self._emit_status("生成最终总结...")
                await self._final_verdict()
                await self._emit_status("辩论结束 ✅")
                db.update_session_status(self.session_id, "done")
                await self.queue.put({"type": "done", "data": {}})
            else:
                db.update_session_status(self.session_id, "cancelled")
        except Exception as e:
            db.update_session_status(self.session_id, "cancelled")
            await self.queue.put({"type": "error", "data": {"text": str(e)}})

    def cancel(self):
        self.cancelled = True
