import type { NextConfig } from "next";

// Server-only: where the FastAPI backend listens. The browser only ever talks to this Next.js
// origin; /api/* is proxied, so the refresh-token cookie stays first-party and no CORS is needed.
const apiUrl = (process.env.API_URL ?? "http://127.0.0.1:8010").replace(/\/$/, "");

const nextConfig: NextConfig = {
  // The app renders in the browser (data is per-user and fetched client-side), so Cache
  // Components / Partial Prefetching are not used: the upgrade guide advises against enabling
  // them merely by default, and they add Suspense requirements for no benefit here.
  cacheComponents: false,
  // gzip can buffer Server-Sent Events (streamed chat answers) before flushing.
  compress: false,
  poweredByHeader: false,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiUrl}/api/:path*` }];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "no-referrer" },
          { key: "X-Frame-Options", value: "DENY" },
        ],
      },
    ];
  },
};

export default nextConfig;
