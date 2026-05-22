# 🎙️ Debate Agents Party

> 一个通用的**多 agent 辩论平台** —— 新建房间、指定主持人、放进几个不同立场的 agent，丢一个议题，看他们实时辩论。你随时可以插话引导讨论。

[English](./README.en.md) · [中文](./README.md)

![status](https://img.shields.io/badge/status-alpha-orange) ![python](https://img.shields.io/badge/python-3.12-blue) ![frontend](https://img.shields.io/badge/frontend-jQuery-yellow) ![license](https://img.shields.io/badge/license-MIT-green)

---

## ✨ 它能做什么

围绕**任何议题**（哲学、产品决策、伦理、"咖啡好还是茶好"...）开一场辩论。**主持人 agent** 调度发言顺序，每一句回复都通过 token 流实时弹进聊天 UI。你随时可以插话 —— 主持人会在下一轮把你的问题派给最合适的角色回应。

- 🧠 **自定义 agent** —— 名字、emoji、配色、system prompt、绑定哪个 LLM
- 🏛️ **自定义房间** —— 选主持人 + 参与者 + 议题 + 最大轮次
- 🎤 **主持人驱动** —— 用 `[NEXT: role]` / `[END]` token 调度发言、自然终结
- 🌊 **端到端流式输出** —— WebSocket，markdown 边收边渲染
- 🙋 **用户插话** —— 任何时候都能打字，主持人下一轮回应
- 🔌 **多 LLM** —— 任何 OpenAI 兼容接口（OpenAI / DeepSeek / 火山方舟 / OpenRouter / 本地 llama.cpp ...），不同 agent 可以用不同 LLM

### 10 个预设 agent（可编辑/删除/增补）

| | 角色 | 立场 |
|---|---|---|
| 🎤 | **主持人** | 中立调度者，掌控节奏 |
| 🧱 | 现实主义者 | 资源、约束、能不能落地 |
| ✨ | 理想主义者 | 应该是什么样、愿景优先 |
| 🔪 | 批判者 | 戳每个论点的漏洞 |
| 🌅 | 乐观主义者 | 上行空间、二阶正向效应 |
| 🌑 | 悲观主义者 | 下行风险、二阶负向效应 |
| 🔍 | 怀疑论者 | "证据呢？" |
| 🚀 | 创新者 | 重新定义问题、提出新角度 |
| 🛠️ | 实用主义者 | 取舍、MVP、先跑起来 |
| ⚖️ | 伦理学者 | 谁会受伤、是否公平 |

这些只是**种子** —— 加载后就变成你配置里的普通数据，可随意改名、改 system prompt、删掉不喜欢的、再加 20 个新的。

---

## 🏗️ 架构

```
┌─────────────────────────────────────────────────────┐
│  静态前端（Node/Express，端口 3000）                │
│  纯 HTML + jQuery + marked + DOMPurify              │
│  ├─ /          房间列表 + 增删改                    │
│  ├─ /agents    Agent 增删改                         │
│  ├─ /room?id=  实时辩论（WebSocket 客户端）         │
│  └─ /config    LLM 配置                             │
└─────────────────┬───────────────────────────────────┘
                  │ HTTP 代理 /api/*  +  直连 WS :8000
┌─────────────────▼───────────────────────────────────┐
│  FastAPI 后端（端口 8000）                          │
│  ├─ /api/agents     CRUD                            │
│  ├─ /api/rooms      CRUD                            │
│  ├─ /api/config     LLM 配置                        │
│  ├─ /ws/debate      WebSocket —— 一连接一场辩论    │
│  └─ DebateEngine    主持人驱动的编排器              │
│         └─ 每个 agent 一个 LLM 客户端（OpenAI 兼容）│
└─────────────────────────────────────────────────────┘
           │
           ▼
    config.json   ← 唯一数据源
    （llm_configs[]、agents[]、rooms[]、schema_version: 3）
```

**为什么这么轻量**：没有 Next.js、没有 React、没有构建步骤。前端就是 4 个静态 HTML + 一个迷你 Express 转发 `/api/*` 并发静态文件。UI 上能做的事，curl 也能做。

**数据模型：**
- `llm_configs[]` —— `{id, name, model, base_url, api_key}`（OpenAI 兼容）
- `agents[]` —— `{id, name, emoji, color, system, llm_id, is_moderator}`
- `rooms[]` —— `{id, name, topic, moderator_id, agent_ids[], max_turns}`
- `default_llm_id` —— 当 agent 的 `llm_id` 为空时使用

---

## 🚀 快速开始

### 前置要求
- Python 3.10+
- Node.js 18+
- 至少一个 OpenAI 兼容 LLM 的 API key

### 1. 克隆 & 安装

```bash
git clone https://github.com/<you>/debate-agents-party.git
cd debate-agents-party

# 后端
python3 -m venv backend/venv
source backend/venv/bin/activate
pip install -r backend/requirements.txt

# 前端
cd web && npm install && cd ..
```

### 2. 配置第一个 LLM

先从模板复制一份：

```bash
cp config.example.json config.json
```

`config.json` 已被 git 忽略 —— 你的 API key 只会留在本地。两种添加凭据的方式：

**方式 A —— 通过 UI（推荐）**：先启动两个服务（下一步），打开 `http://localhost:3000/config`，点"+ 添加 LLM"，填写名称 / 模型 / Base URL / API Key，保存，设为默认。

**方式 B —— 直接编辑 JSON**：打开 `config.json`，在 `llm_configs[0]` 填上。

DeepSeek 示例：
```json
{
  "id": "deepseek-chat",
  "name": "DeepSeek",
  "model": "deepseek-chat",
  "base_url": "https://api.deepseek.com/v1",
  "api_key": "sk-..."
}
```

火山方舟示例：
```json
{
  "id": "ark-doubao",
  "name": "豆包",
  "model": "doubao-1-5-pro-32k-250115",
  "base_url": "https://ark.cn-beijing.volces.com/api/v3",
  "api_key": "你的方舟 key"
}
```

### 3. 启动

开两个终端：

```bash
# 终端 1 —— 后端
source backend/venv/bin/activate
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

```bash
# 终端 2 —— 前端
cd web && node server.js
```

浏览器打开 **http://localhost:3000**，点示例房间体验，或点"+ 新建辩论室"开你自己的。

### Docker（可选）

```bash
docker compose up --build
```
后端 `:8000`，前端 `:3000`。把 `backend/data/` 挂出来，重新构建不丢配置。

### 暴露到公网

⚠️ 项目**没有任何鉴权**。直接放公网 IP，谁找到都能用你配的 LLM 烧 token。建议：
- 绑定 `127.0.0.1` + SSH 隧道：`ssh -L 3000:localhost:3000 -L 8000:localhost:8000 user@host`
- 或者前面挂 nginx + basic auth
- 或者云防火墙只放行你自己的 IP

---

## 🧭 典型使用流程

1. **在 `/config` 配置 LLM** —— 想加几个加几个，选一个设为默认。
2. **在 `/agents` 调整 agent 阵容** —— 改种子的 system prompt 或新建。每个 agent 可绑不同 LLM（比如主持人用聪明的，参与者用便宜的）。
3. **在 `/` 新建房间** —— 选主持人、勾选参与者、写议题、设最大轮次。
4. **进房间** —— 辩论立即开始，消息流式弹出。底部输入框随时插话。
5. 主持人觉得讨论充分或到轮次上限时会自己输出 `[END]`，引擎让 ta 做最终总结，然后关闭连接。

---

## 🔧 自定义 agent —— 主持人协议

参与者的 system prompt 完全自由。**主持人** 是唯一受约束的角色：每一次发言的**最后一行**必须是下面两种之一：

- `[NEXT: <agent_id>]` —— 把话筒交给某个 agent（id 必须在房间花名册里）
- `[END]` —— 结束辩论；引擎随后请主持人做最终总结，然后关闭 WebSocket

如果你想写自己的主持人 agent，建议从 `backend/agents/personas.py::MODERATOR_SYSTEM` 复制一份作为起点。引擎还会自动给每个**非主持人** agent 注入一段"圆桌纪律"小提示，确保 ta 直接回答主持人提的具体问题、不跑题。

---

## 📡 REST API

所有接口无鉴权、纯 JSON。

| 方法 | 路径 | Body | 返回 |
|---|---|---|---|
| GET | `/api/config` | — | LLMs + 默认（api_key 已脱敏） |
| POST | `/api/config` | `{llm_configs?, default_llm_id?}` | `{ok:true}` |
| GET | `/api/agents` | — | `{agents:[...]}` |
| POST | `/api/agents` | `AgentBody` | 新建的 agent |
| PUT | `/api/agents/{id}` | `AgentBody` | 更新后的 agent |
| DELETE | `/api/agents/{id}` | — | `{ok:true}` |
| GET | `/api/rooms` | — | `{rooms:[...]}` |
| GET | `/api/rooms/{id}` | — | 房间详情 |
| POST | `/api/rooms` | `RoomBody` | 新建的房间 |
| PUT | `/api/rooms/{id}` | `RoomBody` | 更新后的房间 |
| DELETE | `/api/rooms/{id}` | — | `{ok:true}` |
| WS | `/ws/debate` | 客户端发 `{type:"start", room_id}` | 服务端推 `stream_start` / `stream_chunk` / `stream_end` / `thinking` / `message` / `done` / `error` |

WebSocket 客户端消息：
- `{type:"start", room_id}` —— 启动指定房间的一场辩论
- `{type:"user_message", text}` —— 插话，下一轮处理
- `{type:"cancel"}` —— 中止当前辩论
- `{type:"ping"}` —— 心跳

---

## 📁 项目结构

```
debate-agents-party/
├── backend/
│   ├── main.py                  FastAPI 入口 + WS 处理
│   ├── core/
│   │   ├── config_store.py      JSON 存储、schema 迁移、CRUD 工具
│   │   ├── debate_engine.py     主持人驱动的编排器
│   │   └── llm.py               OpenAI 兼容流式客户端
│   ├── agents/
│   │   └── personas.py          10 个种子 agent + 主持人 system prompt
│   └── requirements.txt
├── config.json                  ← 你的数据都在这（git 忽略）
├── config.example.json          ← 模板文件，已提交
├── web/
│   ├── server.js                Express 静态服务 + /api 代理
│   └── public/
│       ├── index.html / agents.html / room.html / config.html
│       ├── js/                  每页一个 .js（jQuery）
│       └── css/app.css
└── docker-compose.yml
```

---

## 🗺️ Roadmap

- [ ] 导出辩论记录为 Markdown / 分享链接
- [ ] 房间级模型覆盖（按场切换 LLM，而不是按 agent）
- [ ] 分支辩论（从任意消息分叉）
- [ ] 可选鉴权层
- [ ] 记忆 / RAG 接口，让 agent 引用资料

---

## 🤝 贡献

欢迎 PR。整个项目两端加起来不到 2k 行代码，看完再改也不费时间。

## 📄 License

MIT —— 见 [LICENSE](./LICENSE)。

## 🙏 鸣谢

灵感来自 [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)。流式 UX 的细节是这些年盯着 ChatGPT 看出来的。
