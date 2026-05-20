# Development guide

> **Audience:** contributors editing the code. For deploying the stack (dev compose, staging, prod), see [docs/deployment.md](deployment.md).

How to run, debug, and extend the codebase day-to-day. Read this before opening your first PR.

---

## Repo orientation

```
backend/
  app/
    main.py              FastAPI app + startup hooks
    config.py            Settings (pydantic-settings, env-driven)
    middleware.py        request ID, API key, error JSON wrapper
    db/                  Postgres + Neo4j clients
    schemas/             Pydantic models (extraction.py is the AI contract)
    routers/             one file per resource — endpoints map 1:1 to lines
    services/            business logic; nothing in routers writes data directly
    prompts/templates.py system + user prompts for each AI call type
  tests/                 pytest; conftest.py owns the in-memory fakes
  db/init/               numbered SQL migrations — applied in order on first boot

frontend/
  src/
    api/client.js        axios + X-API-Key + error snackbar
    constants/clinical.js Literals that mirror backend enums
    components/          single-responsibility, prop-driven
    views/               page-level Vue components mapped to routes
    stores/              Pinia (`ui` carries theme + snackbar)
  tests/                 vitest + Vue Test Utils; e2e/ is Playwright

docs/                    you are here
sample-data/             3 text EMRs + 1 FHIR bundle — sanity-check ingest end-to-end
examples/                curl walkthrough, Cypher snippets, vault output sample
scripts/e2e.sh           boots the full stack and runs the e2e suite
```

The orchestration layer is `backend/app/services/ingest.py`. Anything new in the ingest flow lands there.

---

## Local dev loop

Three modes, pick the one that fits what you're touching.

### A) Full stack via docker compose (default)

The flow described in [docs/deployment.md → Development](deployment.md#development). Both backend and frontend hot-reload from bind-mounted source. Use this for end-to-end UI work, anything that touches the queue, anything that needs a real Postgres/Neo4j.

```bash
docker compose up                          # foreground; ctrl-C stops everything
docker compose logs -f backend             # tail just the backend
docker compose restart backend             # re-read env changes
```

### B) Backend in your terminal, deps in docker

Faster Python iteration (no container layer for the reload), and you can attach a debugger directly.

```bash
# 1. Start only the data services.
docker compose up -d postgres neo4j

# 2. Point Settings at the docker-mapped ports.
export POSTGRES_HOST=localhost POSTGRES_PORT=5432
export NEO4J_URI=bolt://localhost:7687

# 3. Run the backend in a virtualenv.
cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m spacy download en_core_web_sm   # the de-identifier wants this; one-time
uvicorn app.main:app --reload --port 8000
```

To skip the +500 MB NER install while iterating on something unrelated, install the slim set instead and set `DEIDENTIFY_LEVEL=regex_only` — the redactor degrades gracefully:

```bash
pip install pydantic pydantic-settings pytest pytest-asyncio anyio \
            SQLAlchemy fastapi httpx 'psycopg[binary]' neo4j PyYAML jinja2 \
            python-slugify python-multipart starlette
export DEIDENTIFY_LEVEL=regex_only
```

### C) Frontend in your terminal, backend in docker

Fastest Vue iteration. The Vite dev server already proxies `/api` to whichever backend you point it at.

```bash
docker compose up -d backend postgres neo4j proxy
cd frontend
npm install
npm run dev -- --host 0.0.0.0 --port 5173
```

Open <http://localhost:5173>. The axios client uses relative URLs so requests hit whatever origin served the page — which means you need to set `VITE_API_BASE=http://localhost:8000` if you open Vite directly instead of via the Caddy proxy.

---

## Debugging

### Backend (VS Code)

`.vscode/launch.json` (not committed) — drop this in:

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "uvicorn (backend)",
      "type": "debugpy",
      "request": "launch",
      "module": "uvicorn",
      "args": ["app.main:app", "--reload", "--port", "8000"],
      "cwd": "${workspaceFolder}/backend",
      "envFile": "${workspaceFolder}/.env",
      "console": "integratedTerminal",
      "justMyCode": false
    },
    {
      "name": "pytest (current file)",
      "type": "debugpy",
      "request": "launch",
      "module": "pytest",
      "args": ["${file}", "-v"],
      "cwd": "${workspaceFolder}/backend",
      "console": "integratedTerminal",
      "justMyCode": false
    }
  ]
}
```

`justMyCode: false` lets you step into FastAPI / SQLAlchemy. `envFile` reads the project `.env` (use the local repo path).

### Backend (PyCharm / IntelliJ)

Run configuration → Python:
- Module name: `uvicorn`
- Parameters: `app.main:app --reload --port 8000`
- Working directory: `backend/`
- Environment variables: load `.env`

### Hitting a breakpoint inside a queue worker

Workers run inside the same process as the API. Set a breakpoint anywhere in `app/services/ingest.py` and trigger an ingest via curl or the UI — execution will stop in your debugger.

### Inspecting a live request

```bash
# Tail the backend log; every request emits one line with X-Request-ID, status, latency.
docker compose logs -f backend | grep -v 'GET /health'

