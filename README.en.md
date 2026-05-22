# 🎙️ Debate Agents Party

> A general-purpose **multi-agent debate platform** — create rooms, pick a moderator, throw in agents with different worldviews, give them a topic, and watch them argue in real time. Jump in anytime to steer the discussion.

[English](./README.en.md) · [中文](./README.md)

![status](https://img.shields.io/badge/status-alpha-orange) ![python](https://img.shields.io/badge/python-3.12-blue) ![FastAPI](https://img.shields.io/badge/FastAPI-WebSocket-009688) ![LLM](https://img.shields.io/badge/LLM-multi--agent-purple) ![license](https://img.shields.io/badge/license-MIT-green)

**Keywords**: multi-agent · LLM debate · AI agents · agent orchestration · ChatGPT · DeepSeek · doubao · volcengine · FastAPI · WebSocket streaming · prompt engineering · agent framework · realtime chat · LLM roleplay

---

## ✨ What it does

Spin up a debate room around **any topic** (philosophy, product decisions, ethics, "is coffee better than tea", ...). A **moderator agent** dispatches turns to the participants you picked, every reply streams token-by-token into a chat UI, and you can interject at any moment — the moderator will fold your input into the next round.

- 🧠 **Create your own agents** — name, emoji, color, system prompt, and which LLM they use
- 🏛️ **Create rooms** — pick a moderator + participants + topic + max turns
- 🎤 **Moderator-driven** — uses `[NEXT: role]` / `[END]` tokens to schedule speakers and end naturally
- 🌊 **Token streaming end-to-end** — WebSocket, markdown rendered live
- 🙋 **User interjection** — type anytime, moderator routes the response on the next turn
- 💾 **Persistent history** — SQLite-backed, survives page refresh and backend restart
- 🔄 **Auto-resume** — debate runs as a backend background task; close the tab, come back later, history rehydrates
- ♻️ **One-click restart** — same room, tweak the topic, rerun; optionally keep or wipe prior history
- 🔌 **Multi-LLM** — any OpenAI-compatible endpoint (OpenAI, DeepSeek, Volcengine Ark, OpenRouter, local llama.cpp, ...). Different agents can use different LLMs.

### 10 preset agents (fully editable)

| | Agent | Stance |
|---|---|---|
| 🎤 | **Moderator** | Neutral scheduler, drives the flow |
| 🧱 | Realist | Resources, constraints, what actually ships |
| ✨ | Idealist | What *should* be, vision-first |
| 🔪 | Critic | Pokes holes in every argument |
| 🌅 | Optimist | Upside, second-order positives |
| 🌑 | Pessimist | Downside, second-order negatives |
| 🔍 | Skeptic | "Where's the evidence?" |
| 🚀 | Innovator | Reframe the question, novel angles |
| 🛠️ | Pragmatist | Trade-offs, MVP, ship it |
| ⚖️ | Ethicist | Who is harmed, what's fair |

These are *seeds* — once loaded they're rows in your config. Rename them, rewrite their system prompts, add 20 more, delete the ones you don't like.

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────┐
│  Static frontend (Node/Express on :3000)            │
│  Plain HTML + jQuery + marked + DOMPurify           │
│  ├─ /          rooms list + create/edit/delete      │
│  ├─ /agents    agents CRUD                          │
│  ├─ /room?id=  live debate (WebSocket client)       │
│  └─ /config    LLM configurations                   │
└─────────────────┬───────────────────────────────────┘
                  │ HTTP proxy /api/*  +  direct WS :8000
┌─────────────────▼───────────────────────────────────┐
│  FastAPI backend (:8000)                            │
│  ├─ /api/agents     CRUD                            │
│  ├─ /api/rooms      CRUD                            │
│  ├─ /api/rooms/{id}/history   latest session + msgs │
│  ├─ /api/config     LLM configs                     │
│  ├─ /ws/debate      WebSocket — subscribes to room  │
│  └─ DebateEngine    moderator-driven orchestration  │
│       ├─ per-agent LLM client (OpenAI-compatible)   │
│       └─ one engine per room, runs as bg asyncio    │
└─────────────────┬───────────────────────────────────┘
                  │
       ┌──────────┴──────────┐
       ▼                     ▼
  config.json           data/debate.db
  agents / rooms /      debate history (SQLite,
  llm_configs           WAL, created on first write)
  (schema_version: 3)
```

**Why it's small:** no Next.js, no React, no build step. The frontend is four static HTML files and a tiny Express server that proxies `/api/*` and serves static assets. Everything you can see in the UI is also a REST call you could `curl` directly.

**Data model:**
- `llm_configs[]` — `{id, name, model, base_url, api_key}` (OpenAI-compatible)
- `agents[]` — `{id, name, emoji, color, system, llm_id, is_moderator}`
- `rooms[]` — `{id, name, topic, moderator_id, agent_ids[], max_turns}`
- `default_llm_id` — fallback when an agent's `llm_id` is empty
- `debate_sessions` / `debate_messages` — SQLite tables for per-room debate history (kept out of `config.json`)

---

## 🚀 Quick start

### Prerequisites
- Python 3.10+
- Node.js 18+
- An API key for at least one OpenAI-compatible LLM provider

### 1. Clone & install

```bash
git clone https://github.com/<you>/debate-agents-party.git
cd debate-agents-party

# backend
python3 -m venv backend/venv
source backend/venv/bin/activate
pip install -r backend/requirements.txt

# frontend
cd web && npm install && cd ..
```

### 2. Configure your first LLM

Copy the template first:

```bash
cp config.example.json config.json
```

`config.json` is git-ignored — your API keys stay local. You have two options to add credentials:

**Option A — through the UI (recommended):** start the servers (next step), open `http://localhost:3000/config`, click "+ Add LLM", fill in name / model / base URL / API key, save, set it as default.

**Option B — edit JSON directly:** open `config.json` and fill in `llm_configs[0]`.

Example for DeepSeek:
```json
{
  "id": "deepseek-chat",
  "name": "DeepSeek",
  "model": "deepseek-chat",
  "base_url": "https://api.deepseek.com/v1",
  "api_key": "sk-..."
}
```

### 3. Run

Two terminals:

```bash
# terminal 1 — backend
source backend/venv/bin/activate
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

```bash
# terminal 2 — frontend
cd web && node server.js
```

Open **http://localhost:3000** and click the sample room, or hit "+ New room" to create your own.

### 💾 Persistence & data directory

- Debate history is stored in `data/debate.db` (SQLite, WAL). The DB and `data/` directory are **created lazily on first write** — zero-config for a fresh clone.
- `config.json` only holds agents / rooms / LLM configs. Debate transcripts are kept out of it, so the file stays small and easy to hand-edit or version.
- Override path: `DEBATE_DB_PATH=/var/lib/debate/debate.db uvicorn backend.main:app ...`
- Wipe all history: just `rm -rf data/` — it will be recreated on the next debate.
- Restarting the backend doesn't affect saved history; reopening a room auto-loads the latest session.

### Docker (optional)

```bash
docker compose up --build
```
Backend on `:8000`, frontend on `:3000`. Mount `backend/data/` to persist your config across rebuilds.

### Exposing to the public internet

⚠️ The app has **no authentication**. If you put it on a public IP, anyone who finds it can burn your LLM budget. Recommended:
- Bind to `127.0.0.1` and use an SSH tunnel: `ssh -L 3000:localhost:3000 -L 8000:localhost:8000 user@host`
- Or put nginx + basic auth in front
- Or restrict the cloud firewall to your own IP

---

## 🧭 Typical flow

1. **Configure LLMs** at `/config` — add as many as you want, mark one as default.
2. **Build your agent roster** at `/agents` — edit the 10 seeds or create new ones. Each agent can use a different LLM (e.g. moderator on a smart model, participants on a cheap fast one).
3. **Create a room** at `/` — pick the moderator, check the participants, write the topic, set max turns.
4. **Click into the room** — debate starts immediately, messages stream in. Type in the input box anytime to interject.
5. The moderator emits `[END]` when the discussion is resolved or hits the turn limit.

---

## 🔧 Customizing agents — the moderator protocol

Participants are free-form (anything you write in the system prompt is up to you). The **moderator** is the one constrained piece: it must emit one of these tokens on its **last line**:

- `[NEXT: <agent_id>]` — pass the mic to that agent (id must be in the room's roster)
- `[END]` — wrap up; engine then asks the moderator for a final summary and closes the WebSocket

If you write your own moderator agent, copy the system prompt from `backend/agents/personas.py::MODERATOR_SYSTEM` as a base. The engine also injects a small "roundtable obedience" rule into every non-moderator system prompt so participants stay on-topic and respond directly to the moderator's question.

---

## 📡 REST API

All endpoints are unauthenticated, JSON-only.

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/config` | — | LLMs + default (api_keys masked) |
| POST | `/api/config` | `{llm_configs?, default_llm_id?}` | `{ok:true}` |
| GET | `/api/agents` | — | `{agents:[...]}` |
| POST | `/api/agents` | `AgentBody` | created agent |
| PUT | `/api/agents/{id}` | `AgentBody` | updated agent |
| DELETE | `/api/agents/{id}` | — | `{ok:true}` |
| GET | `/api/rooms` | — | `{rooms:[...]}` |
| GET | `/api/rooms/{id}` | — | room |
| POST | `/api/rooms` | `RoomBody` | created room |
| PUT | `/api/rooms/{id}` | `RoomBody` | updated room |
| DELETE | `/api/rooms/{id}` | — | `{ok:true}` (cascades: deletes all sessions/history for the room) |
| GET | `/api/rooms/{id}/history` | — | latest session metadata + all messages (used for resume) |
| DELETE | `/api/rooms/{id}/history` | — | wipe all sessions/history for the room |
| WS | `/ws/debate` | client → `{type:"start" \| "restart", room_id, ...}` | server streams `stream_start` / `stream_chunk` / `stream_end` / `thinking` / `message` / `done` / `error` |

WebSocket client messages:
- `{type:"start", room_id}` — kick off (or attach to) a debate for the given room
- `{type:"restart", room_id, topic?, clear_history?}` — cancel the current engine, optionally update the topic, optionally wipe history, then run again
- `{type:"user_message", text}` — interjection, queued for the next round
- `{type:"cancel"}` — stop the current debate
- `{type:"ping"}` — heartbeat

---

## 📁 Project layout

```
debate-agents-party/
├── backend/
│   ├── main.py                  FastAPI app + WS handler + engine registry
│   ├── core/
│   │   ├── config_store.py      JSON store, schema migrations, CRUD helpers
│   │   ├── db.py                SQLite layer (lazy init, WAL, env override)
│   │   ├── debate_engine.py     moderator-driven orchestrator (per-room bg task)
│   │   └── llm.py               OpenAI-compatible streaming client
│   ├── agents/
│   │   └── personas.py          10 seed agent presets + MODERATOR_SYSTEM
│   └── requirements.txt
├── config.json                  ← agents / rooms / LLM configs (git-ignored)
├── config.example.json          ← template, committed
├── data/                        ← SQLite data dir (git-ignored, auto-created)
│   └── debate.db                debate sessions + messages
├── web/
│   ├── server.js                tiny Express static + /api proxy
│   └── public/
│       ├── index.html / agents.html / room.html / config.html
│       ├── js/                  one .js per page (jQuery)
│       └── css/app.css
└── docker-compose.yml
```

---

## 🎯 Use Cases

- 🎓 **Teaching** — Multi-role stance debates for philosophy / law / ethics classrooms
- 💼 **Product decisions** — Roleplay PM / engineer / finance / user perspectives
- ✍️ **Writing aid** — Let multiple AI personas cross-examine each other on a thesis
- 🔬 **LLM evaluation** — Same topic across different models, compare reasoning & stance
- 🧪 **Prompt experiments** — See how system-prompt tweaks shift persona behavior
- 🎮 **AI roleplay entertainment** — 10 distinct AIs arguing about a meme — instant theater

---

## 🗺️ Roadmap

- [x] Persistent debate history (SQLite) + auto-resume on refresh/restart
- [x] One-click restart, keep or wipe prior history
- [ ] Session history list UI (browse/switch past debates per room)
- [ ] Export a debate transcript as Markdown / share link
- [ ] Per-room model overrides (swap LLM per-debate, not per-agent)
- [ ] Branching debates (fork from any message)
- [ ] Optional authentication layer
- [ ] Memory / RAG hook so agents can cite documents

---

## 🤝 Contributing

PRs welcome. The codebase is intentionally small — under ~2k LOC across both halves — so it's easy to read end-to-end before changing anything.

## 📄 License

MIT — see [LICENSE](./LICENSE).

## 🙏 Credits

Inspired by [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents). Streaming UX details borrowed from years of staring at ChatGPT.
