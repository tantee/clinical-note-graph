# Temporal Problems & Medications with Human Curation

**Status:** Approved design — ready for implementation planning
**Date:** 2026-06-04
**Branch:** `feat/temporal-curated-facts`

## Problem & Motivation

Problems and medications need first-class time information. Clinically, *when* something
started and stopped is essential: chemotherapy cycle dates, antibiotic start/stop dates,
the onset of a chronic condition. Today:

- **Problems** (`Condition` facts) already carry `onsetDate` / `resolvedDate` plus
  `severity` / `status`, AI-extracted and optional.
- **Medications** carry **no** dates at all — only `action` (start/continue/stop/modify/hold),
  dose, route, frequency, indication.
- **Manual correction is effectively absent.** The only human action is
  `PATCH /facts/{id}/review` (flip review status). There is no way to edit a value or
  date, insert a fact by hand, or delete one the AI got wrong.

This design delivers two linked capabilities:

1. Give medications a real temporal envelope and treat problem onset/resolved as
   first-class, with explicit support for **open and uncertain bounds**.
2. Let a clinician **insert, correct, and delete** these records when the AI is wrong,
   with the corrections sticking across re-ingestion.

## Key Decisions (from brainstorming)

1. **Edit model:** a *curated longitudinal layer*. Human edits live in a per-patient
   curated record that always wins over AI. The append-only Postgres `facts` table stays
   the immutable AI evidence trail. ("Reset to AI" is therefore trivial.)
2. **Time model:** a single interval (`start` / `stop`) **plus a free-text schedule**
   field (e.g. `q3wk × 6 cycles`, `ATB ×7d`). No structured sub-intervals (YAGNI; can
   extend later).
3. **UI surface:** dedicated **Problems** and **Medications** panels on the patient page.
4. **AI seeding:** the extractor *is* upgraded to pull medication dates and schedule; AI
   values seed the curated record and the human corrects them.
5. **Delete behavior:** *soft-delete, but re-mention resurfaces.* A deleted item is
   hidden; a later note re-mentioning it brings it back as an `ai_suggested` item for
   re-review, **preserving the human's prior date edits**.

Additional requirement surfaced during Q4: the time model must express **open/uncertain
bounds** — a condition that predates the encounter with unknown or only *estimated*
onset ("≈4 months ago"), and conditions/medications that are still ongoing.

## Architecture

### Storage layers

| Layer | Role | Mutated by humans? |
|---|---|---|
| Postgres `facts` | Immutable, append-only AI evidence; one row per document mention. Audit trail. | **No** |
| Postgres `curated_facts` (new) | Reconciled human-truth view; one row per distinct clinical item per patient; holds the temporal envelope + provenance. | **Yes** |
| Neo4j graph | Longitudinal render. `Condition` / `Medication` node values are overridden by the curated row when one exists. | Indirectly (via curated writes) |

### Identity ("the same item")

Used both to merge AI mentions into a single curated row and to detect re-mentions:

```
normalized_key = normalized_code  if present
                 else lower(value)        # condition value / medication name
identity        = (patient_id, type, normalized_key)
```

This matches the existing Neo4j `MERGE` keys (`Condition {patientId, value}`,
`Medication {patientId, name}`).

## Data Model

New table `curated_facts` (migration `db/init/006_curated_facts.sql`, idempotent
`CREATE TABLE IF NOT EXISTS …` to match the existing numbered-migration pattern):

| column | type | meaning |
|---|---|---|
| `id` | uuid PK | |
| `patient_id` | text FK → patients | |
| `type` | text | `condition` \| `medication` |
| `normalized_key` | text | identity key (see above) |
| `display_value` | text | human label |
| `normalized_code` | text null | code |
| `coding_system` | text null | ICD10 / RxNorm / … |
| `start_date` | date null | may be an estimate |
| `start_qualifier` | text | `exact` \| `estimated` \| `before` \| `unknown` |
| `stop_date` | date null | |
| `stop_qualifier` | text | `exact` \| `estimated` \| `ongoing` \| `unknown` |
| `start_text` | text null | original phrase ("4 months ago") |
| `stop_text` | text null | original phrase |
| `schedule_text` | text null | free text ("q3wk × 6 cycles") |
| `status` | text null | clinical status (problems) / action (meds) |
| `record_state` | text | `active` \| `dismissed` (soft-delete) |
| `review_status` | text | `ai_suggested` \| `human_confirmed` |
| `origin` | text | `ai` \| `human` |
| `human_edited_fields` | jsonb | set of field names the human has overridden |
| `last_evidence_fact_id` | uuid null | most recent `facts.id` that fed this row |
| `updated_by` | text null | provenance |
| `created_at` / `updated_at` | timestamptz | |

Unique constraint on `(patient_id, type, normalized_key)`.

### Expressing open / uncertain bounds

- **Open start** (predates encounter, no clear onset): `start_qualifier ∈ {before, unknown}`;
  `start_date` may be null or an estimate.
- **Estimated start** ("≈4 months ago"): `start_qualifier = estimated`, `start_date` =
  the estimated date anchored on the encounter date, `start_text` = the original phrase.
