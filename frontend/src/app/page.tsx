"use client";
import Link from "next/link";

export default function Home() {
  return (
    <main className="min-h-screen flex flex-col items-center justify-center px-6">
      <div className="max-w-3xl w-full">
        <h1 className="text-5xl font-bold mb-3 bg-gradient-to-r from-violet-400 to-pink-400 bg-clip-text text-transparent">
          🎤 Debate Agents Party
        </h1>
        <p className="text-zinc-400 text-lg mb-10">
          多智能体辩论室 — 看 🐂🐻📊📰🛡️ 围绕加密行情各执一词,你随时插话。
        </p>
        <div className="grid sm:grid-cols-2 gap-4">
          <Link
            href="/room"
            className="block rounded-xl bg-gradient-to-br from-violet-600 to-purple-700 hover:from-violet-500 hover:to-purple-600 p-6 transition"
          >
            <div className="text-2xl mb-1">⚔️ 进入辩论室</div>
            <div className="text-sm text-violet-200">
              发起一场辩论,实时观看 AI 们对线
            </div>
          </Link>
          <Link
            href="/config"
            className="block rounded-xl bg-zinc-900 border border-zinc-800 hover:border-zinc-700 p-6 transition"
          >
            <div className="text-2xl mb-1">⚙️ 配置</div>
            <div className="text-sm text-zinc-400">
              LLM provider · agent 角色 · 数据源 · 轮次
            </div>
          </Link>
        </div>
        <p className="text-xs text-zinc-600 mt-10">
          灵感来自{" "}
          <a
            className="underline hover:text-zinc-400"
            href="https://github.com/TauricResearch/TradingAgents"
            target="_blank"
            rel="noreferrer"
          >
            TauricResearch/TradingAgents
          </a>
          ,精简版 + Web UI。
        </p>
      </div>
    </main>
  );
}
