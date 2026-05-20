# Feature guide

> **Audience:** clinicians, PMs, and anyone running the app for the first time. For deployment / configuration see [docs/deployment.md](deployment.md) and [docs/ai-providers.md](ai-providers.md).

A walkthrough of every page and what to do on it. Glossary lives in [docs/glossary.md](glossary.md).

> ⚠️ **AI-assisted output requires clinical review.** Every suggestion is provisional until a clinician confirms or rejects it. The app shows a persistent warning chip in the top bar to keep that visible.

---

## Top navigation

Pages reachable from the app bar:

| Button | Where it goes | What it's for |
|---|---|---|
| **Patients** | `/patients` | Search and browse the patient list. |
| **Ingest** | `/ingest` | Submit a new EMR document for extraction. |
| **Config** | `/config` | Effective settings, AI provider quick-setup, model pricing, export profiles. |
| **Debug** | `/debug` | KPI charts, per-model spend, AI-call log, jobs viewer. |
| **Vector** | `/vector-demo` | Patient search by vector similarity and RAG Q&A. |
| **API** | external | Opens the OpenAPI / Swagger UI at `/docs`. |
| ☀️ / 🌙 | n/a | Toggle light / dark theme (persisted to localStorage). |
| Search box | n/a | Type a patient name or HN to jump straight to a patient. |

---

## Ingest (submit an EMR)

`/ingest` — a single form that accepts three input shapes:

- **Text** — paste raw clinical text. Most flexible.
- **JSON** — a `{patient, encounter, content}` envelope. Useful when an integration script already builds the metadata.
- **FHIR** — a FHIR `Bundle` resource with `Patient`, `Encounter`, and `DocumentReference` entries.

### Fields

- **Patient ID (HN)** and **Encounter type** are required for text / JSON modes; FHIR derives them from the bundle.
- **Async** is on by default — the request returns a `jobId` you can watch. Toggle off for "give me the result now" usage; latency depends on the AI model.

### What happens after submit

1. **Stage 1** — raw document persisted to Postgres + the patient row upserted.
2. **AI extract** — the configured chat model produces a strict JSON of structured facts. The full payload is logged in `ai_outputs`. PHI is redacted at the outbound boundary unless `DEIDENTIFY_LEVEL=off` (see [docs/compliance.md](compliance.md)).
3. **Stage 2** — facts inserted into Postgres in one batched round-trip.
4. **Stage 3** (parallel) — Neo4j graph upserts + Obsidian-style markdown vault writes + bounded-concurrency embedding ingest into pgvector.

Failures at any stage land in the job's `progress` JSONB and surface in the JobWatcher panel. A failed graph upsert is recoverable without re-paying for the AI call — see [Graph tab → Rebuild](#graph-tab) below.

---

## Patients

`/patients` — list / search. Two flavours:

- **Text search** — type a name or HN; uses Postgres `ILIKE`. Fast, exact substring.
- **Vector search** (powered by the global search box) — finds patients whose notes are semantically similar to a free-text query, even if no exact term matches.

Click a row to open the patient detail page.

---

## Patient detail

`/patients/{id}` — the workhorse page. Eight tabs cover progressively deeper views of the same patient.

### Header bar

