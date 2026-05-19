# Clinical Note Graph

[![CI](https://github.com/tantee/clinical-note-graph/actions/workflows/ci.yml/badge.svg)](https://github.com/tantee/clinical-note-graph/actions/workflows/ci.yml)

> Repository: <https://github.com/tantee/clinical-note-graph>

A Dockerised prototype that turns inbound EMR documents into:

- a structured **patient knowledge graph** (Neo4j)
- an **Obsidian-style Markdown vault** (filesystem)
- a **PostgreSQL** record of every fact, document, AI output, and audit event
- **pgvector** embeddings for semantic search
- outbound **summary**, **coding suggestion**, and **export** APIs

> ⚠️ **AI-assisted output requires clinical review.** No diagnosis or coding suggestion is ever marked as final.

---

## Tech choices

| Layer | Choice | Why |
|---|---|---|
| Backend | **FastAPI** (Python 3.12) | Pydantic gives strict JSON-schema validation for AI output — central to the safety story. Built-in OpenAPI/Swagger at `/docs`. Native async for parallel I/O. |
| Frontend | **Vue 3 + Vuetify 4** | Per spec. Vite dev server, multi-stage prod build served by nginx. |
| Relational DB | **PostgreSQL 16 + pgvector** | One image carries relational state and embeddings. |
| Graph DB | **Neo4j 5 (community)** | Standard, mature Cypher tooling. Writes use `UNWIND` for one Cypher round-trip per fact type. |
| AI | Pluggable provider — `mock` or any OpenAI-compatible endpoint (**OpenRouter**, OpenAI, Groq, vLLM, …) | Defaults to deterministic mock so the stack runs offline. OpenRouter is the recommended production path: one key, many models. |
| Files | Bind-mounted volume `/data/vault` | Open the same folder in real Obsidian for a parallel UX. |

---

## Architecture flow

```
POST /api/emr/ingest
   ↓
[Normalize: text / JSON / FHIR → canonical text]
   ↓
[Stage 1 (Postgres tx): persist raw document + patient + encounter]
   ↓
[AI extract → strict-Pydantic-validated ClinicalExtractionResult]
   ↓
[Stage 2 (Postgres tx): persist AI output + facts via executemany; on validation
 failure, log EXTRACTION_INVALID and skip downstream writes]
   ↓
[Stage 3 (parallel): graph upserts via single Cypher session per fact type
                   + Obsidian-style Markdown generation
                   + bounded-concurrency pgvector embedding ingest]
   ↓
[Audit log + AI output snapshot]
```

The AI never writes to the database directly. Its JSON is parsed, validated, and only its **typed output** flows into the deterministic update layer.

---

## Quick start (development)

```bash
git clone <this-repo>
cd clinical-note-graph
cp .env.example .env   # default uses the offline mock AI provider

docker compose up --build
```

Endpoints:

| Service | URL | Notes |
|---|---|---|
| **Unified app (Caddy proxy)** | http://localhost | Recommended. `/` → Vue UI · `/api/*`, `/docs`, `/openapi.json`, `/redoc`, `/health`, `/ready` → backend |
| Frontend (Vite dev server) | http://localhost:5173 | Direct access; useful for full Vue devtools / HMR |
| Backend (FastAPI) | http://localhost:8000 · docs at /docs | Bypasses the proxy; handy for `curl` and integration scripts |
| Neo4j Browser | http://localhost:7474 | user `neo4j`, password `neo4jpass` |
| Postgres | localhost:5432 | exposed for local tooling |

Try the demo:

```bash
./examples/ingest.sh                                       # send sample EMRs (hits the backend directly)
open http://localhost/#/patients/HN123456                  # explore via the unified Caddy proxy
```

## Configuring the AI provider

Any OpenAI-compatible endpoint works. The backend speaks the standard `/chat/completions` and `/embeddings` shape, so `AI_PROVIDER=openai` is reused for all of them — only the `AI_BASE_URL` changes.

You can configure providers three ways:
- Edit `.env` and restart `docker compose up`.
- `PATCH /api/config` — overrides are stored in Postgres and merged into runtime settings without restarting.
- Visit `/#/config` in the UI and click **Quick setup** — it fills OpenRouter / OpenAI / Groq presets so you only need to paste your key.

You can also use a different model **per task** (extract, summary, coding) on top of a single provider — see [Per-task model overrides](#per-task-model-overrides) below.

### Provider quick-reference

| Provider | Key URL | `AI_BASE_URL` | Notes |
|---|---|---|---|
| **OpenRouter** (recommended) | <https://openrouter.ai/keys> | `https://openrouter.ai/api/v1` | One key → 200+ models incl. Claude, GPT-4o, Gemini, Llama, Qwen. Pay-per-use, no monthly commitment. Embeddings supported via `openai/text-embedding-3-*`. |
| **OpenAI** | <https://platform.openai.com/api-keys> | `https://api.openai.com/v1` | Native; best for `gpt-4o`, `gpt-4o-mini`, OpenAI embeddings. |
| **Anthropic via OpenRouter** | as above | as above | Use `AI_MODEL=anthropic/claude-3.5-sonnet` (or `claude-3.7-sonnet`, `claude-3.5-haiku`). Direct Anthropic API uses a different schema and isn't supported by this backend yet. |
| **Google Gemini via OpenRouter** | as above | as above | Use `AI_MODEL=google/gemini-2.0-flash-001` or `google/gemini-2.5-pro`. |
| **Groq** | <https://console.groq.com/keys> | `https://api.groq.com/openai/v1` | Very fast inference; good for `llama-3.3-70b-versatile`, `qwen-2.5-72b`. No embedding endpoint — leave `AI_EMBEDDING_MODEL` blank or point at OpenAI for embeddings. |
| **DeepSeek** | <https://platform.deepseek.com/api_keys> | `https://api.deepseek.com/v1` | Cheap; use `deepseek-chat` (V3) or `deepseek-reasoner` (R1). Reasoner is slow but good at structured extraction. |
| **Azure OpenAI** | Azure portal | `https://<resource>.openai.azure.com/openai/deployments/<deployment>` | Set `AI_MODEL` to your deployment name. |
| **Local (vLLM / LM Studio / Ollama)** | n/a | `http://host:port/v1` | Any OpenAI-compatible self-hosted server. From a container, use `host.docker.internal` or the LAN IP. |
| **Mock / offline** | n/a | leave blank, set `AI_PROVIDER=mock` | Deterministic keyword-based extractor with ICD-10 / SNOMED / LOINC / RxNorm lookups. Runs without a key. |

### OpenRouter setup (recommended)

OpenRouter exposes hundreds of models behind a single key, including the latest Claude, GPT, Gemini, Llama, Qwen, and DeepSeek. The cheapest way to compare models without juggling vendor accounts.

1. Get an API key at <https://openrouter.ai/keys>.
2. Edit `.env`:

   ```env
   AI_PROVIDER=openai
   AI_BASE_URL=https://openrouter.ai/api/v1
   AI_API_KEY=sk-or-v1-…
   AI_MODEL=anthropic/claude-3.5-sonnet
   AI_EMBEDDING_MODEL=openai/text-embedding-3-small
   ```

3. `docker compose up --build`. Or change it live without restart via the Config page or:

   ```bash
   curl -X PATCH http://localhost:8000/api/config \
     -H 'Content-Type: application/json' \
     -d '{"AI_PROVIDER":"openai","AI_BASE_URL":"https://openrouter.ai/api/v1","AI_API_KEY":"sk-or-v1-...","AI_MODEL":"anthropic/claude-3.5-sonnet"}'
   ```

### Per-task model overrides

Two knobs cover the basic case: `AI_MODEL` (default chat model) + `AI_EMBEDDING_MODEL` (embeddings). They apply to **every** chat call (`extract`, `summary`, `coding`) and every embed call respectively.

If you want a stronger model for the strict-JSON extract step but a cheap one for summaries, set the per-task overrides. Each falls back to `AI_MODEL` when blank:

| Env var | What it overrides |
|---|---|
| `AI_MODEL` | Default for any chat task that doesn't have a per-task override |
| `AI_MODEL_EXTRACT` | `POST /api/emr/ingest` — the strict-JSON extraction call |
| `AI_MODEL_SUMMARY` | `POST /api/patient/{id}/summary` |
| `AI_MODEL_CODING` | `POST /api/patient/{id}/coding/suggest` |
| `AI_EMBEDDING_MODEL` | Every `embed` call (per-note + per-problem) |

Example `.env`:

```env
AI_PROVIDER=openai
AI_BASE_URL=https://openrouter.ai/api/v1
AI_API_KEY=sk-or-v1-…

# Best schema-following model for the heavy extraction step…
AI_MODEL_EXTRACT=anthropic/claude-3.5-sonnet

# …a cheap model for the cosmetic summary…
AI_MODEL_SUMMARY=openai/gpt-4o-mini

# …and the default catches anything else (coding, future call types).
AI_MODEL=anthropic/claude-3.5-haiku

AI_EMBEDDING_MODEL=openai/text-embedding-3-small
```

Three ways to change them (any one works, all merge into the same effective settings):

1. **UI** — `/#/config` → AI provider card → fill in the per-task fields → Save.
2. **API** — `PATCH /api/config` with any subset of the keys:

   ```bash
   curl -X PATCH http://localhost/api/config \
     -H 'Content-Type: application/json' \
     -d '{
       "AI_MODEL": "anthropic/claude-3.5-haiku",
       "AI_MODEL_EXTRACT": "anthropic/claude-3.5-sonnet",
       "AI_MODEL_SUMMARY": "openai/gpt-4o-mini"
     }'
   ```

   The overrides land in the `app_config` table and merge into `Settings` on every read — no restart required.
3. **`.env`** — set the env vars and `docker compose up`. Persistent DB overrides (1 + 2) take precedence over env defaults.

The Debug page's *By model* table breaks down spend per actual model used, so you can A/B different choices and compare token + dollar costs side-by-side.

### Cost-effective preset stacks

Three drop-in presets ship at the repo root. Pick one based on the trade-offs below, copy its `AI_*` lines into `.env`, replace the API key, and `docker compose up` — or paste them into `PATCH /api/config` for a live swap.

| Preset file | Stack | Realized cost / encounter¹ |
|---|---|---|
| `.env.option.deepseek` | DeepSeek V3.1 + GLM-4.5-Air + DeepSeek R1 (OSS Chinese) | ~$0.013 |
| `.env.option.gemini` | Gemini 2.5 Flash + Flash-Lite + GPT-5-mini (Western, cost-only) | ~$0.011 |
| `.env.option.hybrid` | Gemini Flash + GLM-Air + GPT-5-mini (recommended mix) | **~$0.0095** |

¹ Assumes a typical encounter: extract ≈ 6K in / 1.5K out, summary ≈ 2K in / 0.7K out, coding ≈ 4K in / 0.8K answer + reasoning. OpenRouter list rates, January 2026.

#### Headline price comparison (per 1M tokens, in / out)

| Slot | DeepSeek/GLM | Gemini/GPT-5 |
|---|---|---|
| extract | `deepseek-chat-v3.1` — **$0.27 / $1.10** | `gemini-2.5-flash` — $0.30 / $2.50 |
| summary | `glm-4.5-air` — ~$0.20 / $1.10 | `gemini-2.5-flash-lite` — **$0.10 / $0.40** |
| coding | `deepseek-r1-0528` — $0.55 / $2.19 | `gpt-5-mini` — **$0.25 / $2.00** |
| embeddings | `text-embedding-3-small` — $0.02 (both) | same |

Headline rates favour DeepSeek, but R1's reasoning-token bill (often 2–5× the answer length, charged as output) flips the realized total. Bounded reasoning on `gpt-5-mini` is why the Gemini and Hybrid stacks finish cheaper end-to-end.

#### Pros / cons

**DeepSeek + GLM (`.env.option.deepseek`)**
- ✅ Lowest input-token price across the board — wins big on long-note / extract-heavy workloads.
- ✅ Open weights — can self-host the same models later for data residency / on-prem / air-gapped deployments.
- ✅ R1's chain-of-thought is genuinely strong on multi-step medical reasoning (conflicting findings, ICD specificity).
- ❌ R1 burns reasoning tokens — coding spend is unpredictable.
- ❌ Latency: R1 can take 10–30s per coding call. Fine inside the async ingest pipeline, painful for a synchronous "suggest codes now" UI.
- ❌ Weaker Western clinical priors than GPT/Gemini; expect worse ICD specificity on rare conditions.
- ❌ Data residency: OpenRouter sometimes routes DeepSeek/Z.AI through Chinese-hosted upstreams — pin providers or move off OpenRouter if you process real PHI.

**Gemini + GPT-5-mini (`.env.option.gemini`)**
- ✅ Lower realized cost on coding (gpt-5-mini's reasoning is bounded vs R1).
- ✅ Fast: Gemini Flash and GPT-5-mini both respond in 1–4s.
- ✅ Stronger medical priors — richer clinical training data.
- ✅ Gemini structured-output is the gold standard for strict JSON — fewer retry-on-malformed-JSON cycles.
- ✅ US/EU-hosted upstreams; predictable for compliance reviews.
- ❌ Closed weights — no self-host escape hatch.
- ❌ Vendor lock to two big-lab APIs — slightly more concentrated risk.

**Hybrid (`.env.option.hybrid`) — recommended**
- ✅ Gemini Flash keeps extract reliability; GLM-Air keeps summary dirt cheap with better prose than Flash-Lite; gpt-5-mini handles coding without R1's latency tax.
- ✅ Cheapest realized cost of the three documented presets.
- ❌ Three different upstream providers — more billing dashboards if you ever leave OpenRouter.

#### When to pick which

- High volume, short notes, no PHI concerns → **deepseek**. Low input prices dominate, R1's reasoning amortizes over simple cases.
- Complex cases, accuracy-sensitive coding, future PHI workload → **gemini**. Bounded reasoning, better priors, cleaner compliance.
- Best balance of cost, accuracy, and latency → **hybrid**.

After a few real ingests, check the Debug → *By model* table to see actual spend by model — if `coding` dominates, swap `AI_MODEL_CODING` for the same model you use on `extract` (typically a 30–40% total-cost cut at marginal quality impact on routine cases).

> ⚠️ Verify exact model slugs at <https://openrouter.ai/models> before applying — OpenRouter occasionally renames variants (e.g. dated suffixes like `-0528`). The slugs above match what was current at preset authoring time (2026-05).

---

## Production deployment

The production overlay (`docker-compose.prod.yml`) collapses the dev `proxy` + `frontend` services into a single **Caddy** container that:

- compiles the frontend at image-build time and serves the static bundle from `/srv`,
- reverse-proxies `/api/*`, `/docs`, `/openapi.json`, `/redoc`, `/health`, `/ready` to the backend on the internal docker network,
- handles TLS termination — when `CNG_DOMAIN` is a real public hostname, Caddy auto-fetches a Let's Encrypt cert via ACME HTTP-01 (ports 80 and 443 must be Internet-reachable),
- gzip/zstd-encodes responses, immutable-caches hashed assets, sets HSTS + `X-Frame-Options` + `X-Content-Type-Options` + `Referrer-Policy`,
- closes Postgres / Neo4j / backend ports to the outside (only the proxy is exposed),
- enforces a CORS allowlist and an `X-API-Key` header on protected endpoints,
- adds health-checks to every service and `restart: always`.

Required env vars for prod:

```env
CNG_DOMAIN=cng.example.com                     # Caddy serves this host; auto-TLS via Let's Encrypt
CADDY_EMAIL=ops@example.com                    # cert renewal notifications
VITE_API_BASE=https://cng.example.com          # baked into the frontend bundle; same host since Caddy fronts both
FRONTEND_ORIGIN=https://cng.example.com        # CORS allowlist for the backend
BACKEND_API_KEY=<long random secret>           # required for /api/emr, /api/config, /api/export, /api/facts
UVICORN_WORKERS=4
```

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Local prod testing without a real domain: set `CNG_DOMAIN=localhost` and Caddy serves plain HTTP on :80. Useful for smoke-testing the prod overlay before pointing DNS.

Operational endpoints (served through the same Caddy proxy):

- `GET /health` — 200 whenever the backend process is up.
- `GET /ready` — 200 only when Postgres responds.
- Every response includes an `X-Request-ID` header that also appears in JSON 500/422 bodies for tracing.

If you'd rather run nginx than Caddy as the front-door, the Caddyfiles are short — swap them for an equivalent `server { … proxy_pass http://backend:8000; … }` block and use `proxy_pass http://frontend:5173` (or `root /srv` in prod). Caddy was chosen here because the auto-HTTPS path is one config line and one env var.

---

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

---

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

---

## API surface

| Verb | Path | Purpose |
|---|---|---|
| POST | `/api/emr/ingest` | Accept text / JSON / FHIR EMR document. Idempotent on `(patientId, source.documentId, source.version)`. Pass `?async=true` to enqueue. |
| GET  | `/api/jobs/{jobId}` | Background job status |
| GET  | `/api/patients` | Search/list patients |
| GET  | `/api/patient/{id}` | Aggregated structured facts |
| GET  | `/api/patient/{id}/timeline` | Encounters timeline (single SQL round-trip with counts) |
| GET  | `/api/patient/{id}/encounter/{eid}/documents` | Documents for one encounter |
| GET  | `/api/patient/{id}/document/{docId}?includeRaw=false` | Raw EMR + facts + latest AI output for one document |
| GET  | `/api/patient/{id}/graph` | Graph (nodes + edges) |
| GET  | `/api/patient/{id}/notes` · `/note?path=…` | List + read vault files; backlinks included |
| POST | `/api/patient/{id}/summary` | brief / detailed / discharge / problem_oriented / timeline / coding_support |
| POST | `/api/patient/{id}/coding/suggest` | ICD-10 / SNOMED CT / LOINC / RxNorm candidates |
| PATCH| `/api/facts/{factId}/review?status=` | Set `ai_suggested` / `human_confirmed` / `rejected` |
| GET  | `/api/search?q=…&patientId=…` | Vector search across facts + notes |
| POST | `/api/export` | summary · coding · graph · markdown_vault (zip) · fhir_bundle · custom (uses export profile) |
| GET/PATCH | `/api/config` | Read effective settings (masked secrets), patch overrides |
| GET/PUT/DELETE | `/api/config/export-profiles[/{id}]` | Manage export profiles |
| GET  | `/api/jobs?status=&type=&limit=&offset=` | List background jobs (queue) |
| POST | `/api/jobs/{id}/requeue`                 | Reset a failed job to pending |
| GET  | `/api/debug/summary?start=&end=`         | KPI totals over a range |
| GET  | `/api/debug/by-model?start=&end=`        | Per-model breakdown |
| GET  | `/api/debug/by-day?start=&end=`          | Stacked-bar dataset |
| GET  | `/api/debug/ai-calls?…`                  | AI call log (filterable) |
| GET  | `/api/debug/ai-calls/{id}`               | Single call detail |
| GET  | `/api/debug/ai-calls.csv?…`              | Streamed CSV export |
| GET  | `/api/config/pricing`                    | List model rates |
| PUT  | `/api/config/pricing/{model}`            | Upsert one rate |
| DEL  | `/api/config/pricing/{model}`            | Delete a rate |
| POST | `/api/config/pricing/refresh-openrouter` | Refresh rates from OpenRouter |

Full schema at `http://localhost:8000/docs` (OpenAPI).

Examples:
- Curl walkthrough: [`examples/ingest.sh`](examples/ingest.sh)
- Cypher queries: [`examples/graph-query.cypher`](examples/graph-query.cypher)
- Coding response: [`examples/coding-response.json`](examples/coding-response.json)
- Generated vault: [`examples/example-vault/`](examples/example-vault/)

---

## Graph model

```
(Patient)-[:HAS_ENCOUNTER]->(Encounter)
(Encounter)-[:HAS_DOCUMENT]->(Document)
(Encounter)-[:MENTIONS]->(Condition)
(Encounter)-[:PRESCRIBED]->(Medication)
(Encounter)-[:HAS_OBSERVATION]->(Observation)
(Encounter)-[:HAS_PLAN]->(Plan)
(Encounter)-[:PERFORMED]->(Procedure)
(Patient)-[:HAS_ALLERGY]->(Allergy)
(Medication)-[:TREATS]->(Condition)
(Plan)-[:ADDRESSES]->(Condition)
(CodingCandidate)-[:CODES]->(Condition)
(Document)-[:EXTRACTED {evidence,confidence}]->(Condition|Medication|Observation|…)
```

All upserts are **longitudinal** — new documents add facts and link them; they never delete prior facts. Contradictions surface as `warnings[]` in `ClinicalExtractionResult` and are visible in the UI.

---

## Markdown vault layout

```
/data/vault/patients/{patientId}/
   index.md
   visits/{date}-{encounterType}.md
   problems/{slug}.md
   medications/{slug}.md
   labs/{slug}.md
   sources/{documentId}.md
```

Every file has YAML frontmatter, `[[wikilinks]]`, an Evidence section quoting the raw EMR, a Timeline, and `updatedAt`. Compatible with vanilla Obsidian — mount the volume into your Obsidian vault folder for a side-by-side workflow.

---

## AI extraction safety

- AI output is parsed into a **strict** Pydantic schema (`extra="forbid"`), so unknown fields hard-fail validation.
- If validation fails, the AI output is logged in `ai_outputs` with errors, an `EXTRACTION_INVALID` audit event is written, and **no downstream writes happen** (no facts, no graph, no markdown, no embeddings).
- Every fact carries `evidenceText`, `confidence`, and `reviewStatus` (`ai_suggested` / `human_confirmed` / `rejected`).
- Every AI call is stored in `ai_outputs`; every state change is logged in `audit_log`; raw documents are kept verbatim in `documents`.
- Coding suggestions and summaries always include a disclaimer.
- The web UI shows a persistent warning chip on every page.

---

## Testing

Three layers — all live under `backend/tests/` and `frontend/tests/`.

### Unit tests (backend, no docker)
```bash
cd backend
pip install -r requirements.txt
pytest -q
```
Covers: extraction schema strictness, mock extractor, FHIR adapter, markdown longitudinal append.

### Integration tests (backend, no docker)
Same `pytest` run. The suite includes an in-process FastAPI `TestClient` with an in-memory Postgres fake and stubbed Neo4j (`conftest.py` provides `fake_store`, `stub_neo4j`, and `app_client` fixtures), so every API endpoint is exercised end-to-end through the FastAPI ASGI app without containers. Includes:

- `test_api_ingest.py` — text ingest, idempotency, 404 semantics, encounter-documents endpoint, config patch (with secret masking), API-key middleware enforcement, summary, coding, export.
- `test_api_fhir_and_longitudinal.py` — FHIR ingest, two-document longitudinal merge.
- `test_graph_updater.py` — verifies the Cypher path issues `UNWIND` batches instead of per-fact round-trips.
- `test_embeddings_batching.py` — proves the embed step is concurrent-bounded and tolerates per-item failures.
- `test_runtime_config.py` — DB overrides are merged in without mutating the cached `Settings` singleton.

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

---

## Project layout

```
backend/
  app/
    config.py · main.py · middleware.py
    db/{postgres,neo4j_client,helpers}.py
    schemas/{common,emr,extraction,coding}.py
    services/
      ai_provider.py         (mock + OpenAI-compatible providers)
      ingest.py              (orchestrator: pre-AI tx → AI → post-AI tx → parallel side-effects)
      graph_updater.py       (UNWIND-batched Cypher in one session)
      markdown_generator.py  (entity-note helper unifies problem/med/lab writers)
      summary.py · coding.py · export.py
      patient_facts.py · fhir_adapter.py · embeddings.py · jobs.py
      runtime_config.py      (DB overrides merged into Settings — never mutates the cache)
    routers/{emr,patient,config,export,jobs}.py
    prompts/templates.py
    utils/{datetime,vault}.py
  tests/  conftest.py + 8 test modules
  db/init/001_schema.sql

frontend/
  src/
    main.js · App.vue · router.js · styles/app.css
    api/client.js              (axios + global error toast + X-API-Key)
    constants/clinical.js      (mirrors backend Literal types)
    utils/format.js
    stores/ui.js               (Pinia: theme, snackbar)
    views/{Patients,PatientDetail,Ingest,Config}.vue
    components/{MarkdownViewer,GraphView,Timeline,SectionHeader,EmptyState,FactCard}.vue
  tests/  setup.js · utils.format.spec.js · MarkdownViewer.spec.js · e2e/

sample-data/  3 text EMRs + 1 FHIR bundle
examples/     curl walkthrough, sample coding response, cypher queries, vault tree
scripts/      e2e.sh
docker-compose.yml + docker-compose.prod.yml
```

---

## What changed since MVP first draft

A full review-and-polish pass landed before this release. Highlights:

**Correctness**
- Ingest is now three crisp stages, each in its own Postgres transaction; AI validation failures stop downstream writes instead of writing empty results.
- `Settings` singleton is no longer mutated by config patches — overrides are merged into a fresh object at read time (`runtime_config.effective()`).
- Fixed a real collision bug where two ingests in the same second produced the same auto-generated encounter_id.
- `MarkdownViewer` no longer registers a global `document.click` listener that leaked across mounts; wikilinks now actually emit `open` to the parent.
- Audit log payloads use `json.dumps`, not string concatenation.
- API-key middleware returns a proper 401 JSON response instead of relying on `raise` inside `BaseHTTPMiddleware`.

**Performance**
- Neo4j writes use `UNWIND $rows` in one session per ingest (was 30+ round-trips for a typical document).
- Postgres fact inserts use `executemany` (was one round-trip per fact).
- Embeddings run with bounded concurrency and a single batched insert.
- Frontend `PatientDetail` loads the four independent endpoints in parallel; in-flight requests are aborted on navigation away.
- Tab content is lazy — Graph and AI-output tabs don't mount until selected.
- Neo4j constraints are created once at startup, gated by a module-level flag (was re-running on every ingest).

**Security / deployment**
- CORS reads a comma-separated allowlist from `CORS_ORIGINS`. Production overlay requires a real origin.
- Optional `X-API-Key` middleware on `/api/emr`, `/api/config`, `/api/export`, `/api/facts`.
- Multi-stage frontend Dockerfile + nginx with gzip, immutable asset caching, security headers.
- Backend runs as a non-root user in the image.
- `X-Request-ID` middleware + request log line on every response.
- `lifespan` replaces deprecated `on_event`; startup constraint loop runs in a thread, not blocking the loop.

**UI / UX**
- Light + dark theme with a persisted toggle.
- Pinia `ui` store powers a global error snackbar for all axios failures.
- Empty states, skeleton-equivalent loading, accessible button labels, sticky AI-assisted warning chip.
- Reusable `SectionHeader`, `EmptyState`, `FactCard` components.
- Graph view has a legend and Fit-to-view; cleanup-on-unmount is correct.

**Tests**
- 36 backend tests (unit + integration with in-memory fake stores).
- E2E smoke (`scripts/e2e.sh`) against the real compose stack.
- Frontend Vitest + Vue Test Utils + Playwright browser E2E.
