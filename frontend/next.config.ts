import type { NextConfig } from "next";

// Server-only: where the FastAPI backend listens. The browser only ever talks to this Next.js
// origin; /api/* is proxied, so the refresh-token cookie stays first-party and no CORS is needed.
// Read at BUILD time: rewrites are compiled into the build output, so a production image must be
// built with the right API_URL (Docker build arg) — changing it at runtime has no effect.
// Client IP: Next forwards an incoming X-Forwarded-For unchanged (it only sets one if absent), so
// in production an edge proxy (Caddy, see infrastructure/) must overwrite it with the real address.
const apiUrl = (process.env.API_URL ?? "http://127.0.0.1:8010").replace(/\/$/, "");

// On Vercel the default (a local address) would silently proxy every /api call into the void: fail the build instead.
if (process.env.VERCEL && !process.env.API_URL) {
  throw new Error(
    "API_URL is not set. In the Vercel project settings (Environment Variables) set API_URL to the backend's public " +
      "HTTPS address, for example https://documind-api.duckdns.org, then redeploy.",
  );
}

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

// The /api rewrite proxy buffers at most `proxyClientMaxBodySize` of a request body (Next's
// default is 10 MB) and silently truncates the rest, which corrupted every upload above 10 MB.
// Allow the backend's per-file limit (MAX_UPLOAD_SIZE_MB, read at build time like API_URL) plus
// multipart overhead; the backend still enforces the real limit.
const maxUploadMb = Number(process.env.MAX_UPLOAD_SIZE_MB) || 50;

const nextConfig: NextConfig = {
  experimental: {
    proxyClientMaxBodySize: `${maxUploadMb + 5}mb`,
    // Socket timeout of the /api proxy (default 30 s). Large uploads on slow links and long
    // streamed answers must not be cut off; the backend has its own timeouts.
    proxyTimeout: 300_000,
  },
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
