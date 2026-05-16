# Async ingest queue, unified ingest UI, and debug page

**Status:** Approved · 2026-05-16
**Scope:** Three interrelated features delivered together; one design, one implementation plan.

## Summary

Bring three changes to the Clinical Note Graph prototype:

1. **Postgres-backed async job queue.** AI-driven ingest is moved off the request path. Jobs survive uvicorn restart, support retries with exponential backoff, and emit per-stage progress.
2. **Unified `/ingest` page.** Single ID-driven form replaces the existing manual ingest screen; backend auto-detects new vs. existing patient (already idempotent), the UI shows a live status hint as the user types the ID. After submit, an in-page JobWatcher polls the job and navigates to the patient on success.
3. **Debug / observability page.** A new `/#/debug` route exposes total spend, per-model cost breakdown, an AI-call live tail, and a jobs view with re-queue. Pricing rates live in a `model_pricing` table that admins edit in the Config page or refresh from OpenRouter's `/models` endpoint.

These three features share a foundation: the queue's `jobs` table, the new `ai_outputs` columns, and the `model_pricing` table. They will ship as one branch.

## Goals

- Ingest must not block the HTTP request when a real provider is used (10–60 s typical extract).
- Jobs are durable: a uvicorn restart in the middle of an ingest does not lose work.
- Every AI call is captured with prompt/completion tokens, latency, and computed USD cost.
- The clinician-facing manual-input flow is a single page; no mode toggle.
- A debug page surfaces cost and call history without forcing operators into Postgres.

## Non-goals

- Distributed / multi-worker-pool scaling. One uvicorn instance with N in-process workers is sufficient for the prototype.
- Authn / authz beyond the existing `X-API-Key` middleware.
- Per-clinician audit dashboards (no users table yet).
- Job priorities beyond a simple integer column (we will not implement scheduling tiers).

## Glossary

- **Job** — a row in `jobs`, e.g. an `emr_ingest`. May trigger one or more AI calls.
- **AI call** — a single round-trip to a chat/embedding endpoint, recorded as one row in `ai_outputs`.
- **Worker** — an asyncio task inside the uvicorn process that picks jobs and runs them.

---

## Architecture

### Process layout

```
┌──────────── uvicorn process ────────────┐
│  FastAPI routers (HTTP)                 │
│  ┌────────────────────────────────────┐ │
│  │ Queue workers (N asyncio tasks)    │ │
│  │  loop: claim → run → finalize      │ │
│  └────────────────────────────────────┘ │
└──────────────────────────────────────────┘
              │
              ▼
   Postgres (jobs, ai_outputs, model_pricing, …)
   Neo4j     (graph upserts)
   Vault     (markdown filesystem)
```

Workers are started in the FastAPI `lifespan` and drained on shutdown. They share the same async event loop as the request handlers; long blocking work (Postgres, Neo4j) is already wrapped in `asyncio.to_thread` and stays that way.

### Data flow — async ingest

```
POST /api/emr/ingest        →  enqueue (insert jobs row, status=pending)
                                returns { jobId, status: "queued" }

worker:
  claim                      →  UPDATE … SET status='running', locked_by, locked_until
  stage_persisted            →  pre-AI transaction (patient/encounter/document)
  stage_ai_extract           →  call provider; capture usage; write ai_outputs row
  stage_facts                →  post-AI transaction (facts inserted)
  stage_graph                →  Neo4j UNWIND batches
  stage_markdown             →  vault writes
  stage_embed                →  embedding calls + INSERT
  finalize                   →  UPDATE jobs SET status='completed', result, finished_at

GET /api/jobs/{jobId}       →  current status + progress object
```

`progress` is updated after each stage as a JSONB object so the UI can show "running (extract)" → "running (markdown)".

---

## Schema changes

All migrations land in `backend/db/init/002_async_and_debug.sql`. The fresh-database init script picks them up automatically because Postgres runs `*.sql` in lexical order. For an already-bootstrapped DB, the same SQL is idempotent (`ALTER TABLE … ADD COLUMN IF NOT EXISTS`, `CREATE TABLE IF NOT EXISTS`), so applying it manually upgrades in place.

