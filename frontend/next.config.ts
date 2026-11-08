import type { NextConfig } from "next";

// Server-only: where the FastAPI backend listens. The browser only ever talks to this Next.js
// origin; /api/* is proxied, so the refresh-token cookie stays first-party and no CORS is needed.
// Read at BUILD time: rewrites are compiled into the build output, so a production image must be
// built with the right API_URL (Docker build arg) — changing it at runtime has no effect.
// Client IP: Next forwards an incoming X-Forwarded-For unchanged (it only sets one if absent), so
// in production an edge proxy (Caddy, see infrastructure/) must overwrite it with the real address.
const apiUrl = (process.env.API_URL ?? "http://127.0.0.1:8010").replace(/\/$/, "");

// Production CSP. Next's App Router bootstraps with inline scripts, so script-src needs
// 'unsafe-inline' unless every response carries a nonce (that would require a proxy.ts, which
// also caps request bodies — uploads stream through /api). The policy still blocks third-party
// script/style/connect origins, framing of the app, <base> injection and foreign form targets.
// blob: covers the in-app PDF preview (a blob URL inherits this policy, incl. object-src).
const csp = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  "connect-src 'self'",
  "frame-src 'self' blob:",
  "object-src 'self' blob:",
  "worker-src 'self' blob:",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "no-referrer" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=(), usb=()" },
  // Dev (next dev / Turbopack HMR) needs eval and websockets, so the CSP is production-only.
  ...(process.env.NODE_ENV === "production" ? [{ key: "Content-Security-Policy", value: csp }] : []),
];

const nextConfig: NextConfig = {
  // Self-contained server bundle for the Docker image (frontend/Dockerfile sets NEXT_OUTPUT).
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
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
        headers: securityHeaders,
      },
    ];
  },
};

export default nextConfig;
