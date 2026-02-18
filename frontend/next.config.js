/** @type {import('next').NextConfig} */
const nextConfig = {
  images: {
    remotePatterns: [
      {
        protocol: 'https',
        hostname: '**',
      }
    ]
  },

  // ─────────────────────────────────────────────────────
  //  WebSocket + API Proxy Rewrites
  //
  //  Proxies /api/ws/* to the Python AI Engine's
  //  WebSocket endpoint at /api/v1/ws.
  // ─────────────────────────────────────────────────────
  async rewrites() {
    const AI_SERVICE = process.env.AI_SERVICE_URL || 'http://localhost:8000';

    return [
      // WebSocket proxy: ws://localhost:3000/api/ws
      //              →   ws://localhost:8000/api/v1/ws
      {
        source: '/api/ws/:path*',
        destination: `${AI_SERVICE}/api/v1/ws`,
      },
    ];
  },
};

module.exports = nextConfig;
