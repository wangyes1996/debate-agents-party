"use client";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { backendWs, ChatMessage, WsEvent } from "@/lib/api";

export default function Room() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [status, setStatus] = useState<string>("待开始");
  const [topic, setTopic] = useState("分析最新的 BTC 行情");
  const [running, setRunning] = useState(false);
  const [draft, setDraft] = useState("");
  const wsRef = useRef<WebSocket | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  function connect() {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) return wsRef.current;
    const ws = new WebSocket(backendWs("/ws/debate"));
    ws.onmessage = (ev) => {
      let evt: WsEvent;
      try { evt = JSON.parse(ev.data); } catch { return; }
      if (evt.type === "message") setMessages((m) => [...m, evt.data]);
      else if (evt.type === "status") setStatus(evt.data.text);
      else if (evt.type === "done") { setStatus("辩论结束 ✅"); setRunning(false); }
      else if (evt.type === "error") { setStatus("⚠️ " + evt.data.text); setRunning(false); }
    };
    ws.onclose = () => { setRunning(false); setStatus((s) => s + " (连接已关闭)"); };
    ws.onerror = () => setStatus("⚠️ WebSocket 连接错误");
    wsRef.current = ws;
    return ws;
  }

  useEffect(() => {
    return () => { wsRef.current?.close(); };
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, status]);

  function start() {
    setMessages([]);
    setStatus("连接中...");
    setRunning(true);
    const ws = connect();
    const send = () => ws.send(JSON.stringify({ type: "start", topic }));
    if (ws.readyState === WebSocket.OPEN) send();
    else ws.addEventListener("open", send, { once: true });
  }

  function stop() {
    wsRef.current?.send(JSON.stringify({ type: "cancel" }));
    setRunning(false);
    setStatus("已取消");
  }

  function sendUserMsg() {
    const text = draft.trim();
    if (!text || !wsRef.current) return;
    wsRef.current.send(JSON.stringify({ type: "user_message", text }));
    setDraft("");
  }

  return (
    <main className="min-h-screen flex flex-col">
      {/* header */}
      <header className="border-b border-zinc-800 bg-zinc-950/80 backdrop-blur sticky top-0 z-10">
        <div className="max-w-5xl mx-auto px-4 py-3 flex items-center gap-3">
          <a href="/" className="text-zinc-400 hover:text-zinc-200">←</a>
          <div className="flex-1">
            <input
              className="w-full bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-violet-500"
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              placeholder="辩论议题"
              disabled={running}
            />
          </div>
          {!running ? (
            <button
              onClick={start}
              className="px-4 py-2 rounded-lg bg-violet-600 hover:bg-violet-500 text-sm font-medium"
            >
              ⚔️ 开始辩论
            </button>
          ) : (
            <button
              onClick={stop}
              className="px-4 py-2 rounded-lg bg-red-600 hover:bg-red-500 text-sm font-medium"
            >
              ⏹ 停止
            </button>
          )}
          <a href="/config" className="text-xs text-zinc-500 hover:text-zinc-300 px-2">⚙️</a>
        </div>
        <div className="max-w-5xl mx-auto px-4 pb-2 text-xs text-zinc-500">
          状态:{status}
        </div>
      </header>

      {/* messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-6">
        <div className="max-w-5xl mx-auto space-y-4">
          {messages.length === 0 && (
            <div className="text-center text-zinc-600 py-20">
              点击右上角"开始辩论"召集 agent 们...
            </div>
          )}
          {messages.map((m) => (
            <Bubble key={m.id} m={m} />
          ))}
        </div>
      </div>

      {/* input */}
      <footer className="border-t border-zinc-800 bg-zinc-950/80 backdrop-blur sticky bottom-0">
        <div className="max-w-5xl mx-auto px-4 py-3 flex gap-2">
          <input
            className="flex-1 bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-violet-500"
            placeholder={running ? "插话(主持人会在下一轮纳入你的发言)..." : "辩论未开始"}
            value={draft}
            disabled={!running}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") sendUserMsg(); }}
          />
          <button
            disabled={!running || !draft.trim()}
            onClick={sendUserMsg}
            className="px-4 py-2 rounded-lg bg-violet-600 hover:bg-violet-500 disabled:bg-zinc-800 disabled:text-zinc-500 text-sm"
          >
            发送
          </button>
        </div>
      </footer>
    </main>
  );
}

function Bubble({ m }: { m: ChatMessage }) {
  const isUser = m.role === "user";
  return (
    <div className={`bubble flex gap-3 ${isUser ? "flex-row-reverse" : ""}`}>
      <div
        className="w-9 h-9 rounded-full flex items-center justify-center text-lg flex-shrink-0"
        style={{ background: m.color + "33", border: `1px solid ${m.color}66` }}
      >
        {m.emoji}
      </div>
      <div className={`max-w-[80%] ${isUser ? "text-right" : ""}`}>
        <div className="text-xs text-zinc-500 mb-1">
          <span style={{ color: m.color }}>{m.name}</span>
          {m.round > 0 && <span className="ml-2">第 {m.round} 轮</span>}
        </div>
        <div
          className="rounded-2xl px-4 py-3 text-sm leading-relaxed prose prose-invert prose-sm max-w-none"
          style={{
            background: isUser ? "#3f1d6b" : "#16181f",
            border: `1px solid ${isUser ? "#5b2ea3" : "#262932"}`,
          }}
        >
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
        </div>
      </div>
    </div>
  );
}