### `jobs` — extended

```sql
ALTER TABLE jobs
  ADD COLUMN IF NOT EXISTS attempts        INT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS max_attempts    INT NOT NULL DEFAULT 3,
  ADD COLUMN IF NOT EXISTS locked_by       TEXT,
  ADD COLUMN IF NOT EXISTS locked_until    TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS priority        INT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS next_run_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  ADD COLUMN IF NOT EXISTS progress        JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS jobs_claimable_idx
  ON jobs (priority DESC, next_run_at)
  WHERE status IN ('pending', 'running');
```

A worker claims with:

```sql
WITH claimed AS (
  SELECT job_id FROM jobs
  WHERE status = 'pending' AND next_run_at <= now()
     OR (status = 'running' AND locked_until < now())     -- crashed worker
  ORDER BY priority DESC, next_run_at
  FOR UPDATE SKIP LOCKED
  LIMIT 1
)
UPDATE jobs
   SET status = 'running',
       locked_by = :worker_id,
       locked_until = now() + interval '2 minutes',
       started_at = COALESCE(started_at, now()),
       attempts = attempts + 1
 WHERE job_id IN (SELECT job_id FROM claimed)
 RETURNING *;
```

`locked_until` is heartbeated every 30 s while a stage runs (one extra `UPDATE` per stage transition).

### `ai_outputs` — extended

```sql
ALTER TABLE ai_outputs
  ADD COLUMN IF NOT EXISTS job_id           UUID,
  ADD COLUMN IF NOT EXISTS call_type        TEXT,           -- extract|summary|coding|embed
  ADD COLUMN IF NOT EXISTS prompt_tokens    INT,
  ADD COLUMN IF NOT EXISTS completion_tokens INT,
  ADD COLUMN IF NOT EXISTS total_tokens     INT,
  ADD COLUMN IF NOT EXISTS latency_ms       INT,
  ADD COLUMN IF NOT EXISTS cost_usd         NUMERIC(10,6),
  ADD COLUMN IF NOT EXISTS error            TEXT;

CREATE INDEX IF NOT EXISTS ai_outputs_time_idx ON ai_outputs (created_at DESC);
CREATE INDEX IF NOT EXISTS ai_outputs_job_idx  ON ai_outputs (job_id);
```

### `model_pricing` — new

```sql
CREATE TABLE IF NOT EXISTS model_pricing (
  model              TEXT PRIMARY KEY,
  prompt_per_1m      NUMERIC(10,4),
  completion_per_1m  NUMERIC(10,4),
  embedding_per_1m   NUMERIC(10,4),
  source             TEXT,                                  -- 'seed'|'openrouter'|'manual'
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO model_pricing (model, prompt_per_1m, completion_per_1m, embedding_per_1m, source) VALUES
  ('gpt-4o-mini',                       0.15,  0.60,  NULL, 'seed'),
  ('gpt-4o',                            2.50, 10.00,  NULL, 'seed'),
  ('anthropic/claude-3.5-sonnet',       3.00, 15.00,  NULL, 'seed'),
  ('anthropic/claude-3.5-haiku',        0.80,  4.00,  NULL, 'seed'),
  ('google/gemini-2.0-flash-001',       0.075, 0.30,  NULL, 'seed'),
  ('text-embedding-3-small',            NULL,  NULL,  0.02, 'seed'),
  ('openai/text-embedding-3-small',     NULL,  NULL,  0.02, 'seed'),
  ('deepseek-chat',                     0.27,  1.10,  NULL, 'seed'),
  ('mock',                              0.00,  0.00,  0.00, 'seed')
ON CONFLICT (model) DO NOTHING;
```

Cost for one call is computed at write time:

```
cost_usd =   (prompt_tokens     / 1e6) * prompt_per_1m
           + (completion_tokens / 1e6) * completion_per_1m
           + (embedding_tokens  / 1e6) * embedding_per_1m
```

If the model is missing from `model_pricing`, `cost_usd` is stored as `NULL` and the debug page renders `?`.

---

## Backend

