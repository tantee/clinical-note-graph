# Operations

> **Audience:** operators and contributors running the stack day-to-day.

Runtime behaviour: how ingest jobs are scheduled, how cost is tracked, and how the test suite is layered. For deployment configuration (dev + prod), see [docs/deployment.md](deployment.md). For failure-mode recovery, see [docs/troubleshooting.md](troubleshooting.md).

## Async ingest

`POST /api/emr/ingest` defaults to **asynchronous** processing. The request returns immediately with:

```json
{ "jobId": "…", "status": "queued", "patientId": "HN1", "encounterId": "", "documentId": "doc-001", "summary": null }
```

Poll `GET /api/jobs/{jobId}` until the status is `completed` or `failed`. The `progress` JSONB field on the job row updates per stage (`stage_persisted` → `stage_ai_extract` → `stage_facts` → `stage_graph_and_markdown` → `stage_embed`), and `stage_ai_extract` carries the model, tokens, latency, and cost. The UI's `JobWatcher` component shows this live.

For inline behaviour (no queue), append `?async=false`:

```bash
curl -X POST 'http://localhost/api/emr/ingest?async=false' -H 'Content-Type: application/json' -d @body.json
```

The sync response includes a `summary` block with the extracted counts and a list of generated Markdown files. Used by `examples/ingest.sh`.

## Cost tracking

Every AI call is metered into the `ai_outputs` table: `prompt_tokens`, `completion_tokens`, `total_tokens`, `latency_ms`, `cost_usd`. Cost is computed at write time using rates from the `model_pricing` table, which:

- Ships with seed rows for current major models (gpt-4o, gpt-4o-mini, claude-3.5-sonnet/haiku, gemini-2.0-flash, deepseek-chat, text-embedding-3-small, …).
- Is editable per-row in the Config page (Model pricing card).
- Supports a single-click "Refresh from OpenRouter" button that fetches `https://openrouter.ai/api/v1/models` and upserts pricing for any matching IDs.

Models without a pricing row record a NULL cost and the debug page renders `?` in the Cost column. The mock provider is seeded at $0 so dev runs don't inflate spend totals.

The Debug page (`/#/debug`) surfaces:

- Total spend / AI calls / avg latency / failures over a chosen date range.
- Per-model breakdown table (calls, tokens, cost).
- A virtualised AI-calls log with model / status / text filters, plus a streamed CSV export.
- A jobs view with a re-queue button for failed jobs.

All `/api/debug/*` endpoints are protected by the same `X-API-Key` middleware as `/api/config` and `/api/emr`.

## Testing

Three layers — all live under `backend/tests/` and `frontend/tests/`.

### Unit tests (backend, no docker)
```bash
cd backend
pip install -r requirements.txt
pytest -q
```
Covers: extraction schema strictness, mock extractor, FHIR adapter, markdown longitudinal append, and the de-identification recognisers / pseudonym map.

### Integration tests (backend, no docker)
Same `pytest` run. The suite includes an in-process FastAPI `TestClient` with an in-memory Postgres fake and stubbed Neo4j (`conftest.py` provides `fake_store`, `stub_neo4j`, and `app_client` fixtures), so every API endpoint is exercised end-to-end through the FastAPI ASGI app without containers. Includes:

- `test_api_ingest.py` — text ingest, idempotency, 404 semantics, encounter-documents endpoint, config patch (with secret masking), API-key middleware enforcement, summary, coding, export.
- `test_api_fhir_and_longitudinal.py` — FHIR ingest, two-document longitudinal merge.
- `test_graph_updater.py` — verifies the Cypher path issues `UNWIND` batches instead of per-fact round-trips.
- `test_embeddings_batching.py` — proves the embed step is concurrent-bounded and tolerates per-item failures.
- `test_runtime_config.py` — DB overrides are merged in without mutating the cached `Settings` singleton.
- `test_deidentify_*.py` — per-recognizer redaction, per-request pseudonym determinism, and outbound-payload PHI-leak assertions on every AI call type.

### End-to-end smoke (live stack)
```bash
./scripts/e2e.sh
```
Boots the full compose stack, waits for `/health`, and runs `pytest -m e2e` from inside the backend container against the real Postgres + Neo4j + pgvector + markdown vault. Use `API_KEY=somethingsecret ./scripts/e2e.sh` to also exercise the API-key middleware.

### Frontend
```bash
cd frontend
npm install
npm run test        # Vitest: utils + MarkdownViewer
npm run e2e         # Playwright: ingest → patient view (needs the stack up)
```

The Playwright suite drives the real browser through the ingest form, opens the patient page, and asserts the timeline + notes render. It assumes the stack is already up (`docker compose up -d`); set `E2E_BASE_URL` to point at a different host if needed.
