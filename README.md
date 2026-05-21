# 🎤 Debate Agents Party

> 多智能体加密货币辩论室 — 灵感来自 [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents),精简核心思想 + Web 聊天室 UI + 用户实时插话。

![status](https://img.shields.io/badge/status-alpha-orange) ![python](https://img.shields.io/badge/python-3.12-blue) ![next](https://img.shields.io/badge/next.js-15.1.6-black)

## ✨ 它做什么

向"主持人 agent"发起一个议题(默认:**分析最新的 BTC 行情**),主持人召集多个角色 agent 围绕实时市场数据展开辩论:

| 角色 | Emoji | 立场 |
|---|---|---|
| 多头分析师 | 🐂 | 找看涨理由 |
| 空头分析师 | 🐻 | 找下行风险 |
| 技术分析师 | 📊 | 价格/成交量/支撑阻力 |
| 消息面分析师 | 📰 | 宏观/政策/ETF/巨鲸 |
| 风险官 | 🛡️ | 永远问"错了能亏多少" |
| 主持人 | 🎤 | 中立总结 + 最终决议 |

✅ 看每一轮辩论实时弹气泡(WebSocket 流式推送)
✅ 你随时在底部输入框插话,主持人会在下一轮采纳
✅ 最终决议按 **Buy / Hold / Sell + 关键 level** 输出
✅ Web 端可配置:LLM provider(OpenAI / Anthropic / DeepSeek / 火山方舟)、参与角色、轮次、数据源

## 🏗️ 架构

```
┌────────────────────────────────────────────────────┐
│  Next.js 15.1.6 前端 (port 3000)                   │
│  ├─ /      首页                                    │
│  ├─ /room  辩论聊天室(WebSocket)                  │
│  └─ /config  全栈配置                              │
└──────────────────┬─────────────────────────────────┘
                   │ HTTP + WS:8000 直连
┌──────────────────▼─────────────────────────────────┐
│  FastAPI 后端 (port 8000)                          │
│  ├─ /ws/debate         WebSocket 辩论流            │
│  ├─ /api/config        读写配置                    │
│  ├─ /api/market        Binance/CoinGecko 行情     │
│  └─ DebateEngine       多 agent 编排              │
└────────────────────────────────────────────────────┘
```

> **安全说明**:WebSocket 不经 Next.js 代理,浏览器直连 FastAPI。这样完全规避 Next.js 历史 WS 相关 CVE 的攻击面。Next.js 锁定 `15.1.6+`(避开 `<14.2` 和 `15.0.x` 已知问题)。

## 🚀 快速开始

### 方式 1:本地直跑

**后端**
```bash
cd backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python -m backend.main   # 监听 :8000
```

**前端**(另开终端)
```bash
cd frontend
npm install
npm run dev   # 打开 http://localhost:3000
```

### 方式 2:Docker Compose

```bash
docker-compose up --build
# 前端 http://localhost:3000
```

## ⚙️ 首次配置

1. 打开 http://localhost:3000/config
2. 选择 LLM Provider(默认 OpenAI),填 API Key
3. (可选)勾选要参与的 agent 角色 + 轮次
4. 保存后回到 `/room`,点 **⚔️ 开始辩论**

> **提示**:支持任何 OpenAI 兼容端点。火山方舟例子 base_url:`https://ark.cn-beijing.volces.com/api/v3`

## 🎮 使用流程

1. 在 `/room` 修改议题(默认"分析最新的 BTC 行情")
2. 点"开始辩论",主持人开场 + 拉取 Binance 实时行情
3. 5 个 agent 依次发言,主持人每轮总结
4. **底部输入框随时插话**,下一轮 agent 们会看到你的发言
5. 最后一轮结束,主持人给最终决议

## 📂 项目结构

```
debate-agents-party/
├─ backend/
│  ├─ main.py                # FastAPI 入口
│  ├─ agents/personas.py     # 角色 prompt
│  ├─ core/
│  │  ├─ debate_engine.py    # 多 agent 编排
│  │  ├─ llm.py              # OpenAI 兼容客户端
│  │  └─ config_store.py     # 配置持久化
│  └─ api/market.py          # 行情拉取
├─ frontend/
│  └─ src/app/
│     ├─ page.tsx            # 首页
│     ├─ room/page.tsx       # 聊天室
│     └─ config/page.tsx     # 配置页
└─ docker-compose.yml
```

## 🛣️ 路线图

- [ ] 流式输出(token 级气泡打字机效果)
- [ ] 历史辩论保存与回看
- [ ] 更多 agent 角色(链上分析师、衍生品分析师)
- [ ] 议题模板(BTC / ETH / 板块轮动 / 单只股票)
- [ ] Agent 之间 1v1 单挑模式

## 📝 致敬

核心多 agent 辩论思想来自 [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)([arxiv: 2412.20138](https://arxiv.org/abs/2412.20138))。本项目精简了原项目的研究图谱(15+ agent → 5 个核心角色),改为 Web 实时交互形态,并新增用户参与辩论的能力。

## 📜 License

MIT