# Filter by a specific request ID (returned in response headers + JSON 500/422 bodies).
docker compose logs backend | grep '<request-id>'
```

### Inspecting an AI call

`ai_outputs` is the audit row. The `redaction_counts` column tells you what the de-identifier caught before the prompt went out.

```sql
SELECT call_type, model, valid, error, redaction_counts, latency_ms, cost_usd, created_at
  FROM ai_outputs
 ORDER BY created_at DESC
 LIMIT 5;
```

Open the latest call's full payload:

```sql
SELECT raw_output FROM ai_outputs ORDER BY created_at DESC LIMIT 1 \gx
```

Or via the UI: `/#/debug` → AI calls tab.

### Inspecting the graph

The Neo4j Browser is at <http://localhost:7474>. Bolt URI for `cypher-shell`: `bolt://localhost:7687`.

```cypher
MATCH (p:Patient {patientId: $hn})-[:HAS_ENCOUNTER]->(e)-[:MENTIONS]->(c)
RETURN p, e, c LIMIT 50
```

---

## Tests

Layered as in [docs/operations.md → Testing](operations.md#testing). Day-to-day:

```bash
cd backend
pytest                                       # full suite
pytest -k deidentify                         # match by name
pytest tests/test_api_ingest.py -v           # one file
pytest tests/test_api_ingest.py::test_xxx    # one test
pytest --lf                                  # re-run only what failed last time
pytest -x --tb=short                         # bail on first failure, short traces
pytest -q --no-header                        # CI-style minimal output
```

The conftest provides three core fixtures:

- `fake_store` — in-memory Postgres dispatcher. Captures every INSERT so tests assert on it directly (`fake_store.ai_outputs[-1]`, `fake_store.facts`, etc.).
- `stub_neo4j` — every Cypher call lands in a list; use `stub_neo4j.prime([row])` to seed the next read.
- `app_client` — a real FastAPI `TestClient` wired to the fakes. Use for endpoint tests.

Frontend:

```bash
cd frontend
npm run test            # Vitest watch mode
npm run test -- --run   # one-shot
npm run e2e             # Playwright; needs the stack up
```

---

## How to add …

### A new HTTP endpoint

1. Add the route to the matching `backend/app/routers/<resource>.py`. Routers don't write data — call a service.
2. Add the service function under `backend/app/services/`. Pure business logic; takes a DB session.
3. Add a test in `backend/tests/test_<resource>_routes.py` using the `app_client` fixture.
4. Update `docs/api.md` — the table is grouped by resource; pick the right section.

### A new ingest fact type (e.g. immunisations)

1. Extend `ClinicalExtractionResult` in `backend/app/schemas/extraction.py` with the new field — strict (`extra="forbid"`). Mirror the constants in `frontend/src/constants/clinical.js`.
2. Update `backend/app/prompts/templates.py` (the system prompt) so the AI knows about the new field.
3. Persist in `backend/app/services/ingest.py` → Stage 2 (`_persist_facts`).
4. Add the graph upsert path in `backend/app/services/graph_updater.py` (mirror the existing UNWIND blocks).
5. Add a markdown emitter in `backend/app/services/markdown_generator.py` if the fact deserves a vault page.
6. Test the extraction → persistence → graph → markdown chain end-to-end with the in-memory fakes; the mock provider should keyword-match the new fact type for deterministic tests.

### A new database migration

1. New SQL file in `backend/db/init/`, next sequential number (`00X_<purpose>.sql`).
2. Use `CREATE … IF NOT EXISTS` and `ADD COLUMN IF NOT EXISTS` — migrations may re-run on dev resets.
3. **For an existing database**, apply manually (the init scripts only run on first Postgres boot):

   ```bash
   docker compose exec -T postgres psql -U $POSTGRES_USER -d $POSTGRES_DB \
       < backend/db/init/00X_new_migration.sql
   ```

4. Update the `conftest.py` `FakeStore` if the new schema is read or written by code under test.

### A new AI call type

1. Add it to the `CallType` literal in `backend/app/services/ai_provider.py`.
2. Add the system + user prompt to `backend/app/prompts/templates.py`.
3. Implement the method on `AIProvider` (abstract), `MockProvider` (deterministic), `OpenAICompatibleProvider` (real).
4. **De-identify before the outbound HTTP** — copy the pattern from any existing call site (construct `Deidentifier`, run `pseudonymize_*` / `redact_text`, pass the redacted payload, persist `deidentified` + `redaction_counts`).
5. Add a test under `backend/tests/test_deidentify_e2e.py` asserting PHI doesn't leak.

### A new vault page type

Add a writer function in `backend/app/services/markdown_generator.py`. The `_entity_note` helper unifies problem / med / lab writers — extend it instead of duplicating.

### A new frontend page

1. New `.vue` file under `frontend/src/views/`.
2. Wire it into `frontend/src/router.js`.
3. Use components from `frontend/src/components/` — `SectionHeader`, `EmptyState`, `FactCard`, `Timeline`, `MarkdownViewer` already cover most layouts.
4. Wrap API calls via `frontend/src/api/client.js` so they pick up `X-API-Key` and the error snackbar.
5. Add a `tests/<Name>.spec.js` covering at least the empty + loaded states.

---

## Code style

- **No comments that restate the code.** Comments explain *why* — a constraint, an invariant, a non-obvious decision. Identifier names should carry the *what*.
- **Routers stay thin** — five lines max per endpoint; if you're branching, that's a service-layer responsibility.
- **Errors are typed.** Raise FastAPI `HTTPException(status_code=…, detail=…)` for HTTP-level errors; let services raise domain exceptions and let the router translate.
- **Strict Pydantic everywhere AI is involved.** `extra="forbid"`; unknown fields hard-fail. Validation failures must skip downstream writes (see `ingest.py`).
- **Never log a secret.** The config router masks `AI_API_KEY` and `BACKEND_API_KEY`; if you add a new secret to `Settings`, register it in the masker.

---

## Pre-commit hygiene

- Run the affected tests: `pytest tests/test_<resource>.py` and `pytest -k <changed-symbol>`.
- For frontend changes: `cd frontend && npm run test -- --run`.
- For SQL migrations: smoke against a fresh database (`docker compose down -v && docker compose up postgres` then re-create the schema).
- Read your diff in `git diff --stat` then `git diff`. Don't commit `print()` / `console.log()` left over from debugging.

---

## Contributing flow

1. Branch off `main` with a per-task name (`feat/<slug>`, `fix/<issue-number>`).
2. Open an issue first if the change is non-trivial — gives space to align on scope before code.
3. Commit message format follows the existing repo convention: `<type>(<scope>): <subject>`. Types: `feat / fix / docs / chore / test / refactor`. Scope is the affected area (`ingest`, `ai-provider`, `compliance`, etc.).
4. PR description includes a **checkbox test plan** for the reviewer — list what you verified manually + what tests cover the change.
5. CI must be green before merge. PRs targeting `main` run the full backend + frontend test suite; targeting other branches skips CI by design.
6. Squash-merge by default. Keep the PR description tidy — it becomes the canonical commit message.