### New module: `services/queue.py`

```
class QueueWorker:
    def __init__(self, worker_id, session_factory, registry):
    async def run_forever(self): ...
    async def _claim_one(self) -> Job | None: ...
    async def _run(self, job: Job): ...
    async def _heartbeat(self, job): ...
    async def _finalize(self, job, result | error): ...
    async def stop(self): ...

# Registry maps job.type -> async handler:
JOB_HANDLERS = {
    "emr_ingest": run_ingest_job,   # already exists, refactored to update progress
}
```

- Workers count: `QUEUE_WORKERS` env (default 2). Started in `app.main.lifespan`. Cancelled on shutdown; in-flight job has up to `JOB_GRACE_SECONDS` (default 15) to finish or it's rolled back to `pending`.
- Backoff on failure: `next_run_at = now() + interval '5 seconds' * (2 ^ attempts)`, capped at 5 minutes; after `max_attempts` it stays `failed`.
- A failed job can be re-queued via `POST /api/jobs/{id}/requeue` which resets `attempts=0`, `status='pending'`, clears the lock.

### `services/ingest.py` — refactor

Today `run_ingest` is `async` and runs synchronously inside the request. Refactor into:

```
async def run_ingest_pipeline(req, *, on_progress=...) -> dict:
    stage_persisted   = await asyncio.to_thread(_persist_pre_extraction, req)
    on_progress("stage_persisted", ...)

    raw_output, ai_meta = await provider.extract(...)            # NEW: returns (output, AICallRecord)
    on_progress("stage_ai_extract", tokens=..., cost=...)

    await asyncio.to_thread(_persist_post_extraction, ...)
    on_progress("stage_facts", count=...)

    await asyncio.gather(
        asyncio.to_thread(update_graph_for_document, ...),
        asyncio.to_thread(generate_markdown, ...),
    )
    on_progress("stage_graph_and_markdown", ...)

    await embed_and_store_many(...)
    on_progress("stage_embed", count=...)

    return summary
```

`POST /api/emr/ingest` becomes:

- `?async=false` → still runs `run_ingest_pipeline` synchronously (kept for E2E tests).
- default (and `?async=true`) → enqueues into `jobs`, returns `{ jobId, status: "queued", patientId, documentId }`.

### `services/ai_provider.py` — instrumentation

```
@dataclass
class AICallRecord:
    call_type: str
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    latency_ms: int
    cost_usd: Decimal | None
    raw_response: dict
    error: str | None

class AIProvider:
    async def extract(self, ..., job_id: str | None = None) -> tuple[dict, AICallRecord]: ...
    async def summarize(self, ..., job_id=None) -> tuple[str, AICallRecord]: ...
    async def suggest_coding(self, ..., job_id=None) -> tuple[dict, AICallRecord]: ...
    async def embed(self, text, ..., job_id=None) -> tuple[list[float], AICallRecord]: ...
```

`OpenAICompatibleProvider` reads `response.json()['usage']` (OpenAI-compatible APIs return it). `MockProvider` synthesises tokens (`len(text.split()) * 1.3` rounded up) so dev metering is non-zero.

A small `services/pricing.py` exposes `compute_cost(model, prompt_tokens, completion_tokens, embedding_tokens) -> Decimal | None`, used both by providers and by the OpenRouter refresh endpoint.

The provider also owns the `INSERT INTO ai_outputs` call (it has all the data + the cost calc). Callers no longer write to `ai_outputs` directly; they receive the record back and the row is already persisted.

### New routes

```
POST   /api/jobs/{id}/requeue
GET    /api/jobs?status=&type=&limit=&offset=                 # extend existing

GET    /api/debug/summary?from=&to=
GET    /api/debug/by-model?from=&to=
GET    /api/debug/ai-calls?from=&to=&model=&status=&q=&limit=&offset=
GET    /api/debug/ai-calls/{id}
GET    /api/debug/ai-calls.csv?from=&to=&model=&status=&q=    # streamed

GET    /api/config/pricing
PUT    /api/config/pricing/{model}                            # upsert one row
DELETE /api/config/pricing/{model}
POST   /api/config/pricing/refresh-openrouter
```

