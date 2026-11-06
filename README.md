# DocuMind — Enterprise Document Intelligence Platform

Upload, manage, search and chat with your organization's documents (PDF, DOCX, XLSX, XLS) using
Retrieval-Augmented Generation with source citations, strict per-document permissions, and a
built-in RAG evaluation framework.

> Architecture & design: [docs/architecture.md](docs/architecture.md)

## Status

| Phase | Scope | State |
|---|---|---|
| 1 | Architecture & design | ✅ done |
| 2 | Backend foundation: FastAPI, PostgreSQL + migrations, auth (JWT + rotating refresh tokens), RBAC, organizations/users/groups/categories, document upload & management APIs, audit log, rate limiting | ✅ done |
| 3 | Ingestion pipeline: PDF/DOCX/XLSX/XLS parsing, structure- and token-aware chunking, local dense + BM25 embeddings, Qdrant indexing with ACL payloads, background worker with retries | ✅ done |
| 4 | RAG: hybrid retrieval, reranking, grounded generation with citations | ⏳ next |
| 5 | Frontend (Next.js) | — |
| 6 | Comparison, conversations, analytics, evaluation | — |
| 7 | Hardening, deployment | — |

**What works today:** registration/login/refresh/logout, role- and grant-based document access,
uploads with content validation and de-duplication, listing/search/filter, download/preview, soft
delete with purge, audit logging — and **ingestion**: every upload is parsed, chunked, embedded and
indexed in Qdrant by the worker (`uploaded → processing → indexed | failed`). Extracted content with
page / section / sheet + row locations is available at `GET /documents/{id}/chunks`.

### Ingestion at a glance

| Step | Implementation |
|---|---|
| Parse | PDF: pdfplumber (MIT) — page text + tables as Markdown, metadata, scanned-page detection · DOCX: python-docx — heading hierarchy → section paths, tables · XLSX: openpyxl · XLS: xlrd — one `Header: value` record per row |
| Chunk | LangChain `RecursiveCharacterTextSplitter` measured in **model tokens**; never crosses page/section/sheet boundaries; tables and row groups repeat their header |
| Embed | fastembed (local ONNX): `BAAI/bge-base-en-v1.5` dense (768-d) + `Qdrant/bm25` sparse — no API key needed |
| Index | Qdrant collection per embedding config (`dm_<model>_<chunking>_<hash>`), alias `chunks_active`; payload carries org, ACL tokens, page/section/sheet/rows, text |
| Safety | Points written `searchable=false`, flipped only when the whole version is stored; old version serves until the new one is complete; deletes hide vectors immediately and purge them; prompt-injection-like chunks are flagged |
| Reliability | arq worker, retries with exponential backoff for transient errors, permanent failures (encrypted/corrupt/no text) reported clearly, sweep recovers lost or stuck jobs every 5 minutes |

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

### Option A — Docker Compose (everything)

```bash
cp .env.example .env            # then set POSTGRES_PASSWORD, JWT_SECRET_KEY, ...
docker compose -f infrastructure/docker-compose.yml --env-file .env up --build
```

Starts PostgreSQL, Redis, Qdrant, runs migrations, then the API (http://localhost:8000/docs) and worker.

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

## Tests

```bash
cd backend
pytest tests/unit                                    # no services needed
TEST_DATABASE_URL=postgresql+asyncpg://documind:change-me@localhost:5432/documind_test \
TEST_QDRANT_URL=http://localhost:6333 \
RUN_MODEL_TESTS=1 \
pytest                                               # everything, incl. real-model retrieval test
```

Integration tests run the real Alembic migrations against `TEST_DATABASE_URL` and **wipe that
database**; the suite refuses to run unless the database name contains `test`. Without
`TEST_DATABASE_URL` they are skipped.

Lint: `ruff check app tests alembic`.

## API overview (v1)

Interactive docs: `/docs`. All endpoints except register/login/refresh/logout require
`Authorization: Bearer <access_token>`. Errors share one envelope:
`{"error": {"code", "message", "details", "request_id"}}`.

| Area | Endpoints |
|---|---|
| Auth | `POST /auth/register` `POST /auth/login` `POST /auth/refresh` `POST /auth/logout` `GET /auth/me` `POST /auth/change-password` |
| Organization | `GET /organization` `PATCH /organization` (admin) |
| Users (admin) | `GET/POST /users` `GET/PATCH/DELETE /users/{id}` |
| Groups | `GET /groups` `POST /groups` `GET/PATCH/DELETE /groups/{id}` `PUT /groups/{id}/members` |
| Categories | `GET/POST /categories` `PATCH/DELETE /categories/{id}` |
| Documents | `POST /documents/upload` `GET /documents` `GET/PATCH/DELETE /documents/{id}` `POST /documents/{id}/reprocess` `GET /documents/{id}/jobs` `GET /documents/{id}/download` `GET /documents/{id}/preview` `GET/PUT /documents/{id}/permissions` |

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
- **Operations:** audit log for security-relevant actions, Redis rate limiting, request ids,
  structured logs with secret redaction, security headers.