- **Ongoing**: `stop_qualifier = ongoing`, `stop_date` null.
- **Exact**: `*_qualifier = exact` with a concrete date.

## AI Seeding (extractor changes)

### Schema additions (`app/schemas/extraction.py`)

- `MedicationChange` gains (all optional/nullable): `startDate`, `startQualifier`,
  `stopDate`, `stopQualifier`, `startText`, `stopText`, `schedule`.
- `PatientFact` (problems) gains `onsetQualifier`, `resolvedQualifier`, `onsetText`,
  `resolvedText` for parity with the existing `onsetDate` / `resolvedDate`.
- New `Literal` types for the qualifiers, defaulting to `None`/`unknown` so smaller models
  can omit them without failing validation.

### Prompt update (`app/prompts/templates.py`)

Instruct the model to:
- Fill dates when the note states them.
- Convert relative expressions ("4 months ago", "since last winter") into an **estimated**
  date anchored on the **encounter date**, set qualifier = `estimated`, and keep the
  original phrase in the `*Text` field.
- Use `before` / `unknown` when a condition predates the note with no clear onset.
- Use `ongoing` when not stopped.

### Reconcile step (`app/services/` — new `reconcile.py` or extend `graph_updater`)

Runs after each ingest's facts are persisted. For each AI problem/medication, upsert into
`curated_facts` by identity:

- **New identity** → insert an `origin=ai`, `review_status=ai_suggested`, `record_state=active` row.
- **Existing active row** → fill *empty* fields from AI and refresh AI-origin fields, but
  **never overwrite a field listed in `human_edited_fields`**.
- **Existing dismissed row (re-mention)** → flip `record_state='active'`,
  `review_status='ai_suggested'`, attach new evidence (`last_evidence_fact_id`), and
  **preserve human date edits** (fields in `human_edited_fields` keep their values).
- After upsert, push the resulting curated values into the corresponding Neo4j node.

## API

New endpoints, following the existing `/facts/{id}/review` style in `app/routers/patient.py`:

| method | path | purpose |
|---|---|---|
| `GET` | `/patient/{id}/curated?type=condition\|medication` | active curated list |
| `POST` | `/patient/{id}/curated` | manual insert (`origin=human`, `review_status=human_confirmed`) |
| `PATCH` | `/curated/{cid}` | edit dates/qualifiers/schedule/status/value; adds touched fields to `human_edited_fields` and sets `review_status=human_confirmed` |
| `DELETE` | `/curated/{cid}` | soft-delete → `record_state='dismissed'` |
| `POST` | `/curated/{cid}/restore` | undo soft-delete |

Each write also propagates the resulting values into the Neo4j node so the graph stays
consistent. Pydantic request/response schemas live alongside the others in `app/schemas/`.

## Frontend (patient-page panels)

Two panels on the patient page — **Problems** and **Medications** — reusing the existing
`FactCard` / `FactSection` patterns and `REVIEW_META` chips:

- Each row shows the value, a compact **date-range** rendering that handles open bounds
  (`~4 mo ago → ongoing`, `2024-01 → 2024-02-07`, `before → 2024-03`), schedule text, and
  a review chip.
- **Inline edit:** date pickers + qualifier `<select>` for each endpoint + schedule field +
  status.
- **+ Add** to insert a record manually.
- **Delete / Restore** for soft-delete.

A small date-range formatter component/util encapsulates the qualifier → label logic so the
rendering is testable in isolation.

## Error Handling

- Reconcile is best-effort and isolated per item: a failure to upsert one curated row (or
  one Neo4j propagation) is logged and does not abort ingest or other items, consistent
  with the existing graph-write resilience.
- API writes validate qualifiers against the allowed `Literal`s; an impossible combination
  (e.g. `stop_qualifier=ongoing` with a non-null `stop_date`) is normalized (clear the
  date) rather than rejected.
- `PATCH` / `DELETE` on a missing `cid` → 404, matching existing router conventions.

## Testing

**Backend**
- Reconcile merge: AI fills empty fields; never clobbers fields in `human_edited_fields`.
- Qualifier / open-bound round-trips through schema → curated row → API response.
- Relative-phrase → estimated-date anchoring (prompt behavior covered by a provider-level
  or fixture test where feasible; reconcile/storage covered directly).
- Soft-delete hides the row; re-mention resurfaces as `ai_suggested` while preserving prior
  human date edits.
- Manual insert / edit / delete / restore endpoints (happy path + 404).
- Migration `006` idempotency (run twice, no error, no duplicate columns).

**Frontend**
- Date-range formatter for every open/estimated/exact combination.
- Edit / add / delete / restore flows against a mocked API.

## Out of Scope (YAGNI)

- Structured multiple-interval / per-cycle sub-records (captured via `schedule_text`).
- Per-field "reset this field to AI value" UI (the curated layer makes it possible later;
  not built now).
- Timeline-tab bar rendering of the curated dates (the chosen surface is the patient-page
  panel; timeline integration can consume the same `GET /curated` endpoint later).
