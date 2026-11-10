# DocuMind — Enterprise Document Intelligence Platform

[![CI](https://github.com/Maria-07/DocuMind/actions/workflows/ci.yml/badge.svg)](https://github.com/Maria-07/DocuMind/actions/workflows/ci.yml)

Upload, manage, search and chat with your organization's documents (PDF, DOCX, XLSX, XLS) using
Retrieval-Augmented Generation with source citations, strict per-document permissions, and a
built-in RAG evaluation framework.

> User manual: [docs/user-manual.md](docs/user-manual.md) · Architecture & design: [docs/architecture.md](docs/architecture.md) · Deployment & operations: [docs/deployment.md](docs/deployment.md) · Public demo on Vercel + AWS Lightsail: [docs/deploy-vercel-lightsail.md](docs/deploy-vercel-lightsail.md)

## Status

| Phase | Scope                                                                                                                                                                                                                                                                                                                                                                             | State   |
| ----- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------- |
| 1     | Architecture & design                                                                                                                                                                                                                                                                                                                                                             | ✅ done |
| 2     | Backend foundation: FastAPI, PostgreSQL + migrations, auth (JWT + rotating refresh tokens), RBAC, organizations/users/groups/categories, document upload & management APIs, audit log, rate limiting                                                                                                                                                                              | ✅ done |
| 3     | Ingestion pipeline: PDF/DOCX/XLSX/XLS parsing, structure- and token-aware chunking, local dense + BM25 embeddings, Qdrant indexing with ACL payloads, background worker with retries                                                                                                                                                                                              | ✅ done |
| 4     | RAG: permission-aware hybrid retrieval, cross-encoder reranking, grounded streaming answers with validated citations, conversations, search API, retrieval-accuracy benchmark; Anthropic or OpenAI (e.g. gpt-5-nano)                                                                                                                                                              | ✅ done |
| 5     | Frontend: Next.js 16 dashboard — auth, documents (drag & drop upload, live status, extracted content, PDF preview), streaming AI chat with clickable citations, user management, settings; responsive; Playwright e2e                                                                                                                                                             | ✅ done |
| 6     | Grounded document comparison, RAG evaluation module (datasets, LLM judge, regression gating, trends), organization analytics, embedding/re-index management, document sharing & groups UI                                                                                                                                                                                         | ✅ done |
| 7     | Hardening & deployment: Docker images (non-root, standalone Next.js), Compose stack behind Caddy (TLS, real client IP, only 80/443 published), production config guards, CSP, per-account login limits, per-document redaction in evaluations, pinned dependencies + audits, strict typing (mypy), CI with a containerized end-to-end smoke test, deployment/backup/upgrade guide | ✅ done |

**What works today:** registration/login/refresh/logout, role- and grant-based document access,
uploads with content validation and de-duplication, listing/search/filter, download/preview, soft
delete with purge, audit logging — and **ingestion**: every upload is parsed, chunked, embedded and
indexed in Qdrant by the worker (`uploaded → processing → indexed | failed`). Extracted content with
page / section / sheet + row locations is available at `GET /documents/{id}/chunks`.

**Phase 6 adds** side-by-side comparison of 2–5 documents, a RAG evaluation module with
regression gating, an analytics dashboard, embedding-config management with re-indexing, and
sharing dialogs (users/groups, view/manage) — see _Comparison, evaluation & analytics_ below.

### Ingestion at a glance

| Step        | Implementation                                                                                                                                                                                                                |
| ----------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Parse       | PDF: pdfplumber (MIT) — page text + tables as Markdown, metadata, scanned-page detection · DOCX: python-docx — heading hierarchy → section paths, tables · XLSX: openpyxl · XLS: xlrd — one `Header: value` record per row    |
| Chunk       | LangChain `RecursiveCharacterTextSplitter` measured in **model tokens**; never crosses page/section/sheet boundaries; tables and row groups repeat their header                                                               |
| Embed       | fastembed (local ONNX): `BAAI/bge-base-en-v1.5` dense (768-d) + `Qdrant/bm25` sparse — no API key needed                                                                                                                      |
| Index       | Qdrant collection per embedding config (`dm_<model>_<chunking>_<hash>`), alias `chunks_active`; payload carries org, ACL tokens, page/section/sheet/rows, text                                                                |
| Safety      | Points written `searchable=false`, flipped only when the whole version is stored; old version serves until the new one is complete; deletes hide vectors immediately and purge them; prompt-injection-like chunks are flagged |
| Reliability | arq worker, retries with exponential backoff for transient errors, permanent failures (encrypted/corrupt/no text) reported clearly, sweep recovers lost or stuck jobs every 5 minutes                                         |

### Retrieval & answers at a glance

| Step            | Implementation                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Route           | Every message is routed first. Unmistakable greetings / thanks / goodbyes are recognised by rule (no model call); anything else gets one small structured-output call that picks **chat** (greeting, "what can you do?", general help: answered by the assistant with _no_ document access, no search, no citations) or **documents** (the pipeline below) and rewrites a follow-up into a standalone search query. A routing failure falls back to a document search; the router errs towards documents when unsure (`app/rag/routing.py`) |
| Retrieve        | Dense + BM25 in one Qdrant query, fused with RRF. The filter (org, searchable, ACL tokens, optional scope) is built only from the server-side user — never from request input                                                                                                                                                                                                                                                                                                                                                               |
| Re-check        | Every hit is re-verified in PostgreSQL (document live, user may view it, active index version), so a stale vector payload can only narrow results                                                                                                                                                                                                                                                                                                                                                                                           |
| Rerank          | `BAAI/bge-reranker-base` cross-encoder on the top 20; relevance threshold decides "not found" (the LLM is then not called); max 4 chunks per document; token budget                                                                                                                                                                                                                                                                                                                                                                         |
| Generate        | `LLM_PROVIDER=anthropic` (default `claude-opus-5-5`): each chunk is a citable `document` (one block per sentence/row) using Claude's native citations, so citations point at exact passages; system prompt cached; refusal fallback. `LLM_PROVIDER=openai` (e.g. `gpt-5-nano`): Responses API with `store=False`; numbered `<source>` blocks cited with inline `[n]` markers (per-chunk granularity). Both stream                                                                                                                           |
| Cite            | Every citation is validated against the chunk it claims to quote; invalid ones are dropped and counted. Answers carry `[n]` markers; citations include document, page / section / sheet + rows, snippet and a link                                                                                                                                                                                                                                                                                                                          |
| Voice           | One persona, `ASSISTANT_SYSTEM_PROMPT` (friendly, brief, honest about what it knows and can do), is shared by every reply; the grounded-answer prompts extend it with the citation rules. General-knowledge answers are labelled as such and never presented as document facts; a document-less reply never states facts about the organization (`app/rag/generation/prompts.py`, versioned by `PROMPT_VERSION`)                                                                                                                            |
| Models & cost   | Every message can pick its model from a catalog (`app/rag/generation/models.json`: OpenAI, Anthropic and Google; a model is offered when its provider key is set). Each reply stores its token usage and an **estimated cost** (provider-reported tokens x the catalog's prices in force that day, with cache and reasoning tokens priced correctly), including the small routing call, which runs on `LLM_ROUTER_MODEL` or the default model. Prices are estimates: edit the catalog when they change                                      |
| Edit & resubmit | A user message can be edited and resubmitted (optionally with another model). It and everything after it are marked superseded, hidden from the conversation and from the model's context, and kept in the database; the new message takes their place                                                                                                                                                                                                                                                                                      |
| Safety          | Retrieved text is data, never instructions (prompt + no tools); injection-like chunks are flagged to the model; document facts and "**Interpretation:**" are kept apart                                                                                                                                                                                                                                                                                                                                                                     |

**Measured retrieval accuracy** (`tests/integration/test_retrieval_accuracy.py`: 12 paraphrased
questions over 4 documents / 35 chunks with near-miss distractors, real models):

| Configuration                        | Recall@1 | Recall@5 | MRR      | NDCG@5   |
| ------------------------------------ | -------- | -------- | -------- | -------- |
| semantic                             | 0.92     | 1.00     | 0.94     | 0.96     |
| hybrid                               | 0.83     | 1.00     | 0.89     | 0.92     |
| hybrid + bge-reranker-base (default) | 0.92     | 1.00     | **0.96** | **0.97** |

Rerankers compared on the same set: MiniLM-L-12 MRR 0.92, jina-turbo 0.62, bge-reranker-base 0.96.
The set is small (each question ≈ 0.08 Recall@1): it guards against regressions, the Phase 6
evaluation module measures quality on real document sets.

### Comparison, evaluation & analytics at a glance

| Feature     | Implementation                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| ----------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Compare     | Each document contributes its own sources (chunks relevant to the optional focus question, else its opening content), so a large document cannot crowd out the others. One structured-output call (JSON schema) returns aspects × documents with `present / absent / unclear`, a difference summary and a conflict flag. Server-side validation keeps only citations of the finding's own document; a "present" finding without valid evidence is downgraded to "unclear". Access is checked per document |
| Evaluate    | Datasets of test cases (question, optional reference answer, relevant documents, **evidence quotes** that are resolved to the chunks of the config under test, so labels survive re-chunking). A run executes every case through the **real** retrieval + answer path on the worker, as the user who started it                                                                                                                                                                                           |
| Metrics     | Retrieval: Precision@5, Recall@5, MRR, NDCG@5, document recall. Answer (LLM judge, versioned rubric `judge-v2`, structured output, rationale stored): faithfulness, hallucination rate, answer relevance, context relevance, correctness vs. reference. Deterministic: citation accuracy, abstention accuracy (says "not found" exactly when it should), latency                                                                                                                                          |
| Regressions | Each run records its full configuration (models, chunking, thresholds, prompt and judge versions) and is compared with the previous completed run; the UI flags drops > 5 pts and `python -m app.cli evaluate` exits non-zero for CI                                                                                                                                                                                                                                                                      |
| Analytics   | Admin dashboard from real data: document counts, uploads vs. indexed per day, failures, questions per day, "not found" rate, retrieval latency by stage (p50/p95), grounded-answer rate, tokens                                                                                                                                                                                                                                                                                                           |
| Embeddings  | Configurations with document / chunk / vector counts; re-index stale, failed or all documents (old index keeps serving until each new version is complete)                                                                                                                                                                                                                                                                                                                                                |

```bash
# Evaluate a dataset from the CLI (e.g. in CI after changing models, chunking or prompts)
python -m app.cli evaluate --dataset <uuid> --fail-under retrieval.ndcg@5=0.7     --fail-under answer.faithfulness=0.9 --max-drop 0.05      # exit 1 on regression
```

The judge uses `LLM_MODEL` unless `EVAL_JUDGE_MODEL` names another model of the same provider.

**Chat API:** `POST /chat/conversations` → `POST /chat/conversations/{id}/messages` with
`Accept: text/event-stream` (body: `content`, optional `model` from `GET /chat/models`, optional `edit_message_id`) streams `meta`, `status` (`thinking`, then `retrieving` for document questions, then `generating`),
`sources`, `token`, `citation`, `done` (with `route`: `chat` or `documents`) (or `error`) events; without it the complete answer is returned as JSON. Conversations are private to
their owner. **Search API:** `POST /search/hybrid`, `/search/semantic`, `/search/documents`.

## Repository layout

```
backend/          FastAPI app, arq worker, Alembic migrations, tests
  app/api/        HTTP routers (v1) and dependencies (auth, permissions, rate limits)
  app/core/       config, security, logging, errors, middleware, permissions
  app/models/     SQLAlchemy models          app/schemas/   Pydantic request/response models
  app/repositories/  org-scoped queries      app/services/  use cases & access policy
  app/workers/    background tasks (arq)
infrastructure/   docker-compose.yml, Qdrant config
docs/             architecture & design
```

## Running locally

### Option A — Docker Compose (production-like, everything)

```bash
cp .env.example .env     # set POSTGRES_PASSWORD (hex), JWT_SECRET_KEY, QDRANT_API_KEY, SITE_ADDRESS, LLM key
docker compose -f infrastructure/docker-compose.yml --env-file .env up -d --build
```

Builds the images, runs migrations and starts PostgreSQL, Redis, Qdrant, the API, the worker, the
Next.js frontend and Caddy. Open **https://localhost** (or your `SITE_ADDRESS`). Only Caddy
publishes ports. See [docs/deployment.md](docs/deployment.md) for TLS, backups, upgrades and scaling.
To run just the datastores in Docker and the app natively, add
`-f infrastructure/docker-compose.datastores.yml` and start `postgres redis qdrant`.

### Option B — Native (no Docker)

Requirements: Python 3.12+, PostgreSQL 15+, Redis 6+, and Qdrant 1.20 (native binary).

**Qdrant (native, Windows):** download `qdrant-x86_64-pc-windows-msvc.zip` for **v1.20.0** from
[github.com/qdrant/qdrant/releases](https://github.com/qdrant/qdrant/releases) (check its SHA-256
against the digest shown on the release page), extract `qdrant.exe` into `infrastructure/qdrant/bin/`, then:

```powershell
powershell -ExecutionPolicy Bypass -File infrastructure\qdrant\start-qdrant.ps1
```

It listens on `127.0.0.1:6333` (HTTP; dashboard at `/dashboard`) and `:6334` (gRPC), stores data in
`infrastructure/qdrant/storage/`, and uses `QDRANT_API_KEY` from `.env` if set. Config:
`infrastructure/qdrant/local.yaml`. On macOS/Linux use the matching release archive with the same config.

```bash
# 1. Database (psql as a superuser)
CREATE ROLE documind LOGIN PASSWORD 'change-me';
CREATE DATABASE documind OWNER documind;
CREATE DATABASE documind_test OWNER documind;   -- for integration tests

# 2. Backend
cd backend
python -m venv .venv
.venv/Scripts/activate            # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements-dev.txt
cp ../.env.example ../.env         # edit DATABASE_URL, JWT_SECRET_KEY, ...

alembic upgrade head
uvicorn app.main:create_app --factory --reload          # API on http://localhost:8000
arq app.workers.settings.WorkerSettings                  # worker (separate terminal)
```

The worker downloads the embedding models into `MODEL_CACHE_DIR` (default `backend/.models`,
~220 MB) on its first start, then registers the active embedding config, creates the Qdrant
collection and points the `chunks_active` alias at it.

After changing `EMBEDDING_MODEL`, `SPARSE_MODEL`, `CHUNK_SIZE_TOKENS` or `CHUNK_OVERLAP_TOKENS`
(which creates a new collection), re-index:

```bash
python -m app.cli reindex --stale     # documents not on the active embedding config
python -m app.cli reindex --failed    # retry failed documents
python -m app.cli reindex --all       # everything (e.g. after a parser improvement)
```

Health: `GET /health` (liveness), `GET /ready` (PostgreSQL, Redis, Qdrant reachability).

```bash
# 3. Frontend (Node 20.9+; `nvm use` picks 22 from frontend/.nvmrc)
cd frontend
npm install
npm run dev                                              # http://localhost:3000
```

The frontend proxies `/api/*` to the API (`API_URL`, default `http://127.0.0.1:8010`), so open the
app at http://localhost:3000, create a workspace, upload documents and ask questions. See
[frontend/README.md](frontend/README.md) for scripts, structure and the Playwright e2e tests.

## Tests

```bash
cd backend
pytest tests/unit                                    # no services needed
TEST_DATABASE_URL=postgresql+asyncpg://documind:change-me@localhost:5432/documind_test \
TEST_QDRANT_URL=http://localhost:6333 \
RUN_MODEL_TESTS=1 \
pytest                                               # everything, incl. real-model retrieval tests

# Real answers from the configured LLM (needs its API key; cents per run):
RUN_LLM_TESTS=1 TEST_QDRANT_URL=http://localhost:6333 pytest tests/integration/test_real_answers.py -s
#   uses LLM_PROVIDER / LLM_MODEL / OPENAI_API_KEY or ANTHROPIC_API_KEY from the environment
```

Integration tests run the real Alembic migrations against `TEST_DATABASE_URL` and **wipe that
database**; the suite refuses to run unless the database name contains `test`. Without
`TEST_DATABASE_URL` they are skipped.

Lint & types: `ruff check app tests alembic` · `ruff format --check app tests alembic` · `mypy app`.
Dependencies are pinned in `backend/constraints.txt` (`pip install -r requirements-dev.txt -c constraints.txt`).

Browser tests (Playwright, against a running stack; the production build is checked for CSP violations):
`cd frontend && npx playwright test`. Whole-stack smoke test (any deployment):
`python infrastructure/smoke_test.py https://localhost --insecure --production`.

**CI** (`.github/workflows/ci.yml`) runs on every push: backend lint, format, mypy, migrations
check, the full unit + integration suite against PostgreSQL and Qdrant, dependency audits; frontend
lint, typecheck, build; then builds the Docker images, starts the whole stack and runs the smoke test
through Caddy — including a check that a spoofed `X-Forwarded-For` is not recorded as the client IP.

## API overview (v1)

Interactive docs: `/docs`. All endpoints except register/login/refresh/logout require
`Authorization: Bearer <access_token>`. Errors share one envelope:
`{"error": {"code", "message", "details", "request_id"}}`.

| Area                 | Endpoints                                                                                                                                                                                                                                                                                        |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Auth                 | `POST /auth/register` `POST /auth/login` `POST /auth/refresh` `POST /auth/logout` `GET /auth/me` `POST /auth/change-password`                                                                                                                                                                    |
| Organization         | `GET /organization` `PATCH /organization` (admin)                                                                                                                                                                                                                                                |
| Users (admin)        | `GET/POST /users` `GET/PATCH/DELETE /users/{id}`                                                                                                                                                                                                                                                 |
| Groups               | `GET /groups` `POST /groups` `GET/PATCH/DELETE /groups/{id}` `PUT /groups/{id}/members`                                                                                                                                                                                                          |
| Categories           | `GET/POST /categories` `PATCH/DELETE /categories/{id}`                                                                                                                                                                                                                                           |
| Organization members | `GET /organization/members` (any member; id, name, e-mail — for share dialogs)                                                                                                                                                                                                                   |
| Documents            | `POST /documents/upload` `GET /documents` `GET/PATCH/DELETE /documents/{id}` `POST /documents/{id}/reprocess` `GET /documents/{id}/jobs` `GET /documents/{id}/download` `GET /documents/{id}/preview` `GET/PUT /documents/{id}/permissions`                                                      |
| Chat & search        | `GET /chat/models` `GET/POST /chat/conversations` `GET/PATCH/DELETE /chat/conversations/{id}` `POST /chat/conversations/{id}/messages` (SSE) `POST /search/hybrid` `/search/semantic` `/search/documents`                                                                                        |
| Comparisons          | `POST /comparisons` `GET /comparisons` `GET /comparisons/{id}`                                                                                                                                                                                                                                   |
| Evaluations          | `GET/POST /evaluations/datasets` `DELETE /evaluations/datasets/{id}` `GET/POST /evaluations/datasets/{id}/cases` `DELETE …/cases/{case_id}` `POST /evaluations/run` `GET /evaluations/results` `GET /evaluations/results/{id}` `GET /evaluations/metrics` (view: managers & admins; run: admins) |
| Analytics (admin)    | `GET /analytics/overview` `GET /analytics/documents?days=` `GET /analytics/retrieval?days=`                                                                                                                                                                                                      |
| Embeddings (admin)   | `GET /embeddings` `POST /embeddings/reindex` (`stale` / `failed` / `all`)                                                                                                                                                                                                                        |

## Security model (summary)

- **Auth:** Argon2id password hashes; 15-min JWT access tokens (kept in memory by the client);
  7-day opaque refresh tokens in an `httpOnly`, `SameSite=Strict` cookie scoped to `/api/v1/auth`,
  rotated on every use, stored only as SHA-256, with reuse detection that revokes the session family.
  Login lockout after repeated failures; generic errors prevent account enumeration via login.
- **Authorization:** roles map to permissions (`admin`, `manager`, `user`). The principal is rebuilt
  from the database on every request, so role changes and deactivation apply immediately.
- **Document access:** visibility `private` / `organization` / `restricted` + user/group grants.
  Enforced in the service layer and in SQL for listings (both tested for equivalence). Other tenants'
  ids return 404. The same rules are encoded as organization-qualified ACL tokens for Qdrant (Phase 3).
- **Uploads:** extension allow-list + content sniffing, macro rejection, zip-bomb guard, size/count
  limits enforced before parsing, files stored outside any web root under random keys.
- **Evaluations:** results and test cases are redacted per viewer — answers, citations, judge notes,
  reference answers and quotes drawn from documents the viewer cannot see are withheld (metrics stay).
- **Edge & network:** TLS at Caddy; only 80/443 published; the backend is reachable from the
  frontend alone, which is what makes trusting `X-Forwarded-For` (set by Caddy) safe. Login is
  rate-limited per IP **and** per target account, so rotating IPs does not help an attacker.
- **Headers:** CSP (no third-party origins, no framing, `object-src`/`frame-src` limited to self and
  blob: for the PDF preview), `X-Frame-Options`, `nosniff`, `Referrer-Policy: no-referrer`,
  `Permissions-Policy`; HSTS at the edge and on the API in production.
- **Production guards:** the API refuses to start with insecure settings (non-secure cookies,
  wildcard CORS, the example JWT secret, no Qdrant API key); API docs are off in production.
- **Operations:** audit log for security-relevant actions, Redis rate limiting, request ids,
  structured JSON logs with secret redaction, non-root containers, pinned dependencies with
  `pip-audit` / `npm audit` in CI.
