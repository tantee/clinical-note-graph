# Clinical Graph Notes

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
| Frontend | **Vue 3 + Vuetify 3** | Per spec. Vite dev server, multi-stage prod build served by nginx. |
| Relational DB | **PostgreSQL 16 + pgvector** | One image carries relational state and embeddings. |
| Graph DB | **Neo4j 5 (community)** | Standard, mature Cypher tooling. Writes use `UNWIND` for one Cypher round-trip per fact type. |
| AI | Pluggable provider (`mock` / OpenAI-compatible / Ollama / custom) | Defaults to deterministic mock so the stack runs offline with no key. |
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

| Service | URL |
|---|---|
| Backend REST + OpenAPI | http://localhost:8000 · docs at http://localhost:8000/docs |
| Frontend (Vue/Vuetify) | http://localhost:5173 |
| Neo4j Browser | http://localhost:7474 (user `neo4j`, password `neo4jpass`) |
| Postgres | localhost:5432 |

Try the demo:

```bash
./examples/ingest.sh                                       # send sample EMRs
open http://localhost:5173/#/patients/HN123456             # explore
```

To use a real model, edit `.env`:

```env
AI_PROVIDER=openai
AI_BASE_URL=https://api.openai.com/v1   # or http://ollama:11434/v1
AI_API_KEY=sk-…
AI_MODEL=gpt-4o-mini
AI_EMBEDDING_MODEL=text-embedding-3-small
```

Ollama is shipped as an opt-in profile:

```bash
docker compose --profile ollama up -d ollama
docker compose exec ollama ollama pull llama3.1
```

---

## Production deployment

A production overlay sits at `docker-compose.prod.yml`. It:

- removes dev bind-mounts and the uvicorn `--reload`,
- runs the frontend as a multi-stage nginx-served static bundle (port 80),
- closes the Postgres / Neo4j / backend ports to the outside (only nginx is exposed),
- enforces a CORS allowlist and an `X-API-Key` header on protected endpoints,
- adds health-checks to every service and `restart: always`.

Required env vars for prod:

```env
VITE_API_BASE=https://api.cng.example.com      # public backend URL baked into the frontend bundle
FRONTEND_ORIGIN=https://cng.example.com         # CORS allowlist for the backend
BACKEND_API_KEY=<long random secret>            # required for /api/emr, /api/config, /api/export, /api/facts
UVICORN_WORKERS=4
```

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Operational endpoints: `GET /health` (always 200 when process is up), `GET /ready` (200 only when Postgres responds). Every response includes an `X-Request-ID` header that also appears in JSON 500/422 bodies for tracing.

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
