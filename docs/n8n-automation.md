# Document processing automation with n8n

DocuMind can hand the *orchestration* of document processing to [n8n](https://n8n.io): it is told about
every upload, asks the backend to start processing, watches the job, retries temporary failures with
exponential backoff, records the outcome and sends an optional notification. The feature is **off by
default**. With `N8N_ENABLED=false` nothing described here applies and uploads work exactly as before.

- [How it works](#how-it-works) · [Setup](#setup) · [Settings](#settings) · [The workflow](#the-workflow)
- [Internal API](#internal-api) · [Statuses](#statuses) · [Failures and retries](#failures-and-retries)
- [Security](#security) · [What the user sees](#what-the-user-sees) · [Troubleshooting](#troubleshooting)
- [Test results](#test-results) · [Fetching linked pages (not part of this)](#not-in-scope)

## How it works

```
 browser ─▶ FastAPI ──(1) validate, store file, save metadata, create job ───────────────▶ PostgreSQL
              │
              │ (2) webhook: ids and metadata only (never file contents)
              ▼
             n8n  ──(3) POST /internal/documents/{id}/process ─▶ FastAPI ─▶ queue ─▶ worker
              │                                                                       │ the existing pipeline:
              │ (4) wait, then GET /internal/jobs/{id}/status  (poll)                 │ parse, hyperlinks, chunk,
              │ (5) temporary failure? wait (exponential backoff), POST …/retry       │ embed, Qdrant, PostgreSQL
              │ (6) POST …/workflow-status  (record the outcome)                      ▼
              └─(7) optional notification (Slack-compatible webhook)             status in PostgreSQL
```

| FastAPI (unchanged responsibilities) | n8n (new) |
|---|---|
| File validation and storage, parsing (PDF, DOCX, Excel), hyperlink extraction, chunking, embeddings, Qdrant, PostgreSQL, authentication, organization-level access control, **the job and document status** | Receiving the upload event, triggering processing, polling, waiting between retries, success and failure branches, notifications, its own execution log |

**PostgreSQL is the single source of truth.** n8n keeps no state it could not rebuild from
`GET /internal/jobs/{id}/status`: the retry limit and backoff delay are decided by the backend and
returned in that response, so the workflow has nothing to keep in sync.

**No retries in two places.** A job started by the workflow makes one attempt in the worker; after a
temporary failure it is marked `failed` + `retryable` and *n8n* decides when to retry. Every other job
(workflow off, bulk re-index) keeps the worker's own retries. The failed attempt's partial output is
removed first, so a retry never duplicates chunks or vectors.

**n8n being down never stops uploads.** If the webhook cannot be delivered (n8n stopped, workflow
inactive), the backend queues the job for the worker directly, as if n8n were off. The scheduled sweep
also takes back any job n8n never picked up (after 10 minutes), and fails any job whose retry has been
pending for `N8N_STALL_MINUTES` because the workflow disappeared.

## Setup

1. **Create the secrets** (one command per value) and put them in `.env`:

   ```bash
   python3 -c "import secrets; print(secrets.token_hex(32))"
   ```

   ```env
   N8N_ENABLED=true
   INTERNAL_API_KEY=<random>        # n8n -> backend
   N8N_WEBHOOK_SECRET=<random>      # backend -> n8n
   N8N_ENCRYPTION_KEY=<random>      # n8n's own credential encryption: keep it safe, never change it
   ```

   With `N8N_ENABLED=true` the backend refuses to start unless `INTERNAL_API_KEY` and
   `N8N_WEBHOOK_SECRET` are set and at least 24 characters long.

2. **Start the stack with the automation profile** (n8n is not part of the default stack):

   ```bash
   docker compose -f infrastructure/docker-compose.yml --env-file .env --profile automation up -d --build
   ```

   The n8n container refuses to start with a clear message if one of the three secrets is missing.

3. **Create the n8n owner account.** Open the editor at **http://localhost:5678** (only reachable from
   this machine: the port is bound to `127.0.0.1`; change it with `N8N_LOCAL_PORT` if 5678 is taken) and
   fill in the one-time owner form.

4. **Import and activate the workflow** (the file is mounted into the container at `/workflows`):

   ```bash
   docker compose -f infrastructure/docker-compose.yml --env-file .env --profile automation exec n8n \
     n8n import:workflow --input=/workflows/documind-document-processing.workflow.json
   docker compose -f infrastructure/docker-compose.yml --env-file .env --profile automation exec n8n \
     n8n list:workflow                       # prints  <id>|DocuMind - Document Processing Automation
   docker compose -f infrastructure/docker-compose.yml --env-file .env --profile automation exec n8n \
     n8n update:workflow --id=<id> --active=true
   docker compose -f infrastructure/docker-compose.yml --env-file .env --profile automation restart n8n
   ```

   Or in the editor: **Workflows -> Import from file** (`infrastructure/n8n/documind-document-processing.workflow.json`),
   then switch **Active** on. Only an *active* workflow has a production webhook; an inactive one makes the
   backend fall back to direct processing (and logs `n8n_webhook_rejected` with status 404).

5. **Check it:** upload a document. In the n8n editor, **Executions** shows one run per upload; in
   DocuMind the document goes *uploaded -> queued -> processing -> completed*.

Local, native setup (no Docker for the app): run n8n any way you like, set `N8N_WEBHOOK_URL` to its
production webhook URL (`http://127.0.0.1:5678/webhook/documind-document-uploaded`), and give n8n the
variables listed below (`DOCUMIND_API_URL` pointing at the API, for example `http://host.docker.internal:8010`
when n8n runs in Docker and the API on the host).

## Settings

Backend (`.env`, see `.env.example`):

| Variable | Default | Meaning |
|---|---|---|
| `N8N_ENABLED` | `false` | Master switch. Off: uploads go straight to the worker and `/internal` answers 404 |
| `INTERNAL_API_KEY` | | Secret n8n sends as `X-Internal-Api-Key` on every `/internal` call |
| `N8N_WEBHOOK_SECRET` | | Secret the backend sends as `X-DocuMind-Webhook-Secret`; the workflow rejects events without it |
| `N8N_WEBHOOK_URL` | `http://n8n:5678/webhook/documind-document-uploaded` | Where upload events are posted (n8n's service name on the Docker network) |
| `N8N_MAX_RETRIES` | `3` | Retries per job after a temporary failure (`0` = never retry) |
| `N8N_RETRY_BASE_SECONDS` | `30` | First backoff; doubles each retry (30, 60, 120 ...) |
| `N8N_RETRY_MAX_SECONDS` | `600` | Backoff cap |
| `N8N_STALL_MINUTES` | `30` | A retry that never arrives is finalized as failed after this long |
| `N8N_INCLUDE_TITLE_IN_EVENTS` | `false` | Send the document title in events. Off by default: titles can be sensitive and n8n stores execution data |
| `N8N_NOTIFY_WEBHOOK_URL` | | Optional Slack-compatible incoming-webhook URL for notifications |

n8n container (set from the same `.env`; the compose service maps them to the names the workflow reads):

| Variable (`.env`) | Reaches the workflow as | Default |
|---|---|---|
| `N8N_ENCRYPTION_KEY` | n8n's `N8N_ENCRYPTION_KEY` | required |
| `INTERNAL_API_KEY` | `DOCUMIND_INTERNAL_API_KEY` | required |
| `N8N_WEBHOOK_SECRET` | `DOCUMIND_WEBHOOK_SECRET` | required |
| `N8N_BACKEND_URL` | `DOCUMIND_API_URL` | `http://backend:8000` |
| `N8N_POLL_SECONDS` | `DOCUMIND_POLL_SECONDS` | `5` |
| `N8N_MAX_POLLS` | `DOCUMIND_MAX_POLLS` | `720` (one hour at 5 s) |
| `N8N_NOTIFY_WEBHOOK_URL` | `NOTIFY_WEBHOOK_URL` | empty (no notification) |
| `TIMEZONE`, `N8N_HOST`, `N8N_PROTOCOL`, `N8N_PUBLIC_URL`, `N8N_SECURE_COOKIE`, `N8N_LOCAL_PORT`, `N8N_EXECUTIONS_MAX_AGE_HOURS` | n8n's own settings | `UTC`, `localhost`, `http`, `http://localhost:5678/`, `false`, `5678`, `336` (execution history is pruned after 14 days) |

The compose service uses the pinned image `n8nio/n8n:1.123.97`, a persistent volume (`n8n-data`) and
`N8N_BLOCK_ENV_ACCESS_IN_NODE=false`, because the workflow reads its settings from environment variables
(so there are no credentials to create after importing). Set `N8N_SECURE_COOKIE=true` when the editor is served over HTTPS.

## The workflow

**DocuMind - Document Processing Automation** (`infrastructure/n8n/documind-document-processing.workflow.json`, 22 nodes):

| # | Node | What it does |
|---|---|---|
| 1 | Webhook - Document Uploaded | `POST /webhook/documind-document-uploaded`; answers through *Respond to Webhook* so the caller gets a real status |
| 2 | Validate Event | Constant-time check of the shared secret; `event` must be `document.uploaded`; `document_id`, `job_id`, `organization_id` must be UUIDs |
| 3 | Valid Event? | Splits valid and invalid events |
| - | Respond 202 Accepted / Respond Rejected / Reject Event | Valid: `202` at once (the backend's webhook call is short). Invalid: `400`, or `401` when the secret is wrong, and the execution is marked failed in n8n |
| 4 | Start Processing | `POST /internal/documents/{id}/process` with the key; FastAPI returns the job state immediately (`202`, or `200` for a duplicate) |
| 5 | Started? | If FastAPI refused (for example the document was deleted) the workflow goes straight to a "needs attention" notice |
| 6 | Wait Before Poll | `N8N_POLL_SECONDS` (default 5 s) |
| 7 | Check Job Status | `GET /internal/jobs/{id}/status?organization_id=...` |
| 8 | Evaluate Status | One decision: `succeeded`, `poll` (still queued or running), `retry` (temporary failure, retries left), `exhausted` (temporary failure, none left), `failed` (permanent), `timeout` (monitoring limit reached; the job is **not** failed, it may still finish) |
| 9 | Route Decision | Switch on the decision |
| 10 | Wait Before Retry -> Retry Job | Waits the backoff the backend advised (`retry_after_seconds`: base x 2^n, capped), then `POST /internal/jobs/{id}/retry`, then back to polling. A hard cap (`N8N_MAX_RETRIES`) lives in the backend, so the loop cannot run forever |
| 11 | Record Success / Failure / Exhausted | `POST /internal/jobs/{id}/workflow-status` |
| 12 | Build Notice -> Notification Configured? -> Send Notification | A short message: the outcome, the document id, the file type, never contents. Sent to `N8N_NOTIFY_WEBHOOK_URL` if set. Errors here are swallowed: **notification failures never affect processing** |
| - | Send Email (optional) | Disabled. To get e-mail: add an SMTP credential in n8n, set the addresses, enable the node. (DocuMind itself has no e-mail integration to reuse) |

HTTP nodes retry three times on network errors, and every call is idempotent on the backend, so a repeated
call (a retry after a timeout, a duplicated webhook) changes nothing.

## Internal API

Mounted at `/internal`, **outside** `/api`: the website proxy and the edge proxy only forward `/api/*`, so
these routes are reachable only over the Docker network. All calls need `X-Internal-Api-Key`; with
`N8N_ENABLED=false` the whole router answers `404`. Every call names the organization; an id that belongs
to another organization is simply "not found" (`404`).

| Endpoint | Purpose | Success | Other responses |
|---|---|---|---|
| `POST /internal/documents/{document_id}/process`<br>`{job_id, organization_id, event_id?, execution_id?}` | Queue the existing ingestion pipeline for this job. Returns at once with the job state | `202` started; `200` (`started: false`) for a duplicate call | `401` bad key · `404` unknown job/document/organization · `409` `document_deleted`, `job_not_managed` · `422` invalid body |
| `GET /internal/jobs/{job_id}/status?organization_id=` | State (`queued` `running` `succeeded` `failed`), document status, stage, attempt, `retry_count`, `max_retries`, `retryable`, `can_retry`, `retry_after_seconds`, `error_code`, timestamps | `200` | `401` · `404` |
| `POST /internal/jobs/{job_id}/retry`<br>`{organization_id, execution_id?}` | Re-queue a job that failed with a temporary error | `202` retried; `200` (`retried: false`) when already queued, running or done | `409` `not_retryable` (permanent failure), `retries_exhausted`, `document_deleted` |
| `POST /internal/jobs/{job_id}/workflow-status`<br>`{organization_id, state, message?, execution_id?}` | Record the verdict: `succeeded`, `failed` (permanent) or `exhausted` (retries used up) | `200` | `409` `job_not_succeeded`, `job_in_progress`, `job_already_succeeded` |

PostgreSQL stays authoritative: the workflow can *record* success only after the pipeline really
succeeded, and cannot fail a job that is still running. The same endpoints appear in the OpenAPI
document (`/api/v1/openapi.json`, development only) under *internal (n8n)*.

## Statuses

Document status (stored in PostgreSQL, shown in the UI):

```
uploaded -> queued -> processing -> indexed            ("Completed": searchable, usable in chat)
                          |  temporary failure
                          v
                       retrying -> processing -> ...   (backoff in between, at most N8N_MAX_RETRIES times)
                          |  permanent failure, or retries used up
                          v
                        failed                         (reason shown; Retry available)
```

`indexed` keeps its name in the API, the database and the filters (renaming it would break clients); the
interface calls it **Completed**. Without n8n the sequence is `uploaded -> processing -> indexed | failed`.
A document that already has a searchable version stays `indexed` while a *re-index* hits trouble: the
previous version keeps serving, and the problem is shown as a message.

Job metadata (`ingestion_jobs`, returned by `GET /documents/{id}` as `processing`, and by the status endpoint):
current stage, `retry_count`, `retryable`, `error_code` (a short stable code such as `vector_store_unavailable`,
`network_error`, `corrupt_pdf`, `no_text`; never exception text or document content), `started_at`, `finished_at`,
`workflow_status`, `workflow_execution_id` (the n8n execution, for tracing a document to its run).

## Failures and retries

| Situation | Classified as | What happens |
|---|---|---|
| Qdrant unavailable | temporary (`vector_store_unavailable`) | retry with backoff, up to `N8N_MAX_RETRIES` |
| Embedding or AI provider timeout / unreachable, network failure | temporary (`network_error`) | same |
| Any other unexpected error in the pipeline | temporary (`processing_error`) | same |
| Unsupported format, corrupted or encrypted file, no extractable text | **permanent** | no retry: the document is `failed` at once with the reason |
| Retries used up | | `failed`, message "Processing failed after N retries (code)", notification "needs attention" |

- **No infinite loops:** the limit is enforced by the backend (`409 retries_exhausted`), the workflow polls
  at most `N8N_MAX_POLLS` times, and the sweep fails a retry that never comes.
- **No duplicates:** a retry reuses the job's index version, and the pipeline removes the failed attempt's chunks
  and vectors before it starts again.
- **Manual retry:** a failed document has a **Retry** action (documents list and detail page). It creates a new
  job, announces it to n8n again, and the document shows *queued* at once. It is refused while a retry is
  pending or a job is running (`409 reprocess_in_progress`).

## Security

- n8n -> backend: `X-Internal-Api-Key` (constant-time comparison), only on `/internal`, never reachable from the
  public side. A user's own token is no substitute for the key.
- backend -> n8n: `X-DocuMind-Webhook-Secret`, checked by the workflow before anything else.
- The editor listens on `127.0.0.1` only. To reach it remotely use an SSH tunnel or a reverse proxy with its own
  authentication; never publish port 5678 as it is.
- Webhooks carry ids and metadata only: no file contents, no extracted text, no hyperlinks, no title unless
  `N8N_INCLUDE_TITLE_IN_EVENTS=true`. Notifications carry the outcome, the document id and the file type.
- n8n keeps execution history (pruned after `N8N_EXECUTIONS_MAX_AGE_HOURS`); it contains the ids above and error
  codes. Document contents are never in it.
- Secrets live in `.env` / environment variables only. The workflow file contains none.
- n8n cannot bypass FastAPI's rules: it only starts and observes jobs that already exist, scoped to an organization.
  It cannot read, create or change documents.

## What the user sees

- **Documents list:** a status badge (Uploaded, Queued, Processing, Retrying, Completed, Failed), a line under it
  with the current step ("Creating embeddings", "Temporary problem, trying again automatically · retry 2") or the
  failure reason, and a **Retry** action for failed documents. The list refreshes by itself every 3 seconds while
  anything is in progress.
- **Notifications:** when a document finishes ("... is ready"), hits a temporary problem or fails (with the reason).
- **Document page:** a progress panel while it is processing, the failure explanation with a **Retry** button.
- **Administrators** can also get a message in a channel through `N8N_NOTIFY_WEBHOOK_URL`.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Documents are processed but the n8n **Executions** list is empty | The workflow is inactive or the webhook URL is wrong, so the backend fell back to the worker. Look for `n8n_webhook_rejected` (status 404 = inactive workflow) or `n8n_webhook_failed` in the backend log; activate the workflow, check `N8N_WEBHOOK_URL` |
| Webhook answers `401` | `N8N_WEBHOOK_SECRET` (backend) and `DOCUMIND_WEBHOOK_SECRET` (n8n) differ |
| n8n shows `401` from the backend | `INTERNAL_API_KEY` and `DOCUMIND_INTERNAL_API_KEY` differ |
| `404` on `/internal/...` | `N8N_ENABLED` is not `true` in the backend |
| n8n container exits at start | One of the three secrets is empty: the log names it |
| The backend does not start | `N8N_ENABLED=true` needs both secrets of 24+ characters |
| A document sits in *queued* | n8n is not running or inactive: the sweep hands it to the worker after 10 minutes |
| A document sits in *retrying* | n8n is waiting out the backoff; if the workflow disappeared, the sweep fails it after `N8N_STALL_MINUTES` |
| Imported workflow shows `$env` access errors | `N8N_BLOCK_ENV_ACCESS_IN_NODE` must be `false` (the compose service sets it) |

## Test results

Automated (run in CI): 19 unit tests (backoff, key check, webhook client against a mock server, failure
classification, settings rules, and checks that the workflow file and the compose service stay consistent with the
backend) and 21 integration tests against real PostgreSQL and Qdrant, with a fake n8n
that records the webhook and drives the internal API exactly as the workflow does: PDF, DOCX and Excel through the
whole lifecycle; duplicate webhooks and duplicate process/retry calls; unauthorized calls; organization isolation;
invalid events; temporary failure -> retry with exponential backoff -> recovery without duplicate chunks or
vectors; retry exhaustion; Qdrant and embedding-provider outages; permanent failures; success that cannot be
claimed by the workflow; manual retry; webhook failure falling back to the worker; the sweep taking back lost jobs
and failing stalled retries; titles off by default. The existing upload, retrieval, RAG, citation, link and
permission tests all still pass.

Manual, against the real workflow imported into n8n 1.123.97 with the real worker (the scripts are not part of the
repository; the scenarios are repeatable by hand):

| Scenario | Result |
|---|---|
| PDF, DOCX, XLSX uploads | all completed in 3-4 s; statuses uploaded/queued -> processing -> completed; job recorded as succeeded; notification received (without the title) |
| Webhook with unknown event, malformed id, wrong secret, no secret | `400`, `400`, `401`, `401` |
| The same valid event delivered twice more | both `202`; one job, one set of chunks, document completed once |
| Corrupted file | failed at once as `no_text`, no retries, "failed permanently" notification |
| Qdrant stopped | retrying (3 s, then 6 s), stopped at the maximum of 2 retries, failed with "after 2 retries (vector_store_unavailable)", "needs attention" notification |
| Qdrant stopped, started again during a retry | the retry succeeded; completed after 1 retry; one set of chunks |
| Manual Retry on a failed document | accepted, document shows *queued* immediately, second click refused (`409`), completed, one set of chunks |
| Browser (Playwright) against that stack | all 9 end-to-end tests pass, including a failed document with its reason and Retry |

## Not in scope

DocuMind extracts links from documents but does not fetch the pages they point to, and this workflow does not
either. A design for doing that safely (SSRF protection, redirect checks, size and time limits, untrusted content)
is in `docs/architecture.md`, section 3.6.