All gated under the same `X-API-Key` prefix list (extend `middleware._PROTECTED_PREFIXES` with `/api/debug`).

### `middleware._PROTECTED_PREFIXES` update

```python
_PROTECTED_PREFIXES = ("/api/emr", "/api/config", "/api/export", "/api/facts", "/api/debug")
```

`/api/jobs` stays open (no PII, status-only).

---

## Frontend

### Unified `IngestView.vue`

- Replace the existing two-column form with the layout below.
- `v-autocomplete` bound to `listPatients(q)` returns `{patient_id, name, gender, birth_date}` rows.
- Local state `enteredPatientId`, derived `existing = patients.find(p => p.patient_id === enteredPatientId)`.
- Hint chip below the field: "Updating HN123 — Somchai Sample" or "New patient will be created".
- On submit:
  - `await ingest(body)` returns `{ jobId, status, patientId, ... }`.
  - The right column flips to a `<JobWatcher :jobId="…">` component.
  - The form is disabled until either the watcher succeeds (navigate to the patient page after a 1-s success banner) or fails (re-enable, show error).

### New `<JobWatcher>` component

- Props: `jobId`.
- Polls `/api/jobs/{jobId}` every 1500 ms until status ∈ `{completed, failed, cancelled}`.
- Reads `progress` JSONB to render a stage strip:
  ```
  ✓ saved · ◐ extract · — facts · — graph · — markdown · — embed
  ```
- Shows running totals from any AI calls already linked to the job (`/api/debug/ai-calls?job_id=…&limit=10`): tokens, $ so far, latency.
- On success: emits `done(patientId)`. On failure: emits `failed(error)` with a Retry button calling `POST /api/jobs/{id}/requeue`.

### Routes / nav

