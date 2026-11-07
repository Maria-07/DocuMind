# DocuMind — Frontend

Next.js 16 (App Router, TypeScript) · Tailwind CSS v4 · shadcn/ui (Radix) · Lucide · TanStack Query.

## Run

Requires **Node 20.9+** (`.nvmrc` pins 22) and the backend API running (see the root README).

```bash
nvm use            # Node 22
npm install
npm run dev        # http://localhost:3000  (or: npm run build && npm start)
```

`API_URL` (server-side, default `http://127.0.0.1:8010`) is where `/api/*` is proxied. The browser
only talks to the Next.js origin, so the httpOnly refresh-token cookie is first-party and no CORS
is involved.

## Scripts

| Script | Purpose |
|---|---|
| `npm run dev` / `build` / `start` | Develop / production build / serve |
| `npm run typecheck` | `tsc --noEmit` |
| `npm run lint` | ESLint (Next 16 no longer lints during `next build`) |
| `npm run gen:api` | Regenerate `types/api.d.ts` from the running backend's OpenAPI schema |
| `npx playwright test` | End-to-end browser tests against the running stack (see below) |

## Structure

```
app/(auth)/        login, register (public)
app/(app)/         authenticated pages: home, documents, documents/[id], chat, chat/[id], users, settings
components/        app shell (sidebar), shared UI, shadcn/ui primitives (components/ui)
features/          per-domain React Query hooks and components (documents, chat, admin)
lib/               api client (auth + refresh), SSE parser, auth context, formatting
types/             OpenAPI-generated types + aliases
e2e/               Playwright tests
```

## Security notes

- The access token lives only in memory; the refresh token is an httpOnly, SameSite=Strict cookie
  scoped to `/api/v1/auth`. A 401 triggers one single-flight refresh, so concurrent requests can't trip
  the server's refresh-token reuse detection. Transient refresh failures (429/5xx/network) are retried
  instead of signing the user out.
- The UI hides actions a role can't perform, but **all authorization is enforced by the API**.
- Model answers are rendered as Markdown without raw HTML; external links open with
  `rel="noopener noreferrer nofollow"`. Citations link only to sources the server validated.
- Protected files (download, PDF preview) are fetched with the auth header and shown via blob URLs.

## End-to-end tests

With PostgreSQL, Redis, Qdrant, the API, the worker (and an LLM key) running and the app served on
:3000:

```bash
npx playwright install chromium   # once
npx playwright test               # register → upload → index → ask → citations → roles → mobile
```

`E2E_BASE_URL` changes the target; `E2E_SCREENSHOTS=<dir>` saves full-page screenshots of each screen.
