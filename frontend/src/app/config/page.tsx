"use client";
import { useEffect, useState } from "react";
import { BACKEND_HTTP } from "@/lib/api";

interface ProviderCfg {
  api_key: string;
  base_url: string;
  model: string;
}
interface Config {
  active_provider: string;
  providers: Record<string, ProviderCfg>;
  agents: { enabled_roles: string[]; max_rounds: number; user_can_interrupt: boolean };
  data_source: { primary: string; symbol: string };
}

const ALL_ROLES = [
  { key: "bull", emoji: "🐂", name: "多头" },
  { key: "bear", emoji: "🐻", name: "空头" },
  { key: "tech", emoji: "📊", name: "技术" },
  { key: "news", emoji: "📰", name: "消息" },
  { key: "risk", emoji: "🛡️", name: "风险" },
];

export default function ConfigPage() {
  const [cfg, setCfg] = useState<Config | null>(null);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");

  async function load() {
    const r = await fetch(`${BACKEND_HTTP}/api/config`);
    setCfg(await r.json());
  }

  useEffect(() => { load(); }, []);

  async function save() {
    if (!cfg) return;
    setSaving(true);
    setMsg("");
    // strip masked keys (***xxxx) so backend keeps existing
    const providers = JSON.parse(JSON.stringify(cfg.providers));
    const r = await fetch(`${BACKEND_HTTP}/api/config`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        active_provider: cfg.active_provider,
        providers,
        agents: cfg.agents,
        data_source: cfg.data_source,
      }),
    });
    setSaving(false);
    if (r.ok) { setMsg("✅ 已保存"); load(); } else setMsg("❌ 保存失败");
    setTimeout(() => setMsg(""), 2500);
  }

  if (!cfg) return <main className="p-8 text-zinc-500">加载中...</main>;

  function setProvider(name: string, patch: Partial<ProviderCfg>) {
    setCfg({ ...cfg!, providers: { ...cfg!.providers, [name]: { ...cfg!.providers[name], ...patch } } });
  }
  function toggleRole(r: string) {
    const cur = new Set(cfg!.agents.enabled_roles);
    cur.has(r) ? cur.delete(r) : cur.add(r);
    setCfg({ ...cfg!, agents: { ...cfg!.agents, enabled_roles: Array.from(cur) } });
  }

  const active = cfg.providers[cfg.active_provider] || { api_key: "", base_url: "", model: "" };

  return (
    <main className="min-h-screen p-6 max-w-3xl mx-auto">
      <div className="flex items-center gap-3 mb-6">
        <a href="/" className="text-zinc-400 hover:text-zinc-200">←</a>
        <h1 className="text-2xl font-bold">⚙️ 配置</h1>
        <div className="flex-1" />
        <button
          onClick={save}
          disabled={saving}
          className="px-4 py-2 rounded-lg bg-violet-600 hover:bg-violet-500 text-sm disabled:bg-zinc-700"
        >
          {saving ? "保存中..." : "💾 保存"}
        </button>
        <span className="text-sm text-zinc-400">{msg}</span>
      </div>

      {/* Provider */}
      <section className="bg-zinc-900 border border-zinc-800 rounded-xl p-5 mb-5">
        <h2 className="font-semibold mb-3 text-zinc-200">🤖 LLM Provider</h2>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-4">
          {Object.keys(cfg.providers).map((p) => (
            <button
              key={p}
              onClick={() => setCfg({ ...cfg, active_provider: p })}
              className={`px-3 py-2 rounded-lg text-sm border ${
                cfg.active_provider === p
                  ? "bg-violet-600 border-violet-500"
                  : "bg-zinc-950 border-zinc-800 hover:border-zinc-700"
              }`}
            >
              {p}
            </button>
          ))}
        </div>
        <div className="space-y-3">
          <Field label="API Key">
            <input
              type="password"
              className="input"
              value={active.api_key}
              onChange={(e) => setProvider(cfg.active_provider, { api_key: e.target.value })}
              placeholder={active.api_key.startsWith("***") ? "已保存,留空不变" : "sk-..."}
            />
          </Field>
          <Field label="Base URL (可选,空则用 SDK 默认)">
            <input
              className="input"
              value={active.base_url}
              onChange={(e) => setProvider(cfg.active_provider, { base_url: e.target.value })}
              placeholder="https://api.openai.com/v1"
            />
          </Field>
          <Field label="Model">
            <input
              className="input"
              value={active.model}
              onChange={(e) => setProvider(cfg.active_provider, { model: e.target.value })}
            />
          </Field>
        </div>
      </section>

      {/* Agents */}
      <section className="bg-zinc-900 border border-zinc-800 rounded-xl p-5 mb-5">
        <h2 className="font-semibold mb-3">👥 参与的 Agent 角色</h2>
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-2 mb-4">
          {ALL_ROLES.map((r) => (
            <button
              key={r.key}
              onClick={() => toggleRole(r.key)}
              className={`px-3 py-2 rounded-lg text-sm border ${
                cfg.agents.enabled_roles.includes(r.key)
                  ? "bg-violet-600 border-violet-500"
                  : "bg-zinc-950 border-zinc-800"
              }`}
            >
              {r.emoji} {r.name}
            </button>
          ))}
        </div>
        <Field label="辩论轮次">
          <input
            type="number"
            min={1}
            max={6}
            className="input w-24"
            value={cfg.agents.max_rounds}
            onChange={(e) =>
              setCfg({ ...cfg, agents: { ...cfg.agents, max_rounds: parseInt(e.target.value) || 1 } })
            }
          />
        </Field>
      </section>

      {/* Data source */}
      <section className="bg-zinc-900 border border-zinc-800 rounded-xl p-5 mb-5">
        <h2 className="font-semibold mb-3">📊 行情数据源</h2>
        <div className="grid grid-cols-3 gap-2 mb-3">
          {["binance", "coingecko"].map((s) => (
            <button
              key={s}
              onClick={() => setCfg({ ...cfg, data_source: { ...cfg.data_source, primary: s } })}
              className={`px-3 py-2 rounded-lg text-sm border ${
                cfg.data_source.primary === s
                  ? "bg-violet-600 border-violet-500"
                  : "bg-zinc-950 border-zinc-800"
              }`}
            >
              {s}
            </button>
          ))}
        </div>
        <Field label="Symbol (Binance: BTCUSDT)">
          <input
            className="input"
            value={cfg.data_source.symbol}
            onChange={(e) => setCfg({ ...cfg, data_source: { ...cfg.data_source, symbol: e.target.value } })}
          />
        </Field>
      </section>

      <style jsx>{`
        .input {
          background: #0b0d12;
          border: 1px solid #2a2f3a;
          border-radius: 8px;
          padding: 8px 12px;
          font-size: 14px;
          width: 100%;
          color: #e5e7eb;
        }
        .input:focus { outline: none; border-color: #8b5cf6; }
      `}</style>
    </main>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <div className="text-xs text-zinc-400 mb-1">{label}</div>
      {children}
    </label>
  );
}
