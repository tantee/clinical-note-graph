# Encounter-scoped AI summary + coding

**Status:** Design approved — awaiting final user review before plan write-up.
**Owner:** —
**Created:** 2026-05-19
**Related issues:** (to be created in GitHub before implementation)

---

## 1. Problem

Today's AI summary and coding endpoints aggregate **all** of a patient's facts
into a single longitudinal output. In real clinical workflow, the most common
artifact is a **discharge summary for this admission** and **a code-set for
this admission** — both scoped to one encounter, not the whole patient. The
patient-level view is still useful (longitudinal overview, multi-encounter
research) but it cannot be the only view.

This design adds encounter-scoped variants of both features while keeping the
existing patient-level surface intact.

## 2. Non-goals

- Refactoring the existing patient-level summary/coding flow.
- Introducing an explicit `facts.status` (active/resolved) column. The
  background-context approximation uses "latest mention wins, excluding rows
  with `review_status = 'rejected'`". A real status column can be added later
  if this proves too noisy.
- Hand-tuning a separate prompt per encounter type beyond `discharge_summary`.
  `detailed` and `brief` cover everything that isn't an admission.
- Locking concurrent regenerate clicks at the backend. Client-side `busy`
  flag is already shipped; last-write-wins on the vault file is acceptable
  for this prototype.

## 3. Scope semantics — what the AI sees

When summarizing or coding a specific encounter, the AI receives **two
sections** of facts:

- `thisEncounter` — facts where `encounter_id = :eid`, organized by type
  (problems, medications, observations, procedures, plans, allergies,
  diagnoses, codingCandidates).
- `background` — the patient's chronic problems, home medications, and
  known allergies derived from facts **outside** this encounter. Definition:
  rows where `patient_id = :pid AND encounter_id <> :eid AND
  review_status <> 'rejected' AND type IN ('condition','medication','allergy')`,
  collapsed to one row per `normalized_code` (or value when code is null)
  keeping the most recent.

The prompt is told to dedupe when a fact appears in both sections — treat as
ongoing.

## 4. Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Frontend                                                                  │
│  • PatientsView  → expandable rows show encounters table inside row       │
│  • PatientDetail → new "Encounters" tab (table); existing Timeline kept   │
│  • EncounterDetail (NEW route /patient/:pid/encounter/:eid)              │
│       ├ header  (type · date · dept · provider)                           │
│       ├ documents list (filtered to this encounter)                      │
│       ├ encounter summary card (regenerate ⇆ load latest)                 │
│       ├ encounter coding card  (regenerate ⇆ load latest)                 │
│       └ background panel (chronic problems / home meds / allergies)       │
├──────────────────────────────────────────────────────────────────────────┤
│ Backend                                                                   │
│  • gather_encounter_facts(eid)  → {encounter, thisEncounter, background,  │
│                                    documents}                             │
│  • Provider prompt gets a new `discharge_summary` type                    │
│  • Five new routes (see §6)                                               │
│  • Patient-level endpoints unchanged                                      │
├──────────────────────────────────────────────────────────────────────────┤
│ Persistence                                                               │
│  • patient_summaries gains a nullable encounter_id column                 │
│       NULL  = patient-level (existing rows)                               │
│       non-null = encounter-scoped                                         │
│  • Vault: patients/<HN>/encounters/<eid>/summary-<type>.md , coding.md    │
└──────────────────────────────────────────────────────────────────────────┘
```

## 5. Data model

### 5.1 Schema migration

Single SQL file `backend/db/init/004_encounter_scope.sql`:

```sql
ALTER TABLE patient_summaries
  ADD COLUMN encounter_id TEXT
  REFERENCES encounters(encounter_id) ON DELETE CASCADE;

CREATE INDEX patient_summaries_encounter_idx
  ON patient_summaries (encounter_id, kind, created_at DESC)
  WHERE encounter_id IS NOT NULL;
```

Applied to the live database via `docker exec -i cng-postgres psql … <
004_encounter_scope.sql` during deployment of the feature branch. Idempotent
on fresh clones since init scripts run in numbered order.

### 5.2 New aggregator

`backend/app/services/patient_facts.py`:

```python
def gather_encounter_facts(encounter_id: str) -> dict[str, Any]:
    """
    Returns:
      encounter:     {encounterId, type, dateTime, department, provider}
      thisEncounter: {problems, medications, observations, procedures,
                      plans, allergies, diagnoses, codingCandidates}
      background:    {chronicProblems, homeMedications, knownAllergies}
      documents:     [{documentId, format, version, ...}]
    """
