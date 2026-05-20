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

## Documentation

| Page | When to read |
|---|---|
| [docs/ai-providers.md](docs/ai-providers.md) | Configure OpenRouter / OpenAI / Groq / self-host; per-task model overrides; cost-effective preset stacks. |
| [docs/api.md](docs/api.md) | HTTP API surface, Neo4j graph model, markdown vault layout. |
| [docs/operations.md](docs/operations.md) | Async ingest queue, cost tracking, the test pyramid (unit → integration → e2e). |
| [docs/deployment.md](docs/deployment.md) | Production overlay (Caddy + Let's Encrypt), required env vars, operational endpoints. |
| [docs/compliance.md](docs/compliance.md) | **Read before pointing at real data.** PHI dataflow audit, HIPAA / GDPR / PDPA framing, de-identification, regulatory posture. |
| [docs/changelog.md](docs/changelog.md) | Notable structural changes since the MVP first draft. |

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

The AI never writes to the database directly. Its JSON is parsed, validated, and only its **typed output** flows into the deterministic update layer. Every outbound payload also passes through a HIPAA Safe Harbor redactor — see [docs/compliance.md](docs/compliance.md).

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

To point at a real AI provider, edit `.env` and follow [docs/ai-providers.md](docs/ai-providers.md).

---

## AI extraction safety

- AI output is parsed into a **strict** Pydantic schema (`extra="forbid"`), so unknown fields hard-fail validation.
- If validation fails, the AI output is logged in `ai_outputs` with errors, an `EXTRACTION_INVALID` audit event is written, and **no downstream writes happen** (no facts, no graph, no markdown, no embeddings).
- Every fact carries `evidenceText`, `confidence`, and `reviewStatus` (`ai_suggested` / `human_confirmed` / `rejected`).
- Every AI call is stored in `ai_outputs`; every state change is logged in `audit_log`; raw documents are kept verbatim in `documents`.
- Outbound AI payloads are de-identified by default — see [docs/compliance.md](docs/compliance.md).
- Coding suggestions and summaries always include a disclaimer.
- The web UI shows a persistent warning chip on every page.

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
      deidentify.py          (HIPAA Safe Harbor redactor)
      ingest.py              (orchestrator: pre-AI tx → AI → post-AI tx → parallel side-effects)
      graph_updater.py       (UNWIND-batched Cypher in one session)
      markdown_generator.py  (entity-note helper unifies problem/med/lab writers)
      summary.py · coding.py · export.py
      patient_facts.py · fhir_adapter.py · embeddings.py · jobs.py
      runtime_config.py      (DB overrides merged into Settings — never mutates the cache)
    routers/{emr,patient,config,export,jobs}.py
    prompts/templates.py
    utils/{datetime,vault}.py
  tests/  conftest.py + test modules (unit + integration + de-identify)
  db/init/00*_schema.sql

frontend/
  src/
    main.js · App.vue · router.js · styles/app.css
    api/client.js              (axios + global error toast + X-API-Key)
    constants/clinical.js      (mirrors backend Literal types)
    utils/format.js
    stores/ui.js               (Pinia: theme, snackbar)
    views/{Patients,PatientDetail,Ingest,Config}.vue
    components/{MarkdownViewer,GraphView,Timeline,SectionHeader,EmptyState,FactCard}.vue
  tests/  setup.js · *.spec.js · e2e/

docs/         topical documentation (see Documentation index above)
sample-data/  3 text EMRs + 1 FHIR bundle
examples/     curl walkthrough, sample coding response, cypher queries, vault tree
scripts/      e2e.sh
docker-compose.yml + docker-compose.prod.yml
```
