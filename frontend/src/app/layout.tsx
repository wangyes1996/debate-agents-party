import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Debate Agents Party",
  description: "Multi-agent crypto debate room - inspired by TauricResearch/TradingAgents",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh">
      <body>{children}</body>
    </html>
  );
}