```

Raises `LookupError` if `encounter_id` does not exist, which the route layer
converts to 404.

### 5.3 Vault layout addition

```
patients/<HN>/
├── visits/                       ← unchanged (ingest-time encounter notes)
├── summaries/                    ← unchanged (patient-level summaries)
├── encounters/                   ← NEW
│   └── <encounter_id>/
│       ├── summary-discharge.md  (or summary-detailed.md, etc.)
│       └── coding.md
```

Regenerating an encounter summary **overwrites** the vault file. Audit
history lives in the DB. Rationale: clinicians browsing the vault want the
current best version; fan-of-revisions is noise.

## 6. API surface

New routes, all under `/api`:

| Method | Path | Purpose |
|---|---|---|
| POST | `/patient/{pid}/encounter/{eid}/summary` | Generate + persist encounter summary |
| GET  | `/patient/{pid}/encounter/{eid}/summary/latest` | Most recent persisted row \| null |
| POST | `/patient/{pid}/encounter/{eid}/coding/suggest` | Generate + persist encounter coding |
| GET  | `/patient/{pid}/encounter/{eid}/coding/latest` | Most recent persisted row \| null |
| GET  | `/patient/{pid}/encounters` | Encounter list w/ docCount, hasSummary, hasCoding flags |

The `summary` request body reuses `SummaryRequest` (`type`,
`includeEvidence`, `dateRange`). `type` accepts `'discharge_summary' \|
'detailed' \| 'brief'`. If omitted, the route resolves the default from the
encounter row:

```
encounters.type in ('admission','discharge_summary','admission_note')
    → summary_type = 'discharge_summary'
otherwise
    → summary_type = 'detailed'
```

A FastAPI dependency `verify_encounter(pid, eid)` returns 404 (single shape:
`{"detail": "Encounter not found for patient"}`) for any mismatch — used by
all five routes.

Patient-level endpoints (`POST /api/patient/{pid}/summary` and friends) are
**not modified**.

## 7. Prompt design

A new conditional branch in `backend/app/services/ai_provider.py`'s
`SUMMARY_SYSTEM` for `summary_type='discharge_summary'`:

```
You are a clinical scribe writing a discharge summary for the encounter
provided. Use ONLY the facts in the JSON payload. Cite source documents
inline when summarizing specific findings.

Output strict markdown with these sections IN THIS ORDER, omitting any
that have no content. Do not invent additional sections.

## Reason for admission
## Past medical history
## Home medications on admission
## Hospital course
## Discharge medications
## Follow-up plan
## Safety notes

If a fact appears in both `thisEncounter` and `background`, treat as
ongoing — do not list it twice.

