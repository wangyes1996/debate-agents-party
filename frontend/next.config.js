/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // we explicitly do NOT proxy WebSocket through Next.js -
  // browser connects directly to FastAPI on :8000 to avoid Next-WS attack surface.
};

module.exports = nextConfig;