- **Back arrow** returns to the patient list.
- **Patient ID + name + DOB + gender** subtitle.
- **Add note** opens the ingest form with the patient ID pre-filled.
- **Summary** generates an AI summary. Five summary types — see [Summary types](#summary-types) below.
- **Coding** suggests ICD-10 / SNOMED CT / LOINC / RxNorm candidates. Stored, see [Where coding suggestions live](#where-coding-suggestions-live).

### Overview tab

The fastest read of the patient's current state:

- **Active problems** — conditions, deduped by normalised code (or case-insensitive value if no code). Same condition mentioned multiple times in the EMR collapses to one row with evidence text accumulated.
- **Medications** — same dedup logic. `tamoxifen` and `Tamoxifen` collapse.
- **Recent observations** — labs and vitals. Dedup is stricter than conditions: identical reading (same name + value + unit + time) collapses, but the same lab measured at different times stays separate so trends are visible.
- **Plans** — care plan items from the most recent encounter(s).

Each fact row carries an `AI suggested` chip and a confirm / reject control. Confirmed facts feed downstream summary + coding calls more reliably.

### Timeline tab

Chronological list of encounters with their fact counts. Click an encounter to drill into:

- **Encounter detail** — facts scoped to that one encounter.
- **Encounter-level summary** — see [Summary types](#summary-types).
- **Encounter-level coding** — coding scoped to just this encounter (useful for episode-of-care coding).

### Encounters tab

Tabular view of encounters with sortable columns and "has summary / has coding" indicators — same data as the Timeline but optimised for finding old encounters quickly.

### Notes tab

The Obsidian-style markdown vault for this patient. Files live under `/data/vault/patients/{id}/`:

- `index.md` — patient overview, auto-regenerated on every ingest.
- `visits/{date}-{encounter_type}.md` — per-encounter note.
- `problems/{slug}.md`, `medications/{slug}.md`, `labs/{slug}.md` — per-entity notes accumulated longitudinally (every ingest appends, never deletes).
- `sources/{document_id}.md` — verbatim EMR + AI extraction snapshot.

Wikilinks (`[[problems/diabetes]]`) work — clicking one opens the linked file in-app, the same way Obsidian does. **The vault is fully Obsidian-compatible** — mount `/data/vault` into your Obsidian vault folder and you get the same UX with full Obsidian plugins.

### Graph tab

The Neo4j knowledge graph rendered with vis-network.

**Filters (top chips):**
- **All** — every encounter for this patient.
- **Latest encounter** — only the most recent encounter.
- **Pick…** — multi-select specific encounters.

**Filter drawer (⚙️):**
- Toggle **Encounters / Documents** as node types.
- **Dedupe across encounters** — when on, the same condition mentioned in 5 visits is one node; when off, you see one per encounter.
- **Review status** — show all / hide rejected / confirmed only.

**Fit-to-view** (📐) and **filter drawer** (⚙️) live in the top right.

**Recovery — "Rebuild graph from Postgres":**
If Neo4j was unhealthy during the original ingest, the patient ends up with facts in Postgres but an empty graph. The empty-state copy on the Graph tab will tell you so; click **Rebuild graph from Postgres** and the backend replays the upserts from the rows that are already there. No AI calls; idempotent (running it twice is a no-op).

### EMR vs facts tab

Three-column audit view:

1. **Documents** — list of source documents for the selected encounter.
2. **Raw EMR** — verbatim source text. Useful for spot-checking that the extractor read what it should have.
3. **Extracted facts** — the structured facts the AI produced from that document. Deduped within the document (the same finding mentioned three times in IMP / Intraop / Discharge collapses).

This is the page reviewers use to grade the AI's accuracy on a specific document.

### AI output tab

Raw AI call log for this patient: prompt template, model, tokens, latency, cost, raw response JSON. Useful for debugging extraction failures or prompt regression.

---

## Vector demo

`/vector-demo` — two side-by-side panels:

- **Patient search** — type a free-text query and see patients whose notes are semantically similar. Click a hit to open that patient.
- **RAG (ask questions)** — select a patient, ask a question, and the backend retrieves the top-K relevant chunks from that patient's notes, then has the LLM answer using only those chunks with `[1]`-style citations.

Both flows respect the de-identifier — the embedded text and the LLM payload are redacted before they leave the host.

---

## Config

`/config` — three cards:

- **AI provider** — read the effective `AI_PROVIDER`, `AI_BASE_URL`, model overrides; click **Quick setup** for OpenRouter / OpenAI / Groq presets. Changes land in the `app_config` table — **no restart required** for runtime-overridable fields.
- **Model pricing** — per-model `prompt_per_1m`, `completion_per_1m`, `embedding_per_1m` rates. Used to compute `cost_usd` on every AI call. **Refresh from OpenRouter** fetches current rates for every model present.
- **Export profiles** — define what `POST /api/export` includes when the user picks `custom`. Each profile is a named JSON config.

The Quick-setup dialog only fills the fields; you still review and click Save. Secrets are masked in `GET /api/config`.

---

## Debug

`/debug` — three tabs:

- **KPIs** — total spend / AI calls / avg latency / failure count over a date range.
- **By model** — table of calls / prompt+completion tokens / cost per model.
- **By day** — stacked bar of cost by model per day. Easy to spot a runaway day.
- **AI calls** — virtualised log of every individual AI call with filters (model, status, free-text). One row per `ai_outputs` entry. Click to view the full raw response. There's also a streamed CSV export.
- **Jobs** — every background job. Re-queue a failed job from here. Each job carries per-stage `progress` so you can see exactly where it died.

All Debug endpoints sit behind the same `X-API-Key` middleware as `/api/config` and `/api/emr`.

---

## Reference

### Summary types

The Summary button (or `POST /api/patient/{id}/summary` body's `summary_type`) accepts:

| Type | What it produces |
|---|---|
| `brief` | One-paragraph TL;DR. |
| `detailed` | Full clinical synthesis. |
| `discharge` | Discharge-summary shape (HPI, hospital course, meds at discharge, follow-up). |
| `problem_oriented` | Organised by active problem. |
| `timeline` | Chronological narrative. |
| `coding_support` | For the coder: list supporting evidence for each diagnosis. |

Encounter-level summaries (one encounter only) use the same types and live at `POST /api/patient/{id}/encounter/{eid}/summary`.

### Where coding suggestions live

- **Database** — every coding run is one row in the `patient_summaries` table (yes, the name is a misnomer; the table holds both summaries and coding) with `kind='coding'`. The row carries the full structured payload as JSONB, the model name, cost, and latency.
- **Markdown vault** — encounter-scoped coding runs also drop a `coding/{date}.md` file into the patient's vault folder for side-by-side review in Obsidian.
- **API** — `GET /api/patient/{id}/coding/latest` returns the most recent patient-level run; `GET /api/patient/{id}/encounter/{eid}/coding/latest` returns the encounter-scoped one. Both are zero-AI-cost reads — the suggestion is cached until the user clicks Regenerate.

To inspect via SQL:

```sql
SELECT created_at, model, payload->'primaryDiagnosis'->>'condition' AS primary
FROM patient_summaries
WHERE patient_id = 'YOUR_HN' AND kind = 'coding'
ORDER BY created_at DESC
LIMIT 5;
```

### Confirming / rejecting a fact

Every fact row carries three states under `review_status`: `ai_suggested` (default), `human_confirmed`, `rejected`.

- Click the check or X on a fact row in any tab to set the status.
- Confirmed facts feed downstream summary / coding calls more reliably (the AI sees a "confirmed by clinician" flag).
- Rejected facts are excluded from the graph by default (filter drawer → Review status to override).
- The audit log records every status change with actor + timestamp.

### Cost tracking

Every AI call writes one row to `ai_outputs` with tokens + cost. The cost is computed at write time from `model_pricing`. Models without a pricing row record NULL and the Debug page renders `?`.

The mock provider is seeded at `$0` so dev runs don't inflate spend totals.

### De-identification

On by default (`DEIDENTIFY_LEVEL=safe_harbor`). Every outbound payload has PHI stripped before it leaves the host. See [docs/compliance.md → De-identification](compliance.md#de-identification) for what gets caught and how to verify.

---

## Where to look next

- **API contract** — [docs/api.md](api.md).
- **Run the stack** — [docs/deployment.md](deployment.md).
- **Pick a model / preset** — [docs/ai-providers.md](ai-providers.md).
- **Something's off** — [docs/troubleshooting.md](troubleshooting.md).
- **Editing the code** — [docs/development.md](development.md).
