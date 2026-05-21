# Troubleshooting & FAQ

> **Audience:** anyone hitting the codebase for the first time and confused why something isn't behaving as documented.

The systematic failure-mode tables live in the page where the failure happens:

- [docs/deployment.md → Common dev pitfalls](deployment.md#common-dev-pitfalls)
- [docs/deployment.md → Failure modes + recovery](deployment.md#failure-modes--recovery)

This page collects the questions that don't fit either — usage gotchas, "is that supposed to do that?" moments, and behaviour that surprises newcomers.

---

## "Why is my AI provider key not being used?"

Effective config is the merge of three sources, in increasing precedence:

1. Defaults in `backend/app/config.py`.
2. Env vars from your shell + `.env` (loaded by docker compose).
3. Persisted overrides in the `app_config` Postgres table (written by `PATCH /api/config` or the UI).

So if you set `AI_PROVIDER=openai` in `.env` but the UI also has a persisted override pointing at `mock`, the override wins. Check `GET /api/config` (secrets masked) to see what's actually in effect, and `DELETE` or re-`PATCH` to fix.

## "Why did my ingest succeed but no facts appear?"

Most likely: the AI returned malformed JSON, so strict Pydantic validation rejected it. By design, validation failure logs the raw output to `ai_outputs.error` and writes an `EXTRACTION_INVALID` audit event, but **skips all downstream writes** (no facts, no graph, no markdown, no embeddings). Check:

```sql
SELECT call_type, model, error, validation_errors
  FROM ai_outputs
 WHERE error IS NOT NULL OR valid = false
 ORDER BY created_at DESC LIMIT 5;
```

If the error is `Expecting value: line 1 column 1`, the model didn't return JSON. Try a different model (Gemini 2.5 Flash and Claude 3.5 Sonnet are gold-standard for `response_format=json_object` adherence).

## "I switched DEIDENTIFY_LEVEL but nothing changed"

`DEIDENTIFY_LEVEL` lives in process env, not in `app_config`. To switch it you need to either:

- Edit `.env` and `docker compose restart backend` (env vars need a process restart).
- Set it directly on the running container with `docker compose exec backend env DEIDENTIFY_LEVEL=off …` — won't survive restart.

The runtime-config overrides path (`PATCH /api/config`) only covers fields stored in Postgres. The de-identifier setting is a deliberate exception — toggling redaction at runtime via an API endpoint would itself be a security risk.

## "Why does the redactor not catch [name]?"

Two recall ceilings:

1. **Regex-only mode** catches structured identifiers (HN, emails, phones, IDs, dates). It does **not** catch arbitrary person names — that needs NER.
2. **Safe-harbor mode** adds Presidio (English) and PyThaiNLP (Thai). Recall is good but not 100% — unusual spellings, OCR artifacts, and code-switched text leak. Check `ai_outputs.redaction_counts` to confirm the redactor is seeing the names you expect.

If a specific name pattern recurs in your data, add a custom recognizer in `backend/app/services/deidentify_recognizers.py` and write a test.

## "RAG returns 'No relevant excerpts retrieved'"

The vector index is empty for that patient. Two common causes:

- The ingest happened before embedding was wired up (or the embedding model was unavailable that day). Re-trigger: `POST /api/jobs/{id}/requeue` on the relevant ingest job, or re-ingest the document.
- The patient ID in the URL doesn't match the `patient_id` in the embeddings table. Check `SELECT DISTINCT patient_id FROM embeddings;` and compare.

## "Backend unhealthy — spaCy model download stuck at 587 MB"

In the backend log:

```
WARNING:presidio-analyzer:Model en_core_web_lg is not installed. Downloading...
Defaulting to user installation because normal site-packages is not writeable
Downloading … en_core_web_lg-3.7.1-py3-none-any.whl (587.7 MB)
…
Container cng-backend  Error  dependency failed to start: container cng-backend is unhealthy
```

Presidio's default NER model is `en_core_web_lg` (~587 MB) but the Dockerfile only installs `en_core_web_sm`. The non-root container user can't write to system `site-packages` and the 100 s healthcheck window expires long before the runtime download finishes anyway.

**Fix:** the latest backend code reads `DEIDENT_SPACY_MODEL` (defaults to `en_core_web_sm` — the model already in the image), so Presidio uses the bundled `_sm` model and never tries to download. Pull, rebuild, and bring the stack back up:

```bash
git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build backend
```

If you specifically want the larger-recall `en_core_web_lg`, add it to `backend/Dockerfile`:

```dockerfile
RUN python -m spacy download en_core_web_lg
```

…then set `DEIDENT_SPACY_MODEL=en_core_web_lg` in `.env`.

## "Prod proxy returns 502 — dial tcp: lookup frontend on 127.0.0.11:53"

Exact log shape:

```
ERROR  http.log.error.log0  dial tcp: lookup frontend on 127.0.0.11:53: server misbehaving
                            …status=502, err_trace=reverseproxy.statusError…
```

The prod proxy is still loading the dev `Caddyfile.dev` (which reverse-proxies to `frontend:5173`) instead of `Caddyfile.prod` (which serves static files). The dev container is disabled in prod via `profiles: ["never"]`, so the `frontend` hostname doesn't resolve → 502.

**Root cause**: pre-fix, `docker-compose.prod.yml`'s proxy block didn't reset the `volumes:` list. Compose merges lists by default, so the dev compose's bind mount

```yaml
- ./caddy/Caddyfile.dev:/etc/caddy/Caddyfile:ro
```

survived the merge and shadowed the `Caddyfile.prod` baked into the image.

**Fix**: pull the latest, rebuild, and bring the proxy back up:

```bash
git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build proxy
```

To confirm after the fix: `docker compose exec proxy cat /etc/caddy/Caddyfile | head -5` should show `# Caddy: production reverse proxy and static-file server.` from `Caddyfile.prod`, not `# Caddy: dev reverse proxy.` from `Caddyfile.dev`.

## "Invalid or missing X-API-Key" after first prod deploy

Symptom: a snackbar fires saying *"Invalid or missing X-API-Key"* and every protected request (`/api/emr`, `/api/config`, `/api/export`, `/api/facts`, `/api/debug`) returns 401.

The backend has `BACKEND_API_KEY` set in `.env` (correct, required in prod). The browser doesn't know the matching value yet — it lives only in the user's `localStorage` and a fresh browser session has none.

**Fix**:

1. Open the app and go to **Config** (top-nav, no API key required to load the page itself).
2. Scroll to the **Browser API key** card.
3. Paste your `BACKEND_API_KEY` value from `.env` into the *X-API-Key (browser-side)* field.
4. Click **Save in this browser**.
5. Refresh — protected endpoints now succeed.

The latest frontend code:
- The snackbar text now spells out *where* to set the key (Config → Browser API key) instead of just echoing the backend's terse error.
- The Config page renders even when `GET /api/config` 401s, so the Browser API key card is always reachable.
- The "missing X-API-Key" toast fires once per session (not once per failed request) so the first paint doesn't spam.

If pasting the key still 401s, double-check it matches `BACKEND_API_KEY` in `.env` byte-for-byte (no trailing whitespace, no quotes).

## "Blocked request. This host (...) is not allowed."

You're hitting the Vite dev server (`docker compose up`) via a hostname other than `localhost`. Vite 5+ rejects non-allowlisted host headers by default.

Two fixes:

- **Per-host allow-list** — set `VITE_ALLOWED_HOSTS=cng.example.com,other.host` in `.env` and `docker compose restart frontend`. Comma-separated; supports leading-dot wildcards like `.example.com`.
- **Allow all** (default in this repo) — leave `VITE_ALLOWED_HOSTS` unset and Vite falls back to `allowedHosts: 'all'`. Safe because Caddy is the public entry point.

This issue **doesn't apply to production** (`docker-compose.prod.yml`). The prod overlay disables the Vite container and Caddy serves pre-built static files directly — `npm run build` runs at image-build time, the dev server never starts.

## "Vite is silent — how do I see what it's doing?"

Two knobs in `.env`:

- `VITE_LOG_LEVEL=info` (default; bump to `warn` / `error` / `silent` to quiet things down).
- `VITE_DEBUG=1` — turns on Node's `debug()` output for every Vite namespace (`DEBUG=vite:*`). Verbose but invaluable when chasing host-header rejects, missing imports, plugin order issues, or HMR drops.

Apply with `docker compose restart frontend` and tail `docker compose logs -f frontend`.

## "The frontend can't reach the backend"

Three layers of failure:

- **Wrong base URL.** In dev, axios uses relative URLs (`/api/...`), so requests go to whichever origin served the page. If you open the UI via the Vite dev server (`:5173`) instead of the Caddy proxy (`:80`), the relative URL hits Vite, which doesn't proxy `/api`. Open via the Caddy URL, or set `VITE_API_BASE=http://localhost:8000`.
- **CORS.** Default `.env` has `CORS_ORIGINS=*` (dev only). If you set a real allow-list, the origin must match exactly — `http://localhost` ≠ `http://localhost:5173`.
- **API key.** `/api/emr`, `/api/config`, `/api/export`, `/api/facts`, `/api/debug` require `X-API-Key: $BACKEND_API_KEY` when `BACKEND_API_KEY` is set. The axios client sets it automatically from `localStorage`; check the Config page or set it via DevTools.

## "Image build is huge / slow"

Presidio + spaCy + PyThaiNLP adds ~500 MB. First build is slow; subsequent builds reuse the Docker layer cache because `requirements.txt` is `COPY`d before the rest of the source.

If you don't need the safe-harbor de-identifier (e.g. you're behind a BAA), set `DEIDENTIFY_LEVEL=off`. The libs are still in the image — to shrink the image too, remove the four de-identifier lines from `backend/requirements.txt` and the `python -m spacy download …` step from `backend/Dockerfile`, then rebuild.

## "Tests pass locally but CI is red"

CI uses Python 3.12; the in-tree venv defaults to whatever `python` resolves to. If you ran tests under 3.11 locally, an asyncio / typing nuance might have hidden. Pin your local venv: `python3.12 -m venv backend/.venv`.

## "How do I see what actually left the host?"

`ai_outputs.raw_response` is the verbatim JSON returned by the AI provider. To see the prompt that was sent, add a temporary log line in `app/services/ai_provider.py` — we deliberately don't persist prompts (storage doubles, and prompts are reconstructible from `patient_facts` + the redacted patient row). The de-identification audit row tells you which categories got caught (`ai_outputs.redaction_counts`) without you having to inspect the prompt.

## "Where's the source of truth for [thing]?"

| Thing | Source of truth |
|---|---|
| Schema migrations | `backend/db/init/*.sql` (numeric order). No formal tracker. |
| API contract | `backend/app/routers/*.py` decorators. Auto-generated OpenAPI at `/docs`. |
| AI output shape | `backend/app/schemas/extraction.py` (`ClinicalExtractionResult`). |
| Effective config | `GET /api/config` (with secrets masked). |
| Cost per model | `model_pricing` table; UI at `/#/config` → Model pricing card. |
| What got redacted | `ai_outputs.redaction_counts` for the call in question. |
| Whether a job ran | `jobs` table; UI at `/#/debug` → Jobs tab. |
