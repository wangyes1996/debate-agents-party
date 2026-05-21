/** Backend base URL helpers. */
export const BACKEND_HTTP =
  process.env.NEXT_PUBLIC_BACKEND_HTTP || "http://localhost:8000";

export function backendWs(path: string): string {
  if (typeof window !== "undefined" && process.env.NEXT_PUBLIC_BACKEND_WS) {
    return process.env.NEXT_PUBLIC_BACKEND_WS + path;
  }
  // derive ws://localhost:8000 from BACKEND_HTTP
  const u = new URL(BACKEND_HTTP);
  const proto = u.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${u.host}${path}`;
}

export type WsEvent =
  | { type: "message"; data: ChatMessage }
  | { type: "status"; data: { text: string; ts: number } }
  | { type: "done"; data: {} }
  | { type: "error"; data: { text: string } }
  | { type: "pong" };

export interface ChatMessage {
  id: string;
  role: string; // moderator | bull | bear | tech | news | risk | user
  name: string;
  emoji: string;
  color: string;
  content: string;
  round: number;
  ts: number;
}
