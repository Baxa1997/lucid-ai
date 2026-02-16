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
  //  These rewrites forward /api/ws/* traffic directly to
  //  the Python AI microservice. The Next.js dev server
  //  (and Vercel) will transparently proxy these requests,
  //  including WebSocket upgrades.
  //
  //  IMPORTANT: This bypasses the Gatekeeper auth check.
  //  Use the /api/agent/socket endpoint first to validate
  //  the session, then connect to the returned wsUrl.
  //  For production, use a SIGNED TICKET approach or
  //  validate auth in the Python WebSocket handler.
  // ─────────────────────────────────────────────────────
  async rewrites() {
    const AI_SERVICE = process.env.AI_SERVICE_URL || 'http://localhost:8000';

    return [
      // WebSocket proxy: ws://localhost:3000/api/ws/:path*
      //              →   ws://localhost:8000/ws/:path*
      {
        source: '/api/ws/:path*',
        destination: `${AI_SERVICE}/ws/:path*`,
      },

      // REST proxy: any additional AI service endpoints
      // you want to expose through the Next.js server
      // (remember to add auth middleware if needed)
      // {
      //   source: '/api/ai/:path*',
      //   destination: `${AI_SERVICE}/:path*`,
      // },
    ];
  },
};

module.exports = nextConfig;