- `/#/debug` → new `DebugView.vue`. Nav bar adds a `Debug` link, hidden when the page errors with 401 (i.e. user hasn't set their `X-API-Key`).
- Patient-detail page gets `[+ Add note]` linking to `/#/ingest?patientId=HN…`.

### `DebugView.vue`

- Tabs: Overview · AI calls · Jobs (Vuetify `v-tabs` + `v-window`).
- Date-range dropdown (last 24 h / 7 d / 30 d / 90 d / custom).
- Tab 1: 4 KPI cards on top, a stacked-bar chart (use Chart.js via `chart.js` + `vue-chartjs` — already a thin add), and a sortable table of per-model spend.
- Tab 2: filter bar + virtualised `v-data-table-server` driven by `/api/debug/ai-calls`; row click opens a `v-navigation-drawer` on the right with the call detail. Auto-refresh toggle (5 s) when the tab is active.
- Tab 3: same shape, hitting `/api/jobs?…`; row drawer shows the per-stage timeline parsed out of `progress`. Re-queue button on failed rows.

### Config page additions

- New "Model pricing" card under "AI provider".
  - `v-data-table` editable inline (Vuetify pattern: edit-on-click), columns: model · prompt $/1M · completion $/1M · embedding $/1M · source · updated.
  - `Add row` and `Delete row` buttons.
  - `Refresh from OpenRouter` button → calls `POST /api/config/pricing/refresh-openrouter`, shows toast with `{ upserted: N }` result.

---

## Testing

### Backend

New pytest modules:

- `tests/test_queue_worker.py`
  - `test_claim_skips_locked_rows` — two workers, one job, only one wins.
  - `test_failed_job_retries_with_backoff` — handler raises, attempts increment, next_run_at advances.
  - `test_max_attempts_marks_failed` — after `max_attempts`, status stays `failed`.
  - `test_stale_lock_reclaimed` — set `locked_until` in the past, worker picks the row up.
  - `test_progress_updates_visible` — handler writes progress, query reflects it.

- `tests/test_ingest_async.py`
  - `test_ingest_async_returns_jobid_and_processes` — POST returns `queued`, polling job hits `completed`, patient is created.
  - `test_ingest_async_failure_records_error` — patch the provider to throw, job ends `failed` with error captured.

- `tests/test_ai_metering.py`
  - `test_openai_provider_records_tokens_and_cost` — fake httpx returns `usage`, an ai_outputs row is written with non-null cost.
  - `test_mock_provider_synthesises_tokens` — non-zero tokens recorded.
  - `test_unknown_model_cost_is_null` — clears the seeded pricing row, cost ends up NULL.

- `tests/test_debug_endpoints.py`
  - `test_summary_aggregates_by_date_range`
  - `test_by_model_breakdown_excludes_models_outside_range`
  - `test_ai_calls_paginate_and_filter`
  - `test_ai_calls_csv_streams`

- `tests/test_pricing_routes.py`
  - `test_upsert_pricing_row`
  - `test_delete_pricing_row`
  - `test_openrouter_refresh_upserts_matching_models` — patch `httpx.AsyncClient.get` to return a fixture, assert rows upserted with `source='openrouter'`.

Existing `tests/test_api_ingest.py` is extended:

- `test_ingest_sync_still_works` — `?async=false` returns 200 immediately with summary.
- `test_ingest_default_is_async` — default flag returns 200 with `status='queued'` and a jobId.

### Frontend

- `tests/JobWatcher.spec.js` — mounts the component with a stubbed API client; first poll returns `running` (renders stage strip), second returns `completed` (emits `done`).
- `tests/IngestView.spec.js` — fills the form, mocks `ingest()` to return a jobId, asserts the JobWatcher appears and the form is disabled.
- `tests/DebugView.spec.js` — mounts the page with three tabs, asserts the KPI cards render the numbers from a stubbed `/api/debug/summary` response.

### E2E

`tests/test_e2e_smoke.py` is extended:

- After ingest, poll `/api/jobs/{jobId}` until completed (already-done test, but the watcher path is exercised).
- Hit `/api/debug/summary` and assert `total_calls >= 1`.
- Hit `/api/debug/ai-calls` and assert at least one extract-type row exists.

`scripts/e2e.sh` unchanged.

`frontend/tests/e2e/` Playwright suite adds:

- `tests/e2e/debug.spec.js` — open `/#/debug`, assert KPI cards visible after at least one ingest.

CI changes: none — the existing pipeline runs all new tests.

---

## Migration / rollout

- This branch ships all three features together. The `002_async_and_debug.sql` migration is additive and reversible (`DROP COLUMN`, `DROP TABLE`).
- On first boot after the change, queue workers spin up empty; any in-flight job that pre-dates the upgrade is in the old `running` state with no `locked_by` → workers will pick those up because `locked_until IS NULL` matches the "claim crashed worker" branch.
- Existing API consumers calling `POST /api/emr/ingest` without `?async=…` will now get an async response shape (`status='queued'`, no `summary` field). To preserve sync behaviour for the example curl script, we add `?async=false` to `examples/ingest.sh` so it keeps printing the full summary inline. Documented in the README.

## Risk / mitigations

| Risk | Mitigation |
|---|---|
| Worker crash mid-stage leaves a partially-written job | `locked_until` heartbeat + the "running with expired lock" claim branch lets the next worker pick it back up; idempotent upserts mean re-running is safe. |
| Cost rates drift over time | `model_pricing` is editable + the OpenRouter refresh; rate older than 90 days is highlighted in the UI. |
| Token counts missing on some providers | If `usage` is absent from the response, we store NULLs and skip cost calc rather than reject the call. The debug page renders `–` for missing values. |
| Mock provider inflates dev cost dashboard | `mock` is seeded with $0 rates and the mock implementation reports zero cost. |
| Large debug queries | `/api/debug/ai-calls` is paginated (`limit` default 50, max 500); the CSV endpoint streams. |

## Open questions

- Should re-queue be limited to admins? (Defer — same gate as everything else is fine for prototype.)
- Should we emit Server-Sent Events for job progress instead of polling? (Defer — polling is 5 calls per job at the typical 1.5 s cadence; cost is negligible. Revisit if jobs grow into the minute range.)
- Should the debug page also show pgvector embedding-storage usage? (Out of scope; track separately if needed.)
