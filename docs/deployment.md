# Deploying DocuMind

This guide covers running DocuMind with Docker Compose on a single Linux host. That setup suits
small and mid-sized teams; the [scaling](#scaling) section describes what to change beyond it.
Commands run from the repository root. To put a demo online with the website on Vercel and the backend on
AWS Lightsail (including the scripts, costs and safeguards for a public demo), follow
[deploy-vercel-lightsail.md](deploy-vercel-lightsail.md).

## Topology

```
 browser ──HTTPS──▶ caddy ──▶ frontend (Next.js) ──/api/*──▶ backend (FastAPI) ──▶ postgres
                    :80/:443      edge + internal networks        internal only        redis
                                                                   worker (arq) ──────▶ qdrant
```

- **Caddy** terminates TLS and is the only service with published ports. It replaces any
  client-sent `X-Forwarded-For` with the real peer address.
- The **frontend** serves the UI and proxies `/api/*` to the backend, so cookies stay first-party
  and the browser needs no CORS.
- The **backend** and **worker** run the same image. The backend trusts `X-Forwarded-For`
  (`FORWARDED_ALLOW_IPS=*`) because the network layout lets only the frontend reach it. That trust
  is what gives correct per-IP rate limits and audit IPs. Do not publish the backend's port.
- **migrate** runs `alembic upgrade head` once, before the backend and worker start.
- **Volumes:**
  - `postgres-data` (users, documents, chunks, audit log, evaluations)
  - `file-storage` (uploaded originals)
  - `qdrant-data` (vectors)
  - `redis-data`
  - `model-cache` (downloaded embedding and reranker models)
  - `caddy-data` (certificates)

## 1. Prerequisites

- Docker Engine 24+ with the Compose plugin.
- At least 4 GB of RAM: the embedding and reranker models run on the CPU, and the worker holds
  them in memory.
- About 3 GB of disk for images and models, plus your documents.
- For a public deployment: a DNS name pointing at the host, with ports 80 and 443 open (Let's
  Encrypt needs both).

## 2. Configure

```bash
cp .env.example .env
python3 -c "import secrets; print(secrets.token_hex(24))"   # run once per secret below
```

Set these in `.env`:

| Variable | Value |
|---|---|
| `POSTGRES_PASSWORD` | Random **hex** string. Compose builds the database URL from it, so it must be URL-safe. |
| `JWT_SECRET_KEY` | Random, at least 32 characters. The app refuses to start with the example placeholder. |
| `QDRANT_API_KEY` | Random. Required in production because Qdrant stores document text. |
| `SITE_ADDRESS` | Your domain (e.g. `docs.example.com`), or `localhost` for a trial run. |
| `LLM_PROVIDER`, `LLM_MODEL`, `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY` | The default chat model and the provider keys. Every provider with a key adds its models to the chat model picker. Search and ingestion work without any. To change the offered models or prices, mount your own `models.json` into the backend container and set `LLM_CATALOG_FILE`. |
| `HSTS_MAX_AGE` | `63072000` once the site runs on its real domain with a valid certificate. |

Compose forces the production settings: `ENVIRONMENT=production`, secure cookies, JSON logs, CORS
limited to `https://$SITE_ADDRESS`, and API docs off. In production the app also refuses to start
when the secrets are insecure.

## 3. Start

```bash
docker compose -f infrastructure/docker-compose.yml --env-file .env up -d --build
docker compose -f infrastructure/docker-compose.yml --env-file .env ps     # all "healthy"
```

Open `https://$SITE_ADDRESS`. The first visitor registers a new organization and becomes its admin.

On the first document upload, the worker downloads its models into `model-cache`. That takes a few
minutes; later uploads and restarts reuse the cache.

**Verify** with the smoke test. It registers a throwaway account, uploads and indexes a document,
runs a search, and checks the security headers:

```bash
pip install python-docx
python3 infrastructure/smoke_test.py https://$SITE_ADDRESS --production   # add --insecure for localhost
```

With `SITE_ADDRESS=localhost`, Caddy uses its own internal CA. Browsers will warn unless you trust
that CA, so use `--insecure` with the smoke test.

## 4. Upgrade

```bash
git pull
docker compose -f infrastructure/docker-compose.yml --env-file .env up -d --build
```

- `migrate` applies new database migrations before the new backend and worker start.
- Back up first (see below). Migrations are forward-only in production.
- If a release changes the embedding model or the chunking settings, it creates a new index. Search
  keeps using each document's current index until it is re-indexed. Re-index from the Embeddings
  page, or run:
  `docker compose ... exec backend python -m app.cli reindex --stale`.

## 5. Back up and restore

PostgreSQL and the uploaded files are the **source of truth**. Qdrant can be rebuilt from them with
`python -m app.cli reindex --all` (this re-embeds every document). Snapshot Qdrant anyway if you
want restores to be quick.

```bash
C="docker compose -f infrastructure/docker-compose.yml --env-file .env"
# Database (consistent online dump)
$C exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB"' > documind-$(date +%F).dump
# Uploaded files
docker run --rm -v documind_file-storage:/data:ro -v "$PWD":/backup alpine \
  tar czf /backup/documind-files-$(date +%F).tgz -C /data .
```

Restore into a fresh stack:

```bash
$C up -d postgres
$C exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists' < documind-YYYY-MM-DD.dump
docker run --rm -v documind_file-storage:/data -v "$PWD":/backup alpine \
  tar xzf /backup/documind-files-YYYY-MM-DD.tgz -C /data
$C up -d
$C exec backend python -m app.cli reindex --all     # rebuild vectors if qdrant-data was not restored
```

Store backups off the host and encrypt them: they contain every document.

## 6. Operate

- **Health:** inside the network, the backend serves `/health` (liveness) and `/ready`
  (PostgreSQL, Redis and Qdrant reachable); the worker answers `arq --check` (heartbeat). Compose
  uses all of these for its health checks.
- **Logs:** `$C logs -f backend worker` prints one JSON line per event. Each request is logged with
  its `request_id`, which is also returned in the `X-Request-ID` header and in every error body.
  Logs never contain document text, prompts, tokens or passwords.
- **Audit:** the `audit_logs` table records logins (including failed ones), user and role changes,
  uploads, downloads, deletions, sharing changes, settings changes and evaluation runs.
- **Failed ingestion:** see the document's *Details* tab, or retry everything with
  `$C exec backend python -m app.cli reindex --failed`. A sweep job also recovers lost or stuck jobs
  every 5 minutes.
- **Quality regressions:** run an evaluation dataset after changing models, chunking or prompts:
  `$C exec backend python -m app.cli evaluate --dataset <id> --max-drop 0.05`. It exits with 1 on a
  regression.

## Scaling

- **More ingestion throughput:**
  `$C up -d --scale worker=3`. Jobs are distributed through Redis.
- **API replicas:** the backend is stateless; sessions live in PostgreSQL and rate limits in Redis.
  Run more replicas behind the frontend, or move to an orchestrator.
- **Managed datastores:** point `DATABASE_URL`, `REDIS_URL` and `QDRANT_URL` at managed services
  and remove those services from the compose file.
- **Another proxy or load balancer in front of Caddy:** add its addresses to `trusted_proxies` in
  `infrastructure/caddy/Caddyfile`. Otherwise every request will appear to come from the load
  balancer.

## Security checklist

- [ ] Secrets are random and unique, and `.env` is readable only by the deploying user
      (`chmod 600 .env`).
- [ ] Only ports 80 and 443 are reachable from outside; the datastores have no published ports.
- [ ] `HSTS_MAX_AGE` is set once HTTPS works on the real domain.
- [ ] Backups run regularly, are encrypted, are stored off the host, and a restore has been tested.
- [ ] The host OS and Docker are kept updated, and images are rebuilt regularly for base-image fixes.
- [ ] LLM provider data handling is acceptable for your documents. With `LLM_PROVIDER=openai`,
      requests are sent with `store=false`.