End with the standard AI-assisted disclaimer.
```

**Language note:** The Thai-language hint considered during design (because
sample data contains Thai) was not added to the prompt for this iteration —
the existing `SUMMARY_SYSTEM` does not enforce a language and the LLM tends
to follow the dominant language of the input. If the discharge summary
drifts to English on Thai notes in practice, add the hint as a follow-up.

`detailed` and `brief` retain their existing free-form prompts — they
remain the right tool for outpatient and progress-note style summaries.

The coding prompt is **unchanged structurally** — the existing prompt
already returns `primaryDiagnosis`, `secondaryDiagnoses`, and
`codingCandidates`. What changes is the facts payload (encounter-scoped via
`gather_encounter_facts`).

## 8. UI

### 8.1 PatientsView — expandable rows

Migrate the current `v-card`-based list to `v-data-table` with `show-expand`.
Each expanded row lazy-loads `GET /api/patient/{pid}/encounters` and renders
a nested table (date · type · department · doc count · summary chip · coding
chip · **View** action). Primary row keeps **View patient**; encounter rows
have **View encounter** → router push to `/patient/:pid/encounter/:eid`.

### 8.2 PatientDetail — new Encounters tab

Inserted between **Timeline** and **Notes**. Fetches `GET /api/patient/{pid}/encounters`
(the new endpoint from §6) — the table needs `docCount`, `hasSummary`, and
`hasCoding` flags that `getTimeline` does not provide. Columns: date · type
· department · provider · docs · summary status · coding status · actions.
Two row actions: **View encounter** and **Summarize / Code** (deep-link with
`?action=summary`).

Timeline tab stays visual/chronological; existing encounter cards get a
click handler that pushes to the same `/encounter/:eid` route.

### 8.3 EncounterDetail.vue (new)

Route `/patient/:pid/encounter/:eid`. Layout: 8/4 split on `md+`.
Left column: encounter summary card, coding card, documents list.
Right column: background panel (chronic problems / home meds / known
allergies, collapsible `v-list`), this-encounter facts (mini fact cards).

The Summary button is a `v-menu` split-button — primary action regenerates
with the resolved default type; dropdown lets the user override
(`discharge_summary` / `detailed` / `brief`). Button labels flip to
"Regenerate summary" / "Regenerate coding" when latest data is present
(same pattern as the patient page).

`?action=summary` query param auto-triggers regenerate-on-load — supports
the one-click flow from the Encounters tab.

### 8.4 Shared components

Extract `SummaryCard.vue` and `CodingCard.vue` from `PatientDetail.vue` —
~80 LOC of refactor — so both views render identically. No other new shared
components.

### 8.5 API client helpers

Added to `frontend/src/api/client.js`:

```javascript
listEncounters(pid)
summarizeEncounter(pid, eid, body)
getLatestEncounterSummary(pid, eid)
suggestEncounterCoding(pid, eid, body)
getLatestEncounterCoding(pid, eid)
```

## 9. Error handling

| Case | Behavior |
|---|---|
| `eid` not found / not belonging to `pid` | 404 from `verify_encounter` dependency |
| Empty `thisEncounter` (no facts) | Summary still runs; AI sees only `background` and reports "No documented findings for this encounter" |
| Zero documents for the encounter | Allowed; some encounters are pure follow-up plans |
| Two rapid Regenerate clicks | Client-side `busy` flag blocks second click; no backend lock |
| Vault write failure (perms/disk) | Existing `save_summary` stores `vault_path = NULL`; DB row authoritative; UI hides the vault-path chip |
| Patient deleted mid-session | `CASCADE` on the new FK cleans encounter rows; UI 404 → redirect to `/patients` with snackbar |

## 10. Testing

### Backend (`backend/tests/`) — pytest

1. **`test_gather_encounter_facts.py`** (unit, TDD) — fixture: one patient,
   two encounters, facts on each. Assertions: `thisEncounter` contains only
   the eid's facts; `background` excludes them; dedupe when the same
   condition appears in multiple prior encounters keeps the latest mention.
2. **`test_encounter_routes.py`** (integration via FastAPI test client) —
   all 5 routes. 200 with `null` body when no latest exists; 200 with
   persisted body after a POST; 404 when `eid` doesn't belong to `pid`;
   default `summary_type` resolution by `encounters.type`.
3. **`test_discharge_prompt.py`** (unit) — assert the section list appears
   in `SUMMARY_SYSTEM` for `summary_type='discharge_summary'`. Lightweight
   grep-style check; keeps the prompt contract enforceable.
4. **`test_patient_summary_existing.py`** (regression) — confirms the
   patient-level endpoints still return correctly after the schema/
   aggregator additions. Gates the merge.

### Frontend (`frontend/src/views/__tests__/`) — Vitest + vue-test-utils

5. **`EncounterDetail.spec.js`** — mount with stubbed API; header renders,
   button labels flip to "Regenerate…" when latest is present, 404 → error
   state.
6. **`PatientsView.spec.js`** — expand-row test: stub `listEncounters` for
   one patient, click expand, assert encounter rows render with expected
   actions.

### E2E (`frontend/e2e/`) — Playwright

7. **`encounter-summary.spec.ts`** — boot against `AI_PROVIDER=mock`. Pick
   HN-DEMO-1 → click an admission → click Summarize → assert the summary
   card appears with section headers from the discharge prompt. Smoke test
   for the whole feature.

### Budget

Unit + integration < 30 s; +20 s for the E2E. Acceptable.

## 11. Open questions

None at design approval time. If the "latest mention wins" rule for the
background section proves too noisy in practice, a follow-up spec can
introduce a `facts.status` column. Not blocking this feature.

## 12. Out-of-scope follow-ups

- Encounter-level **graph view** (sub-graph filtered to one encounter).
  Tracked separately under the second feature in the broader brief.
- **Vector DB demo page** (RAG + patient search). Tracked as the third
  feature in the broader brief.
- Encounter-level **export profile** (FHIR/CDA per encounter). Not
  scoped here.
- **Resolved-condition tracking** (`facts.status`). Possible follow-up if
  background noise becomes a real problem.
