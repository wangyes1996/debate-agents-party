// Minimal static + thin proxy server for debate-agents-party.
// Serves /public as the SPA and proxies /api/* to the FastAPI backend.
// WebSocket goes browser -> :8000 directly (no proxy).

const express = require("express");
const http = require("http");

const PORT = process.env.PORT || 3000;
const BACKEND = process.env.BACKEND || "http://127.0.0.1:8000";

const app = express();
app.use(express.json({ limit: "1mb" }));

// Tiny manual proxy for /api/* -> backend (so the browser can use same-origin URLs).
app.all(/^\/api\/.*/, (req, res) => {
  const url = new URL(BACKEND);
  const opts = {
    host: url.hostname,
    port: url.port || 80,
    path: req.originalUrl,
    method: req.method,
    headers: { ...req.headers, host: `${url.hostname}:${url.port}` },
  };
  const upstream = http.request(opts, (r) => {
    res.writeHead(r.statusCode || 502, r.headers);
    r.pipe(res);
  });
  upstream.on("error", (e) => {
    res.status(502).json({ error: "backend_unreachable", detail: e.message });
  });
  if (req.method !== "GET" && req.method !== "HEAD") {
    upstream.write(JSON.stringify(req.body || {}));
  }
  upstream.end();
});

// Static SPA. Serve /public, with HTML pages at /, /room, /config.
app.use(express.static(__dirname + "/public", { extensions: ["html"] }));

app.get("/", (_, res) => res.sendFile(__dirname + "/public/index.html"));
app.get("/room", (_, res) => res.sendFile(__dirname + "/public/room.html"));
app.get("/config", (_, res) => res.sendFile(__dirname + "/public/config.html"));

app.listen(PORT, "0.0.0.0", () => {
  console.log(`web ready → http://0.0.0.0:${PORT}  (backend=${BACKEND})`);
});
