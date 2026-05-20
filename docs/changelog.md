# Changelog (highlights)

A running list of structural changes since the MVP first draft. For the full
record, see `git log`.

## v1B — HIPAA Safe Harbor de-identification (PR #11, May 2026)

- New `Deidentifier` service applies HIPAA Safe Harbor redaction at the
  outbound boundary in `ai_provider` for all five AI call types
  (`extract`, `summary`, `coding`, `embed`, `rag`).
- Three levels: `off | regex_only | safe_harbor` (default `safe_harbor`).
- Regex sweep + Microsoft Presidio (English NER) + PyThaiNLP (Thai NER) +
  per-request pseudonym map for names / HN / providers; dates rounded to
  year; DOB → year + age class.
- New `ai_outputs.deidentified` / `redaction_counts` audit columns
  (migration `005_deidentified_flag.sql`).
- 27 new tests; full backend suite green.

## v1A — Compliance & data-flow audit (PR #9, May 2026)

- Added the Compliance & data handling section to the README, auditing
  what PHI leaves the host on each AI call type.
- HIPAA / GDPR / PDPA regulatory framing.
- Mitigations ladder (mock → self-host → BAA → de-identify → consent →
  encryption → audit).

## Initial review-and-polish pass

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
