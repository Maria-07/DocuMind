# DocuMind User Manual

DocuMind is a document intelligence platform for an organization. You upload PDF, Word and Excel
files. DocuMind reads them, indexes them for search, and answers questions about them with
citations that link to the exact page, section or spreadsheet row. Every answer is limited to the
documents the person asking is allowed to see.

This manual has three parts:

- **Part 1 – Using DocuMind** (sections 1–12): for everyone who uses the web app.
- **Part 2 – How it works** (sections 13–18): the technical design behind each feature.
- **Part 3 – Running and configuring** (sections 19–24): for whoever installs and operates it.

Related documents: [architecture.md](architecture.md) (design decisions in depth) and
[deployment.md](deployment.md) (production deployment, backups, upgrades).

---

## Contents

**Part 1 – Using DocuMind**
1. [Getting started](#1-getting-started)
2. [Key concepts](#2-key-concepts)
3. [Home](#3-home)
4. [Documents](#4-documents)
5. [AI Chat](#5-ai-chat)
6. [Compare](#6-compare)
7. [Evaluations](#7-evaluations)
8. [Analytics](#8-analytics)
9. [Embeddings](#9-embeddings)
10. [User Management](#10-user-management)
11. [Settings](#11-settings)
12. [Roles and permissions reference](#12-roles-and-permissions-reference)

**Part 2 – How it works**

13. [Architecture](#13-architecture)
14. [Ingestion pipeline](#14-ingestion-pipeline)
15. [Retrieval and answers](#15-retrieval-and-answers)
16. [Comparison](#16-comparison)
17. [Evaluation metrics](#17-evaluation-metrics)
18. [Security model](#18-security-model)

**Part 3 – Running and configuring**

19. [Running DocuMind](#19-running-documind)
20. [Configuration reference](#20-configuration-reference)
21. [Command-line tools](#21-command-line-tools)
22. [REST API](#22-rest-api)
23. [Troubleshooting](#23-troubleshooting)
24. [Glossary](#24-glossary)

---

# Part 1 – Using DocuMind

## 1. Getting started

### Create a workspace

DocuMind has no built-in default account. The first person creates a workspace:

1. Open the app. Locally this is http://localhost:3000; in a deployment it is `https://<your domain>`.
2. On the sign-in page, choose **register**.
3. Enter your full name, e-mail, a password and an organization name.
4. You are signed in as the **Admin** of the new organization.

Password rules:

- At least **10 characters** by default (set by `PASSWORD_MIN_LENGTH`).
- At least one letter and at least one digit.
- No leading or trailing spaces.

### Add your colleagues

Colleagues do not register themselves into your workspace; registering always creates a *new*
organization. Instead, an admin adds them:

1. Go to **User Management → Add user**.
2. Enter their name, e-mail, role and an initial password.
3. Share the password with them securely. They can change it later in **Settings → Password**.

### Signing in and sessions

- You stay signed in for up to 7 days of inactivity.
- Your session renews itself in the background.
- Signing out, or changing your password, ends the session. A password change ends it on every
  device.
- After 5 wrong passwords in a row the account is locked for 15 minutes.

---

## 2. Key concepts

### Organization

Everything (users, documents, chats, evaluations) belongs to one organization. Organizations are
fully isolated: nobody can see another organization's data, and a document id from another
organization behaves as if it doesn't exist.

### Roles

Each user has exactly one role:

| Role | Intended for | In short |
|---|---|---|
| **Admin** | Workspace owners, IT | Everything: all documents, users, groups, settings, analytics, embeddings, evaluations |
| **Manager** | Team leads, content owners | Uploads documents, manages categories, views evaluation results |
| **User** | Everyone else | Reads documents shared with them, chats, compares. Can upload only if an admin allows it |

The full matrix is in [section 12](#12-roles-and-permissions-reference).

### Document visibility

Each document has one of three visibility levels:

| Visibility | Who can see it |
|---|---|
| **Everyone in the organization** | All members |
| **Restricted (shared only)** | The owner, admins, and the people or groups it is explicitly shared with |
| **Private (me and admins)** | The owner and admins only. Sharing has no effect |

Rules that apply on top of visibility:

- Sharing gives either **Can view** or **Can manage**. *Manage* lets that person edit, re-index,
  share and delete the document.
- Visibility applies everywhere: in the document list, in search, in chat answers, in comparisons
  and in evaluation results. The AI cannot quote a document you aren't allowed to open.

### Document status

Every upload moves through these states:

```
uploaded ──▶ processing ──▶ indexed      (searchable, usable in chat)
                       └──▶ failed       (reason shown on the document's Details tab)
```

Only **indexed** documents are used for answers.

### Categories and groups

- **Categories** label documents (for example Finance or Legal) for filtering.
- **Groups** are teams of people. You can share a *restricted* document with a whole group at
  once.

---

## 3. Home

The home page shows:

- Counts of total, indexed, processing and failed documents.
- An **Ask your documents** box that starts a chat.
- Your recent documents with their processing status.
- Your recent conversations.

---

## 4. Documents

### Uploading

1. On **Documents**, choose **Upload documents**.
2. Drag files in, or click to browse.
3. Choose **Who can see these documents** (visibility) and, optionally, a **Category**.
4. Upload. Files are sent in parallel, three at a time, each in its own request with its own
   progress bar, so a large file doesn't hold back the smaller ones. Each file appears in the list as
   soon as its own upload finishes, with status *uploaded*, and moves to *indexed* on its own; the
   page updates live.
   You can add more files while others upload. You can also close the dialog: uploads in progress
   continue, and a notification reports the result.

Upload rules:

| Rule | Default |
|---|---|
| File types | PDF, DOCX, XLSX, XLS |
| Maximum file size | 50 MB (`MAX_UPLOAD_SIZE_MB`) |
| Files per upload | 20 (`MAX_FILES_PER_UPLOAD`) |
| PDF pages | 2,000 (`MAX_PDF_PAGES`) |
| Spreadsheet rows | 100,000 per sheet (`MAX_SHEET_ROWS`) |

What DocuMind checks:

- **Content, not just the extension.** A renamed file is rejected.
- **No macros.** Macro-enabled Office files are refused.
- **Duplicates.** Uploading an identical file again is reported as a duplicate.
- **Unusable files.** Encrypted or corrupt files, and scanned PDFs with no text layer, are
  reported as failures with the reason. DocuMind does not perform OCR.

Who can upload: managers and admins. Users can upload only when an admin turns on **Settings →
Users can upload documents**.

### Finding documents

The list can be searched by title or file name, and filtered by status, file type and category.
It is paginated.

### Document detail page

Click a document to open it. The page has three tabs:

**Extracted content**
- The text DocuMind extracted, split into the same chunks it uses for answers.
- Each chunk shows its location: page, section heading path, or sheet and rows.

**Preview**
- PDFs render inside the app.
- Other file types can be downloaded with **Download**.

**Details**
- *Metadata*: type, size, pages or sheets, author and similar fields.
- *Extraction notes*, for example "page 4 has no text layer".
- *Visible to*: who can see the document.
- *Processing history*: every ingestion job with its status, duration and error. If processing
  failed, the reason is shown here.

Actions (shown to people who can manage the document):

| Action | What it does |
|---|---|
| **Ask about this document** | Starts a chat limited to this document |
| **Download** | Downloads the original file |
| **Share** | Opens the share dialog (see below) |
| **Visibility** | Changes between organization, restricted and private |
| **Reprocess** | Re-indexes the document, for example after a failure or a configuration change. The previous index keeps answering until the new one is complete |
| **Delete** | Removes the document. It disappears from search and chat immediately; its vectors are then purged |

### Sharing

**Share** opens a dialog:

1. Add a person or a group.
2. Pick **Can view** or **Can manage**.
3. Remove an entry to revoke it.

Sharing only has effect when visibility is **Restricted** (or, for *Can manage* grants, also
**Everyone in the organization**). Changes apply to search and chat immediately.

---

## 5. AI Chat

### Asking a question

1. Open **AI Chat** and type a question, for example *"How long are customer database backups
   kept?"*.
2. While the answer is prepared you see the stages: *Thinking* (DocuMind decides whether your
   message needs your documents), then, for document questions, *Searching your documents* and
   *Reading sources*. The answer then streams in word by word.
3. Use **Stop** to interrupt an answer.

Conversations are saved in the list on the left. They are private to you. Follow-up questions use
the last few turns as context, so *"and for the HR system?"* works.

### Chatting naturally

You don't have to start with a question about a file. DocuMind decides for each message whether it
needs your documents:

| You say | DocuMind does |
|---|---|
| A greeting, thanks, goodbye ("Hello!", "Thanks!") | Replies in a sentence or two. No search, no citations |
| "What can you do?", "Who are you?" | Explains briefly what it can help with |
| A general question or writing help ("Explain what a REST API is") | Answers from general knowledge and says so; it is **not** from your documents |
| Anything about your organization's rules, figures, files or people | Searches your documents and answers with citations |

Replies that need no documents carry no citations and no warning badge. When a *document* question
can't be backed by a citation, the answer shows **Not backed by document citations**.

When DocuMind is unsure, it searches your documents; it never answers a question about your
organization from memory. If you ever get a general answer to a question that should have used your
documents, ask again naming the topic or document.

### Choosing a model

The model picker sits at the bottom left of the message box. It lists every model your administrator has
made available (those whose provider key is configured), grouped by provider, with its price per million
input and output tokens. The choice is remembered in your browser and applies to your next message, so you
can switch models in the middle of a conversation: the history is plain text and works with any model.

- A model that just failed because its quota or usage limit is used up is greyed out in the list, with the
  provider's explanation (for example when access returns). It becomes selectable again once it works, or
  after ten minutes.
- Cheaper models suit simple questions; larger ones help with hard reasoning. Document answers are
  grounded in the same retrieved passages whichever model writes them.

### Editing and resubmitting a message

Point at one of your messages and choose the pencil icon. Change the text (or leave it as it is to ask
again with a different model) and choose **Resubmit**.

- Your message and every reply after it are replaced by the new answer. The answer is written using only
  the conversation *before* the edited message.
- Nothing is deleted: the replaced messages are kept out of sight in the database (for the audit trail),
  but they no longer appear and are not sent to the model.
- An edited message is marked *edited*. If you edit the first message, the conversation is renamed after it.
- You can only edit your own messages, and not while a reply is being written.

### What a reply costs

Under every reply a line shows the model, the tokens and the **estimated cost**, for example
*Gemini 3.5 Flash-Lite · 1,529 in · 124 out · $0.0004*. Point at it for the breakdown:

- **Answer**: the model you chose. Tokens *in* are your question, the conversation and any retrieved
  passages; tokens *out* are the reply (for reasoning models this includes their hidden thinking).
- **Routing**: the small call that decides whether your message needs your documents. It runs on the
  default (or configured routing) model, not the one you picked, and is skipped for plain greetings.
- How long the reply took, and cached or reasoning tokens where the provider reports them.

The conversation header shows the running total. Costs are **estimates**: provider-reported token counts
multiplied by the prices in the model catalog. They are saved with each reply, so changing a price later
does not rewrite history. If a model has no price in the catalog you see *cost n/a* and only tokens.

### Citations

Answers contain numbered markers like **[1]**. Each marker is a citation with:

- the document title;
- the location: page, section, or sheet and row numbers;
- a quoted snippet.

Click a citation to open the source document. DocuMind checks every citation against the passage
it claims to quote; a citation that doesn't match is dropped and never shown.

### When the documents don't say

If no document is relevant enough, DocuMind says it couldn't find the answer instead of guessing, and
suggests rephrasing or naming the document. In that case the language model isn't called at all.

When an answer adds reasoning that goes beyond what the documents state, that part is labelled
**Interpretation:**, so facts and inference stay separate.

### Limiting a chat to certain documents

**Ask about this document** on a document page starts a chat limited to that one document. The
chat header then shows *Scoped to 1 document(s)*.

Through the API, a conversation or a single message can be scoped to up to 100 documents and 50
categories. A scope can only narrow what you're allowed to see, never widen it.

---

## 6. Compare

**Compare** puts 2–5 documents side by side.

1. Choose **New comparison**.
2. Add 2–5 indexed documents.
3. Optionally enter a **focus question**, for example *"How do retention periods differ?"*.

The result is a table:

- **Rows** are aspects, such as *retention period* or *approval process*.
- **Columns** are documents.
- Each cell is **present** (with the document's own cited evidence), **absent** or **unclear**.
- Each aspect has a short summary of the differences. Aspects where the documents conflict are
  flagged under **Missing or inconsistent**.

Rules:

- A "present" finding without valid evidence from *that* document is downgraded to "unclear".
- You can only compare documents you are allowed to view.
- Past comparisons are listed under **History**.

---

## 7. Evaluations

Evaluations measure how well DocuMind answers questions about your own documents, so you can
tell whether a change (a new model, different chunking or a new prompt) made it better or worse.
Managers and admins can see results; only admins can start runs.

### Datasets and test cases

1. **New dataset**: give it a name and description.
2. Open it and choose **Add a test case**:
   - **Question** (required).
   - **Reference answer** (optional): the correct answer, used to grade correctness.
   - **Relevant documents**: the documents that contain the answer. Leave this empty for a
     question the documents *cannot* answer. The test then checks that DocuMind correctly says
     "not found".
   - **Evidence quote** (optional, recommended): exact text from the document that answers the
     question. It enables passage-level metrics and still works after the document is re-chunked.
   - **Tags**, comma separated.
3. To add many cases at once, use **Import JSON** with an array of cases:

```json
[
  {
    "question": "How long are database backups kept?",
    "expected_answer": "Thirty days.",
    "relevant_document_ids": ["<document uuid>"],
    "evidence": [{ "document_id": "<document uuid>", "quote": "kept for thirty days" }],
    "tags": ["retention"]
  },
  { "question": "What is the CEO's favourite colour?", "relevant_document_ids": [] }
]
```

Limits: up to 20 relevant documents, 20 evidence quotes and 10 tags per case.

### Running and reading results

- **Run** executes every case through the real search-and-answer pipeline, as the admin who
  started it. It runs in the background.
- The run page shows:
  - **Metrics**: averages, with the change against the previous run. Drops greater than 5 points
    are flagged.
  - **Per-case results**: answer, citations, faithful, correct, abstention, latency, and the
    judge's rationale and unsupported claims.
  - **Configuration**: everything that can affect results, recorded with the run (models, chunk
    sizes, thresholds, prompt and judge versions).
- The dataset page shows a **Quality trend** across completed runs.

What each metric means is described in [section 17](#17-evaluation-metrics).

**Redaction:** if a test case or a run's answer involves a document you aren't allowed to view,
the reference answer, quotes, answer, citations and judge notes are hidden from you; the scores
remain visible. The page tells you when this has happened.

---

## 8. Analytics

*Admins only.* Choose a time range, for example **Last 7 days**. The page shows:

- **Tiles**: documents, indexed chunks, questions and grounded answers. Conversational replies
  (greetings and the like) are not document answers, so they don't count towards the grounded
  rate or the answer latency.
- **Document activity**: uploads and successful indexing per day, plus failures.
- **Questions per day**, with the "not found" rate.
- **Retrieval latency** per stage (search, rerank, generation), as p50 and p95.
- **Recent document activity**.

All figures come from the live database; nothing is sampled or simulated.

---

## 9. Embeddings

*Admins only.* An *embedding configuration* is the combination of embedding model and chunking
settings used to index documents. Changing any of them on the server creates a new configuration
and a new index.

The page shows:

- How many documents are on the active index, are **stale** (on an older configuration), are not
  yet indexed, or have failed.
- Each configuration with its chunking settings and its document, chunk and vector counts. A
  warning appears if the vector count doesn't match the chunk count.

Re-index actions: **stale**, **failed** or **all** documents. Each document keeps answering from
its old index until its new version is complete, so re-indexing causes no downtime.

---

## 10. User Management

*Admins only.*

- Search people by name or e-mail and see each person's role and last sign-in.
- **Add user**: full name, e-mail, role and an initial password.
- Change a person's role, or use the active switch to deactivate them. Role changes take effect on
  that person's next request. Deactivating someone signs them out everywhere.

---

## 11. Settings

| Section | Who | What |
|---|---|---|
| **Organization** | Admin | Workspace name; **Users can upload documents** (off by default); **Default visibility for new uploads** (uploaders can still choose for each upload) |
| **Categories** | Admin, Manager | Create, rename and delete categories |
| **Groups** | Admin | Create groups and choose their members |
| **Password** | Everyone | Change your own password. This signs you out on every device |

---

## 12. Roles and permissions reference

| Capability | Admin | Manager | User |
|---|:-:|:-:|:-:|
| View documents shared with them or visible to the organization | ✓ | ✓ | ✓ |
| View and manage *every* document, including private ones | ✓ | | |
| Upload documents | ✓ | ✓ | only if enabled in Settings |
| Manage own uploads (edit, share, re-index, delete) | ✓ | ✓ | ✓ |
| AI Chat, search, Compare | ✓ | ✓ | ✓ |
| Manage categories | ✓ | ✓ | |
| View evaluations | ✓ | ✓ | |
| Create datasets and run evaluations | ✓ | | |
| Analytics | ✓ | | |
| Embeddings and re-index everything | ✓ | | |
| Manage users and groups | ✓ | | |
| Organization settings | ✓ | | |

The internal permission names are `documents:admin`, `documents:upload`, `categories:manage`,
`evaluations:view`, `evaluations:run`, `analytics:organization`, `organization:manage`,
`users:manage`, `groups:manage` and `audit:view`. The backend checks these permissions, never role
names, and the navigation hides pages you can't use.

---

# Part 2 – How it works

## 13. Architecture

```
 browser ──HTTPS──▶ Caddy ──▶ Next.js frontend ──/api/*──▶ FastAPI backend ──▶ PostgreSQL
                    TLS        UI + API proxy               REST + SSE         Redis
                                                            arq worker ───────▶ Qdrant
```

| Component | Technology | Role |
|---|---|---|
| Frontend | Next.js 16, React, Tailwind, shadcn/ui, TanStack Query | The UI. It proxies `/api/*` to the backend, so cookies stay first-party and no CORS is needed |
| Backend | FastAPI, async SQLAlchemy 2, Pydantic v2 | REST API, authentication, permissions, search, chat streaming (SSE) |
| Worker | arq (Redis queue) | Parsing, chunking, embedding and indexing documents; evaluation runs; a sweep for stuck jobs |
| PostgreSQL | 16, Alembic migrations | Source of truth: users, documents, chunks, conversations, evaluations, audit log |
| Qdrant | 1.20 | Dense and sparse vectors with permission (ACL) data attached |
| Redis | 7 | Job queue and rate limiting |
| Caddy | 2 | TLS, real client IP, the only publicly exposed service |
| LLM | OpenAI, Anthropic Claude and Google Gemini (several can be enabled at once) | Answer generation, routing, comparison, evaluation judging |

Embeddings and reranking run **locally on the CPU** with fastembed (ONNX). No document text leaves
the server for indexing. Only the passages selected for an answer are sent to the LLM provider.

Backend layout: `app/api` (routers and dependencies), `app/services` (use cases, access policy),
`app/repositories` (organization-scoped queries), `app/models`, `app/schemas`, `app/workers`,
`app/core` (config, security, logging, rate limiting).

## 14. Ingestion pipeline

Triggered by every upload or re-index. The worker:

1. **Parses** the file:
   - **PDF** with pdfplumber: page text, tables converted to Markdown, metadata, and detection of
     scanned pages.
   - **DOCX** with python-docx: the heading hierarchy becomes section paths; tables are kept.
   - **XLSX** with openpyxl and **XLS** with xlrd: one `Header: value` record per row.
2. **Chunks** the text with LangChain's `RecursiveCharacterTextSplitter`, measured in model
   tokens:
   - Default size 400 tokens with 60 tokens of overlap.
   - Chunks never cross a page, section or sheet boundary.
   - Table and row-group chunks repeat their header row.
3. **Embeds** each chunk with `BAAI/bge-base-en-v1.5` (768-dimension dense vectors) and
   `Qdrant/bm25` (sparse keyword vectors).
4. **Indexes** the chunks in Qdrant. There is one collection per embedding configuration
   (`dm_<model>_<chunking>_<hash>`), reached through the alias `chunks_active`. Each point's
   payload holds:
   - the organization;
   - ACL tokens;
   - page, section, sheet and rows;
   - the chunk text.
5. **Publishes** the new version. New points are written as `searchable=false` and switched on only
   when the whole version is stored, so the old version keeps answering until then.

Additional behaviour:

- Chunks that look like prompt injection (for example "ignore previous instructions") are flagged.
- Transient errors are retried up to 3 times with exponential backoff (`INGESTION_MAX_TRIES`).
  Permanent errors (encrypted, corrupt, or no text) fail immediately with a clear reason.
- A sweep job every 5 minutes re-queues lost jobs and recovers stuck ones.

## 15. Retrieval and answers

For each message:

0. **Route** (`app/rag/routing.py`). Unmistakable greetings, thanks and goodbyes are recognised by
   rule, with no model call. Anything else gets one small structured-output call (low reasoning
   effort) that returns `chat` or `documents` and, for follow-ups, a standalone search query (this
   replaces the former separate query-rewrite call).
   - **chat**: the assistant replies using `ASSISTANT_SYSTEM_PROMPT` with no document access, no
     search and no citations. The reply never states facts about the organization.
   - **documents**: steps 1-5 below.
   - If the routing call fails, or the model is unsure, the message is treated as `documents`.
1. **Retrieve.** One Qdrant query combines dense and BM25 search (up to 40 candidates each), fused
   with Reciprocal Rank Fusion. The permission filter (organization, `searchable`, ACL tokens,
   optional scope) is built from the authenticated user on the server, never from request input.
2. **Re-check.** Every hit is verified again in PostgreSQL: the document must still exist, the user
   must still be allowed to view it, and the chunk must belong to the active index version. Stale
   vector data can therefore only remove results, never add them.
3. **Rerank.** The top 20 hits are rescored by the `BAAI/bge-reranker-base` cross-encoder.
   - If the best score is below `RETRIEVAL_MIN_RELEVANCE` (0.002), the answer is "not found" and
     the LLM is not called.
   - Otherwise the top 8 chunks are kept, at most 4 per document, within a 6,000-token budget.
4. **Generate.** The answer depends on `LLM_PROVIDER`:
   - **OpenAI**: the Responses API with `store=false`. Sources are numbered `<source>` blocks, cited
     with `[n]` markers (one citation per chunk).
   - **Anthropic**: Claude's native citations over one document block per sentence or row, so
     citations point at exact passages.

   Either way, the answer streams over Server-Sent Events. The stream sends these events in order:
   `meta`, `status` (`thinking`, then `retrieving` for document questions, then `generating`),
   `sources`, `token`, `citation`, `done` (or `error`). `done` carries `route` (`chat` or
   `documents`), and each stored message records its route.
5. **Validate citations.** Each citation is checked against the chunk it references. Invalid ones
   are dropped and counted.

Retrieved text is treated as data, never as instructions: the model has no tools, and flagged
chunks are marked as suspicious in the prompt.

**Prompts** (`app/rag/generation/prompts.py`, `PROMPT_VERSION` is recorded with every evaluation
run):

- `ASSISTANT_SYSTEM_PROMPT`: the personality (warm, brief, natural, in the user's language), what
  DocuMind can and cannot do, and the honesty rules. It is the voice of every reply.
- `ANSWER_SYSTEM_PROMPT` / `MARKER_ANSWER_SYSTEM_PROMPT`: the same persona plus the rules for
  answering from excerpts (cite, say so when the documents don't cover it, keep
  **Interpretation:** apart from facts). Native citations for Claude, `[n]` markers for OpenAI.
- `ROUTER_SYSTEM_PROMPT`: the chat-or-documents decision and the query rewrite.
- `JUDGE_SYSTEM_PROMPT` (`app/rag/evaluation/judge.py`, `JUDGE_VERSION`): the evaluation rubric.

Measured retrieval quality on the built-in benchmark (12 paraphrased questions, real models):
hybrid search with reranking reaches MRR 0.96 and NDCG@5 0.97.

## 16. Comparison

1. Each selected document contributes its own sources: chunks relevant to the focus question or,
   without one, its opening content. This way a large document cannot crowd out the others.
2. One structured-output call (JSON schema) returns aspects × documents with
   `present / absent / unclear`, a summary of the differences and a conflict flag.
3. Validation on the server:
   - Each finding keeps only citations from its own document.
   - "Present" without valid evidence becomes "unclear".
   - Labels the model invents (such as `D1`) are replaced with document titles.

## 17. Evaluation metrics

| Group | Metric | Meaning |
|---|---|---|
| Retrieval | Precision@5, Recall@5 | Share of the top 5 chunks that are relevant, and share of the relevant chunks found in the top 5 |
| | MRR | How high the first relevant chunk ranks (1.0 = first) |
| | NDCG@5 | Ranking quality of the top 5 |
| | Document recall | Share of the relevant documents that were retrieved |
| Answer (LLM judge, rubric `judge-v2`) | Faithfulness | Claims supported by the cited sources |
| | Hallucination rate | Share of unsupported claims |
| | Answer relevance | Whether the answer addresses the question |
| | Context relevance | Whether the retrieved passages fit the question |
| | Correctness | Agreement with the reference answer |
| Deterministic | Citation accuracy | Citations that match their passages |
| | Abstention accuracy | Says "not found" exactly when it should |
| | Latency | Time for retrieval and answer |

How metrics are computed:

- The rubric (`JUDGE_SYSTEM_PROMPT`) ignores friendly wording, offers of help and citation markers,
  counts only statements of fact about the documents as claims, and defines each 1-5 score. Evaluation
  runs test the document-answer path; routing is covered by its own tests.
- The judge uses `LLM_MODEL`, unless `EVAL_JUDGE_MODEL` names a different model from the same
  provider. A stronger judge model reduces self-preference bias.
- Relevance labels come from the evidence quotes, which are resolved against the chunks of the
  configuration being tested. This lets one dataset compare different chunking settings fairly.

## 18. Security model

**Authentication**
- Passwords are hashed with Argon2id.
- Access tokens are 15-minute JWTs, held in memory by the browser.
- Refresh tokens:
  - 7-day opaque tokens, in an `httpOnly`, `Secure`, `SameSite=Strict` cookie scoped to
    `/api/v1/auth`;
  - rotated on every use and stored only as SHA-256 hashes;
  - reuse of an old token revokes the whole session family.
- Sign-in failures return generic errors, and accounts lock after repeated failures.

**Authorization**
- Roles map to permissions.
- The user's identity and permissions are rebuilt from the database on every request, so role
  changes and deactivation apply immediately.

**Document access**
- One policy, implemented three times and tested against each other: in Python (`can_view` /
  `can_manage`), in SQL for listings, and as organization-qualified ACL tokens in Qdrant.
- Another organization's ids return 404.

**Evaluations**
- Content drawn from documents a viewer cannot see is redacted for that viewer.

**Uploads**
- An extension allow-list plus content sniffing.
- Macro rejection and a zip-bomb guard (500 MB uncompressed limit).
- Size and count limits enforced before parsing.
- Files are stored under random keys, outside any web root.

**Network**
- Only Caddy publishes ports (80 and 443).
- Caddy replaces any `X-Forwarded-For` header sent by a client. Because only the frontend can
  reach the backend, the backend can trust that header for per-IP rate limits and audit IPs.

**Rate limits (per minute)**
- Login and register: 10 per IP, and 10 per target account.
- Token refresh: 60. Uploads: 60 (one file per request). Chat: 20. Search: 60. Everything else: 300.

**Headers**
- Content-Security-Policy with no third-party origins.
- `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: no-referrer`, `Permissions-Policy`.
- HSTS in production.

**Production guards**
- The API refuses to start with non-secure cookies, wildcard CORS, the example JWT secret, or no
  Qdrant API key.
- API docs are disabled in production.

**Audit log**
- Records sign-ins (including failures), user and role changes, uploads, downloads, deletions,
  sharing and settings changes, and evaluation runs.
- Logs never contain document text, prompts, tokens or passwords.

---

# Part 3 – Running and configuring

## 19. Running DocuMind

### Option A – Docker Compose (production-like)

```bash
cp .env.example .env    # set POSTGRES_PASSWORD (hex), JWT_SECRET_KEY, QDRANT_API_KEY, SITE_ADDRESS, LLM key
docker compose -f infrastructure/docker-compose.yml --env-file .env up -d --build
```

Open `https://<SITE_ADDRESS>`. Migrations run automatically before the backend starts. TLS,
backups, upgrades and scaling are covered in [deployment.md](deployment.md).

### Option B – Native (development; this is how the Windows machine runs it)

Requirements: Python 3.12+, Node 20.9+ (22 recommended), PostgreSQL 15+, Redis 6+ and Qdrant 1.20.

Start each service in its own terminal, with `.env` at the repository root:

```powershell
# 1. Qdrant (native binary in infrastructure/qdrant/bin)
powershell -ExecutionPolicy Bypass -File infrastructure\qdrant\start-qdrant.ps1

# 2. API
cd backend; .venv\Scripts\activate
alembic upgrade head
uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8010

# 3. Worker (needed for uploads to be indexed, and for evaluations)
cd backend; .venv\Scripts\activate
arq app.workers.settings.WorkerSettings

# 4. Frontend
cd frontend
npm run dev        # http://localhost:3000; proxies /api/* to API_URL (default http://127.0.0.1:8010)
```

On its first start the worker downloads the models (about 220 MB, plus the reranker) into
`MODEL_CACHE_DIR`.

Health endpoints:
- `GET /health`: the process is alive.
- `GET /ready`: PostgreSQL, Redis and Qdrant are reachable.

## 20. Configuration reference

All settings are environment variables, read from `.env`. Names are case-insensitive. Secrets have
no defaults.

**Required**

| Variable | Notes |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://…` |
| `JWT_SECRET_KEY` | At least 32 random characters |
| `QDRANT_API_KEY` | Required in production |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY` | At least one is needed for chat, comparison and evaluation; set several to offer several providers' models in chat. Upload and search work without any |

**General**

| Variable | Default |
|---|---|
| `ENVIRONMENT` | `development` (or `test`, `production`) |
| `LOG_LEVEL` | `INFO` |
| `LOG_JSON` | `true` |
| `CORS_ORIGINS` | `http://localhost:3000` |
| `API_DOCS_ENABLED` | On, except in production |
| `REDIS_URL` | `redis://localhost:6379/0` |
| `QDRANT_URL` | `http://localhost:6333` |

**Authentication**

| Variable | Default |
|---|---|
| `ACCESS_TOKEN_TTL_MINUTES` | 15 |
| `REFRESH_TOKEN_TTL_DAYS` | 7 |
| `COOKIE_SECURE` | `true` |
| `LOGIN_MAX_FAILED_ATTEMPTS` | 5 |
| `LOGIN_LOCKOUT_MINUTES` | 15 |
| `REGISTRATION_ENABLED` | `true`. Set `false` to close public sign-up (nobody can create a workspace); create admins with `python -m app.cli create-admin`. The sign-in page then hides its "create a workspace" link |
| `PASSWORD_MIN_LENGTH` | 10 |

**Uploads**

| Variable | Default |
|---|---|
| `STORAGE_LOCAL_PATH` | `./storage` |
| `MAX_UPLOAD_SIZE_MB` | 50 |
| `MAX_FILES_PER_UPLOAD` | 20 |
| `MAX_UNCOMPRESSED_OOXML_MB` | 500 |
| `DEFAULT_DOCUMENT_VISIBILITY` | `organization` |

**Ingestion**

Changing `EMBEDDING_MODEL`, `SPARSE_MODEL`, `CHUNK_SIZE_TOKENS` or `CHUNK_OVERLAP_TOKENS` creates a
new index. Re-index afterwards.

| Variable | Default |
|---|---|
| `EMBEDDING_MODEL` | `BAAI/bge-base-en-v1.5` |
| `SPARSE_MODEL` | `Qdrant/bm25` |
| `CHUNK_SIZE_TOKENS` | 400 |
| `CHUNK_OVERLAP_TOKENS` | 60 |
| `MODEL_CACHE_DIR` | `./.models` |
| `MAX_PDF_PAGES` | 2000 |
| `MAX_SHEET_ROWS` | 100000 |
| `INGESTION_MAX_TRIES` | 3 |
| `WORKER_MAX_JOBS` | 2 |
| `WORKER_JOB_TIMEOUT_SECONDS` | 900 |

**Retrieval**

| Variable | Default |
|---|---|
| `RERANKER_ENABLED` | `true` |
| `RERANKER_MODEL` | `BAAI/bge-reranker-base` |
| `RETRIEVAL_CANDIDATES` | 40 |
| `RERANK_CANDIDATES` | 20 |
| `RETRIEVAL_TOP_N` | 8 |
| `RETRIEVAL_MIN_RELEVANCE` | 0.002. Re-calibrate this if you change the reranker |
| `RETRIEVAL_MAX_CHUNKS_PER_DOCUMENT` | 4 |
| `CONTEXT_MAX_TOKENS` | 6000 |

**Generation**

| Variable | Default |
|---|---|
| `LLM_PROVIDER` | `anthropic` (or `openai`, `google`): the provider of the default model |
| `LLM_MODEL` | `claude-opus-5-5` (this installation uses `gpt-5-nano`): the default model |
| `LLM_CATALOG_FILE` | Unset (built-in `app/rag/generation/models.json`). Your own copy adds models or updates prices; a broken file stops the API at start-up |
| `CHAT_MODEL_IDS` | Unset (every catalog model with a key is selectable). A comma-separated list limits what users can pick, e.g. `gpt-5-nano,gpt-5-mini` for a public demo |
| `LLM_ROUTER_MODEL` | Unset (routing uses the default model). A cheap, fast model here keeps routing cheap when users pick an expensive one |
| `LLM_EFFORT` | `medium` |
| `LLM_MAX_TOKENS` | 8000 |
| `LLM_TIMEOUT_SECONDS` | 120 |
| `CHAT_HISTORY_TURNS` | 6 |
| `EVAL_JUDGE_MODEL` | Unset (the answer model judges itself) |

**Rate limits (per minute)**

| Variable | Default |
|---|---|
| `RATE_LIMIT_ENABLED` | `true` |
| `RATE_LIMIT_AUTH_PER_MINUTE` | 10 |
| `RATE_LIMIT_LOGIN_PER_ACCOUNT_PER_MINUTE` | 10 |
| `RATE_LIMIT_REFRESH_PER_MINUTE` | 60 |
| `RATE_LIMIT_UPLOAD_PER_MINUTE` | 60 |
| `RATE_LIMIT_CHAT_PER_MINUTE` | 20 |
| `RATE_LIMIT_SEARCH_PER_MINUTE` | 60 |
| `RATE_LIMIT_DEFAULT_PER_MINUTE` | 300 |

**Docker stack only**

| Variable | Notes |
|---|---|
| `SITE_ADDRESS` | Your domain, or `localhost` |
| `HSTS_MAX_AGE` | Set it once real TLS works |
| `HTTP_PORT`, `HTTPS_PORT` | Published ports |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | Database credentials |

**Frontend build**

| Variable | Notes |
|---|---|
| `API_URL` | Backend address, compiled into the build |
| `NEXT_OUTPUT=standalone` | Used for the Docker image |

The backend validates these settings at start-up. It rejects, for example, chunk overlap that is
not smaller than chunk size, or an OpenAI model name with `LLM_PROVIDER=anthropic`.

## 21. Command-line tools

Run these from `backend/` with the virtual environment active. In Docker, prefix them with
`docker compose … exec backend`.

```bash
python -m app.cli reindex --stale     # documents not on the active embedding configuration
python -m app.cli reindex --failed    # retry failed documents
python -m app.cli reindex --all       # everything (e.g. after a parser improvement)

# Create an organization and its admin. Works while sign-up is closed; prompts for the password
# (or set DOCUMIND_ADMIN_PASSWORD for scripts)
python -m app.cli create-admin --email you@example.com --name "Your Name" --organization "Acme"

# Run an evaluation dataset; exits 1 on a regression (for CI)
python -m app.cli evaluate --dataset <uuid> \
    --fail-under retrieval.ndcg@5=0.7 --fail-under answer.faithfulness=0.9 \
    --max-drop 0.05 [--baseline <run uuid>]

python infrastructure/smoke_test.py https://<site> [--insecure] [--production]   # end-to-end check
```

## 22. REST API

The base path is `/api/v1`. Interactive docs are at `/docs`, outside production. Every endpoint
except register, login, refresh and logout needs `Authorization: Bearer <access_token>`.

Errors share one format:

```json
{ "error": { "code": "…", "message": "…", "details": {}, "request_id": "…" } }
```

The `request_id` matches the `X-Request-ID` response header and the server logs.

| Area | Endpoints |
|---|---|
| Auth | `GET /auth/config` (public: is sign-up open?); `POST /auth/register`, `/auth/login`, `/auth/refresh`, `/auth/logout`, `/auth/change-password`; `GET /auth/me` |
| Organization | `GET`/`PATCH /organization`; `GET /organization/members` |
| Users (admin) | `GET`/`POST /users`; `GET`/`PATCH`/`DELETE /users/{id}` |
| Groups | `GET`/`POST /groups`; `GET`/`PATCH`/`DELETE /groups/{id}`; `PUT /groups/{id}/members` |
| Categories | `GET`/`POST /categories`; `PATCH`/`DELETE /categories/{id}` |
| Documents | `POST /documents/upload`; `GET /documents`; `GET`/`PATCH`/`DELETE /documents/{id}`; `POST …/reprocess`; `GET …/jobs`, `…/download`, `…/preview`, `…/chunks`; `GET`/`PUT …/permissions` |
| Chat | `GET /chat/models` (models, prices, availability); `GET`/`POST /chat/conversations`; `GET`/`PATCH`/`DELETE /chat/conversations/{id}`; `POST …/{id}/messages` (SSE with `Accept: text/event-stream`, otherwise JSON; body: `content`, optional `model`, optional `edit_message_id`). Messages carry `model`, `usage` (tokens, `cost_usd`, per-step breakdown), `latency_ms` and `edit_of` |
| Search | `POST /search/hybrid`, `/search/semantic`, `/search/documents` |
| Comparisons | `POST`/`GET /comparisons`; `GET /comparisons/{id}` |
| Evaluations | `GET`/`POST /evaluations/datasets`; `DELETE …/{id}`; `GET`/`POST …/{id}/cases`; `DELETE …/cases/{case_id}`; `POST /evaluations/run`; `GET /evaluations/results`, `…/results/{id}`, `/evaluations/metrics` |
| Analytics (admin) | `GET /analytics/overview`, `/analytics/documents?days=`, `/analytics/retrieval?days=` |
| Embeddings (admin) | `GET /embeddings`; `POST /embeddings/reindex` (`stale`, `failed` or `all`) |

Example session:

```bash
API=http://localhost:3000/api/v1
TOKEN=$(curl -s $API/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"<password>"}' | jq -r .access_token)

curl -s $API/documents/upload -H "Authorization: Bearer $TOKEN" -F files=@policy.pdf

curl -s $API/search/hybrid -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"query":"How long are backups kept?","top_k":5}'

CONV=$(curl -s $API/chat/conversations -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{}' | jq -r .id)
curl -N $API/chat/conversations/$CONV/messages -H "Authorization: Bearer $TOKEN" \
  -H 'Accept: text/event-stream' -H 'Content-Type: application/json' \
  -d '{"content":"How long are backups kept?"}'
```

## 23. Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| A document stays **uploaded** | The worker isn't running. Start `arq app.workers.settings.WorkerSettings` |
| The first upload takes several minutes | The worker is downloading its models (only once) |
| Status **failed** | Read the reason on the document's **Details** tab. Encrypted or scanned PDFs need to be fixed at the source; for transient errors, use **Reprocess** |
| Chat says it couldn't find the answer | No indexed document you can see is relevant enough. Check the document is *indexed* and shared with you, or rephrase the question |
| A model is greyed out or a reply ends with a limit message | The provider refused the request because the API key's quota, spend limit or credit is used up (the message says when access returns, if the provider tells us). Pick another model, or raise the limit with the provider. A Google key on the free tier cannot use Gemini Pro-tier models at all |
| *cost n/a* under a reply | The model is not in the catalog (or has no price there). Add it to `models.json` or `LLM_CATALOG_FILE` |
| A reply has no citations | Not every message needs the documents: greetings, thanks and general questions are answered without a search. A *document* question without citations shows **Not backed by document citations** |
| Chat errors with "Answer generation is not configured" (`llm_not_configured`) | Set `LLM_PROVIDER`, `LLM_MODEL` and the matching API key, then restart the API and worker |
| `/ready` fails, or Qdrant returns 401 | Qdrant is down, or `QDRANT_API_KEY` differs between Qdrant and the app. Restart Qdrant with `start-qdrant.ps1` |
| "Too many requests" (429) | A rate limit was reached. Wait a minute |
| Locked out after wrong passwords | Wait 15 minutes, or ask an admin to set a new password |
| A page is missing from the navigation | Your role doesn't include it (see [section 12](#12-roles-and-permissions-reference)) |
| The API refuses to start in production | A production guard failed. The error names the setting (e.g. the JWT secret or the Qdrant key) |
| Changed embedding or chunk settings, and old documents are "stale" | Expected. Re-index them from **Embeddings** or with `app.cli reindex --stale` |

## 24. Glossary

| Term | Meaning |
|---|---|
| **Chunk** | A passage of a document (about 400 tokens) that is indexed and cited |
| **Embedding** | A numeric vector representing a chunk's meaning, used for semantic search |
| **BM25** | Keyword relevance scoring, used for exact terms |
| **Hybrid search** | Semantic and keyword search combined |
| **Reranker** | A model that rescores the top candidates for precision |
| **RAG** | Retrieval-Augmented Generation: answering from retrieved passages |
| **Grounded answer** | An answer supported by valid citations |
| **Abstention** | Saying "not found" instead of guessing |
| **ACL token** | A permission marker stored with each vector so search can filter by access |
| **Embedding configuration** | The combination of embedding model and chunking settings; each one has its own index |
| **Stale document** | A document indexed under an older embedding configuration |
| **Token** | The unit models read and write (roughly three quarters of a word); providers bill per million tokens, separately for input and output |
| **Superseded message** | A message hidden after the user edited an earlier one; kept in the database, never shown or sent to the model |
