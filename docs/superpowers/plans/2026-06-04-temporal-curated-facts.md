# Temporal Curated Facts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give problems and medications a first-class temporal envelope (start/stop dates + qualifiers + free-text schedule) and let a clinician insert, correct, and delete those records through a curated longitudinal layer that always wins over AI and survives re-ingestion.

**Architecture:** A new append-never-for-humans `curated_facts` Postgres table holds one reconciled row per distinct clinical item per patient. The immutable `facts` table stays the AI evidence trail. After each ingest a `reconcile` step upserts AI mentions into `curated_facts` by identity, never clobbering human-edited fields, and resurfaces soft-deleted items on re-mention. New API endpoints let humans GET/insert/edit/soft-delete/restore curated rows; every write also propagates into the Neo4j Condition/Medication node. The Vue frontend renders dedicated Problems and Medications panels with inline date/qualifier/schedule editing.

**Tech Stack:** Python 3 / FastAPI / SQLAlchemy Core (raw SQL via `text()`) / Postgres / Neo4j (Cypher) on the backend; Vue 3 + Vuetify 4 + Vite + Vitest on the frontend. All paths below are relative to the repo root `/Users/tantee/IdeaProjects/clinical-note-graph`.

---

## File Structure

**Backend (`backend/`)**

- Create `db/init/006_curated_facts.sql` — the new table + indexes (idempotent).
- Modify `app/schemas/extraction.py` — qualifier `Literal`s; new fields on `MedicationChange` and `PatientFact`.
- Modify `app/prompts/templates.py` — instruct the model to fill dates/qualifiers/schedule.
- Create `app/services/curated.py` — pure merge logic (`normalized_key`, `normalize_bounds`, `merge_curated`, `ai_item_from_*`) + DB layer (`reconcile_curated`, `list_curated`, `insert_curated`, `update_curated`, `dismiss_curated`, `restore_curated`) + `propagate_curated_to_graph`.
- Modify `app/services/ingest.py` — call `reconcile_curated` after facts are persisted.
- Create `app/schemas/curated.py` — Pydantic request/response models.
- Modify `app/routers/patient.py` — the five new endpoints.
- Modify `backend/tests/conftest.py` — extend `FakeStore` with a `curated_facts` table + statement matchers.
- Create tests: `tests/test_curated_schema.py`, `tests/test_curated_merge.py`, `tests/test_reconcile.py`, `tests/test_curated_api.py`, `tests/test_migration_006.py`.

**Frontend (`frontend/`)**

- Create `src/utils/dateRange.js` — qualifier→label range formatter.
- Modify `src/api/client.js` — five curated API calls.
- Create `src/components/CuratedPanel.vue` — Problems/Medications panel with inline edit/add/delete/restore.
- Modify `src/views/PatientDetail.vue` — mount two `CuratedPanel`s on the Overview tab.
- Create tests: `tests/utils.dateRange.spec.js`, `tests/CuratedPanel.spec.js`.

The backend phase (Tasks 1–9) produces working, tested software on its own; the frontend phase (Tasks 10–13) consumes the resulting API.

---

## Conventions used throughout

- Run backend tests from `backend/`: `cd backend && python -m pytest <path> -v`.
- Run frontend tests from `frontend/`: `cd frontend && npx vitest run <path>`.
- Qualifier vocab: `start_qualifier ∈ {exact, estimated, before, unknown}`, `stop_qualifier ∈ {exact, estimated, ongoing, unknown}`.
- `human_edited_fields` is a JSON array of curated column names the human has overridden. Reconcile must never overwrite a listed field.
- Identity: `normalized_key = lower(normalized_code) if code else lower(value)`; `(patient_id, type, normalized_key)` is unique.

---

## Task 1: Migration `006_curated_facts.sql`

**Files:**
- Create: `backend/db/init/006_curated_facts.sql`
- Test: `backend/tests/test_migration_006.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_migration_006.py
from pathlib import Path

MIGRATION = Path(__file__).resolve().parents[1] / "db" / "init" / "006_curated_facts.sql"


def test_migration_file_exists():
    assert MIGRATION.is_file()


def test_migration_is_idempotent_shaped():
    """Every create statement must be guarded so re-applying the file is a no-op.

    A real double-apply needs a live Postgres; in unit context we assert the file
    only uses IF-NOT-EXISTS constructs and never bare CREATE/ALTER that would error
    on a second run."""
    sql = MIGRATION.read_text().lower()
    assert "create table if not exists curated_facts" in sql
    # Identity uniqueness + lookup indexes are both guarded.
    assert sql.count("create unique index if not exists") >= 1
    assert sql.count("create index if not exists") >= 1
    # No un-guarded create/alter that would fail on re-run.
    assert "create table curated_facts" not in sql  # i.e. only the IF NOT EXISTS form
    assert "alter table curated_facts add column " not in sql


def test_migration_declares_identity_and_state_columns():
    sql = MIGRATION.read_text().lower()
    for col in (
        "normalized_key", "display_value", "start_date", "start_qualifier",
        "stop_date", "stop_qualifier", "schedule_text", "record_state",
        "review_status", "origin", "human_edited_fields", "last_evidence_fact_id",
    ):
        assert col in sql, f"missing column {col}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_migration_006.py -v`
Expected: FAIL (file does not exist).

- [ ] **Step 3: Write the migration**

```sql
-- backend/db/init/006_curated_facts.sql
-- Curated longitudinal layer for temporal problems & medications.
-- One reconciled row per distinct clinical item per patient. Human edits live
-- here and always win over AI. The append-only `facts` table stays the AI
-- evidence trail. Idempotent: safe to re-apply (matches the numbered-migration
-- pattern in 001..005). uuid_generate_v4() / pgcrypto already provisioned in 001.

CREATE TABLE IF NOT EXISTS curated_facts (
    id                    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    patient_id            TEXT NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
    type                  TEXT NOT NULL,                       -- 'condition' | 'medication'
    normalized_key        TEXT NOT NULL,                       -- identity key
    display_value         TEXT NOT NULL,
    normalized_code       TEXT,
    coding_system         TEXT,
    start_date            DATE,                                -- may be an estimate
    start_qualifier       TEXT NOT NULL DEFAULT 'unknown',     -- exact|estimated|before|unknown
    stop_date             DATE,
    stop_qualifier        TEXT NOT NULL DEFAULT 'unknown',     -- exact|estimated|ongoing|unknown
    start_text            TEXT,                                -- original phrase ("4 months ago")
    stop_text             TEXT,
    schedule_text         TEXT,                                -- free text ("q3wk x 6 cycles")
    status                TEXT,                                -- clinical status (problems) / action (meds)
    record_state          TEXT NOT NULL DEFAULT 'active',      -- active | dismissed (soft-delete)
    review_status         TEXT NOT NULL DEFAULT 'ai_suggested',-- ai_suggested | human_confirmed
    origin                TEXT NOT NULL DEFAULT 'ai',          -- ai | human
    human_edited_fields   JSONB NOT NULL DEFAULT '[]'::jsonb,  -- column names the human overrode
    last_evidence_fact_id UUID,                                -- most recent facts.id that fed this row
    updated_by            TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS curated_facts_identity_idx
    ON curated_facts (patient_id, type, normalized_key);

CREATE INDEX IF NOT EXISTS curated_facts_patient_idx
    ON curated_facts (patient_id, type, record_state);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_migration_006.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/db/init/006_curated_facts.sql backend/tests/test_migration_006.py
git commit -m "feat(db): add curated_facts migration 006"
```

---

## Task 2: Extraction schema — qualifiers & new fields

**Files:**
- Modify: `backend/app/schemas/extraction.py` (add `Literal`s after line 18; add fields to `PatientFact` ~lines 27-46 and `MedicationChange` ~lines 59-68)
- Test: `backend/tests/test_curated_schema.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_curated_schema.py
from app.schemas.extraction import MedicationChange, PatientFact


def test_medication_change_accepts_temporal_fields():
    m = MedicationChange(
        name="paclitaxel",
        action="start",
        startDate="2026-01-10",
        startQualifier="exact",
        stopQualifier="ongoing",
        startText="started this admission",
        schedule="q3wk x 6 cycles",
    )
    assert m.startQualifier == "exact"
    assert m.stopQualifier == "ongoing"
    assert m.schedule == "q3wk x 6 cycles"
    assert m.stopDate is None


def test_medication_change_temporal_fields_default_none():
    m = MedicationChange(name="metformin")
    assert m.startDate is None
    assert m.startQualifier is None
    assert m.stopQualifier is None
    assert m.schedule is None


def test_patient_fact_accepts_onset_qualifiers():
    p = PatientFact(
        type="condition",
        value="breast cancer",
        onsetDate="2025-09-01",
        onsetQualifier="estimated",
        onsetText="about 4 months ago",
        resolvedQualifier="ongoing",
    )
    assert p.onsetQualifier == "estimated"
    assert p.onsetText == "about 4 months ago"
    assert p.resolvedQualifier == "ongoing"


def test_patient_fact_qualifiers_default_none():
    p = PatientFact(type="condition", value="diabetes")
    assert p.onsetQualifier is None
    assert p.resolvedQualifier is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_curated_schema.py -v`
Expected: FAIL with `ValidationError` / unexpected keyword (StrictBase rejects unknown fields).

- [ ] **Step 3: Add the qualifier Literals**

In `backend/app/schemas/extraction.py`, immediately after the `ClinicalStatus` Literal block (ends line 18), add:

```python
StartQualifier = Literal["exact", "estimated", "before", "unknown"]
StopQualifier = Literal["exact", "estimated", "ongoing", "unknown"]
```

- [ ] **Step 4: Add fields to `PatientFact`**

In the `PatientFact` class, immediately after the existing `resolvedDate: datetime | None = None` line, add:

```python
    onsetQualifier: StartQualifier | None = None
    resolvedQualifier: StopQualifier | None = None
    onsetText: str | None = None
    resolvedText: str | None = None
```

- [ ] **Step 5: Add fields to `MedicationChange`**

In the `MedicationChange` class, immediately after the existing `indication: str | None = None` line, add:

```python
    startDate: datetime | None = None
    startQualifier: StartQualifier | None = None
    stopDate: datetime | None = None
    stopQualifier: StopQualifier | None = None
    startText: str | None = None
    stopText: str | None = None
    schedule: str | None = None
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_curated_schema.py -v`
Expected: PASS (4 tests).

- [ ] **Step 7: Run the existing extraction tests to confirm no regression**

Run: `cd backend && python -m pytest tests/ -k "extraction or schema or graph_updater" -q`
Expected: PASS (existing tests still green — new fields are optional).

- [ ] **Step 8: Commit**

```bash
git add backend/app/schemas/extraction.py backend/tests/test_curated_schema.py
git commit -m "feat(schema): add temporal qualifiers to MedicationChange and PatientFact"
```

---

## Task 3: Prompt update for dates/qualifiers/schedule

**Files:**
- Modify: `backend/app/prompts/templates.py` (the extraction system prompt, lines 3-77)
- Test: `backend/tests/test_curated_schema.py` (append a prompt-content assertion — cheap, deterministic)

- [ ] **Step 1: Write the failing test (append to existing file)**

Append to `backend/tests/test_curated_schema.py`:

```python
def test_extraction_prompt_mentions_temporal_guidance():
    from app.prompts import templates

    text = templates.EXTRACTION_SYSTEM_PROMPT.lower() if hasattr(
        templates, "EXTRACTION_SYSTEM_PROMPT"
    ) else "".join(
        str(getattr(templates, n)) for n in dir(templates)
        if isinstance(getattr(templates, n), str)
    ).lower()
    # Estimated-date anchoring + open bounds must be described to the model.
    assert "estimated" in text
    assert "ongoing" in text
    assert "schedule" in text
    assert "encounter date" in text
```

> Note: confirm the actual constant name in `templates.py` (the explore map shows the extraction system prompt at lines 3-77). If it is not `EXTRACTION_SYSTEM_PROMPT`, the fallback branch scans all module-level strings, so the test still works — but prefer asserting the real constant directly once you've read the file.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_curated_schema.py::test_extraction_prompt_mentions_temporal_guidance -v`
Expected: FAIL (`schedule` / `encounter date` not yet in the prompt).

- [ ] **Step 3: Edit the prompt**

Open `backend/app/prompts/templates.py`. In the **medications** section of the extraction system prompt, add these instructions (match the surrounding bullet/prose style):

```
- For each medication, capture timing when stated: `startDate` and `stopDate`
  (ISO date). Use `startQualifier` (exact|estimated|before|unknown) and
  `stopQualifier` (exact|estimated|ongoing|unknown).
- Convert relative expressions ("started 2 weeks ago", "since last winter") to an
  ESTIMATED date anchored on the encounter date: set the date, set the qualifier to
  `estimated`, and keep the original phrase in `startText` / `stopText`.
- Use `stopQualifier = ongoing` when the drug is still being taken (leave `stopDate` null).
- Put dosing cadence the interval can't express in `schedule` as free text
  (e.g. "q3wk x 6 cycles", "ATB x7d").
```

In the **problems / conditions** section, extend the existing onset/resolved guidance:

```
- For onset/resolution, in addition to `onsetDate` / `resolvedDate`, set
  `onsetQualifier` (exact|estimated|before|unknown) and `resolvedQualifier`
  (exact|estimated|ongoing|unknown). Convert relative onset ("about 4 months ago")
  to an ESTIMATED date anchored on the encounter date, qualifier `estimated`, and
  keep the phrase in `onsetText`. Use `before`/`unknown` when the condition predates
  the note with no clear onset, and `ongoing` when it has not resolved.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_curated_schema.py::test_extraction_prompt_mentions_temporal_guidance -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/prompts/templates.py backend/tests/test_curated_schema.py
git commit -m "feat(prompt): instruct extractor to capture medication/problem timing and schedule"
```

---

## Task 4: Pure curated merge logic (no DB)

This is the testable heart of reconciliation. Pure functions, zero I/O.

**Files:**
- Create: `backend/app/services/curated.py`
- Test: `backend/tests/test_curated_merge.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_curated_merge.py
from app.services.curated import (
    normalized_key,
    normalize_bounds,
    ai_item_from_condition,
    ai_item_from_medication,
    merge_curated,
)
from app.schemas.extraction import MedicationChange, PatientFact


def test_normalized_key_prefers_code():
    assert normalized_key("E11.9", "Type 2 Diabetes") == "e11.9"
    assert normalized_key(None, "Type 2 Diabetes") == "type 2 diabetes"
    assert normalized_key("  ", "  Asthma ") == "asthma"


def test_normalize_bounds_defaults_and_ongoing_clears_stop():
    s_date, s_q, e_date, e_q = normalize_bounds("2026-01-01", None, "2026-02-01", "ongoing")
    assert s_q == "exact"          # date present, no qualifier -> exact
    assert e_q == "ongoing"
    assert e_date is None          # ongoing clears the stop date
    s_date, s_q, e_date, e_q = normalize_bounds(None, None, None, None)
    assert s_q == "unknown" and e_q == "unknown"


def test_ai_item_from_condition_maps_onset_resolved():
    p = PatientFact(
        type="condition", value="Breast cancer", normalizedCode="C50.9",
        codingSystem="ICD10", onsetDate="2025-09-01", onsetQualifier="estimated",
        onsetText="about 4 months ago", resolvedQualifier="ongoing", status="active",
    )
    item = ai_item_from_condition(p)
    assert item["type"] == "condition"
    assert item["normalized_key"] == "c50.9"
    assert item["display_value"] == "Breast cancer"
    assert item["start_qualifier"] == "estimated"
    assert item["start_text"] == "about 4 months ago"
    assert item["stop_qualifier"] == "ongoing"
    assert item["status"] == "active"


def test_ai_item_from_medication_maps_schedule_and_action():
    m = MedicationChange(
        name="Paclitaxel", rxNorm="56946", action="start",
        startDate="2026-01-10", startQualifier="exact", stopQualifier="ongoing",
        schedule="q3wk x 6 cycles",
    )
    item = ai_item_from_medication(m)
    assert item["type"] == "medication"
    assert item["normalized_key"] == "56946"
    assert item["display_value"] == "Paclitaxel"
    assert item["schedule_text"] == "q3wk x 6 cycles"
    assert item["status"] == "start"
    assert item["stop_qualifier"] == "ongoing"


def test_merge_new_identity_is_insert_ai_suggested():
    ai = ai_item_from_medication(MedicationChange(name="metformin", startDate="2026-01-01"))
    row, is_new = merge_curated(None, ai, resurface=False)
    assert is_new is True
    assert row["origin"] == "ai"
    assert row["review_status"] == "ai_suggested"
    assert row["record_state"] == "active"
    assert row["start_date"] == "2026-01-01"
    assert row["human_edited_fields"] == []


def test_merge_never_clobbers_human_edited_field():
    existing = {
        "display_value": "Metformin XR", "normalized_code": None, "coding_system": None,
        "start_date": "2025-12-25", "start_qualifier": "exact", "stop_date": None,
        "stop_qualifier": "ongoing", "start_text": None, "stop_text": None,
        "schedule_text": None, "status": "continue", "record_state": "active",
        "review_status": "human_confirmed", "origin": "ai",
        "human_edited_fields": ["start_date", "display_value"],
    }
    ai = ai_item_from_medication(
        MedicationChange(name="metformin", startDate="2026-01-01", action="modify")
    )
    row, is_new = merge_curated(existing, ai, resurface=False)
    assert is_new is False
    assert row["start_date"] == "2025-12-25"      # human edit preserved
    assert row["display_value"] == "Metformin XR" # human edit preserved
    assert row["status"] == "modify"              # non-human field refreshed from AI


def test_merge_fills_empty_non_human_field():
    existing = {
        "display_value": "Metformin", "normalized_code": None, "coding_system": None,
        "start_date": None, "start_qualifier": "unknown", "stop_date": None,
        "stop_qualifier": "unknown", "start_text": None, "stop_text": None,
        "schedule_text": None, "status": None, "record_state": "active",
        "review_status": "ai_suggested", "origin": "ai", "human_edited_fields": [],
    }
    ai = ai_item_from_medication(MedicationChange(name="metformin", startDate="2026-01-01"))
    row, _ = merge_curated(existing, ai, resurface=False)
    assert row["start_date"] == "2026-01-01"      # empty field filled from AI


def test_merge_resurface_reactivates_and_preserves_human_dates():
    existing = {
        "display_value": "Breast cancer", "normalized_code": "C50.9", "coding_system": "ICD10",
        "start_date": "2025-09-01", "start_qualifier": "estimated", "stop_date": None,
        "stop_qualifier": "ongoing", "start_text": "4 months ago", "stop_text": None,
        "schedule_text": None, "status": "active", "record_state": "dismissed",
        "review_status": "human_confirmed", "origin": "ai",
        "human_edited_fields": ["start_date", "start_qualifier"],
    }
    ai = ai_item_from_condition(
        PatientFact(type="condition", value="Breast cancer", normalizedCode="C50.9",
                    codingSystem="ICD10", onsetDate="2024-01-01", onsetQualifier="exact")
    )
    row, is_new = merge_curated(existing, ai, resurface=True)
    assert is_new is False
    assert row["record_state"] == "active"        # resurfaced
    assert row["review_status"] == "ai_suggested" # back to review
    assert row["start_date"] == "2025-09-01"       # human date edit preserved
    assert row["start_qualifier"] == "estimated"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_curated_merge.py -v`
Expected: FAIL (module `app.services.curated` does not exist).

- [ ] **Step 3: Write the pure logic in `curated.py`**

Create `backend/app/services/curated.py` with this content (DB functions are added in Task 6; this step delivers only the pure layer):

```python
"""Curated longitudinal layer for temporal problems & medications.

Two halves:
  * Pure merge logic (this task) — identity keys, bound normalization, mapping AI
    extraction objects to curated-row dicts, and the merge rule that never clobbers
    human-edited fields. No I/O, exhaustively unit-tested.
  * DB + graph layer (Task 6) — reconcile_curated, CRUD, propagate_curated_to_graph.
"""
from __future__ import annotations

from typing import Any

from app.schemas.extraction import MedicationChange, PatientFact

# Curated columns the AI is allowed to populate/refresh. Order is irrelevant.
AI_FILLABLE_FIELDS: tuple[str, ...] = (
    "display_value", "normalized_code", "coding_system",
    "start_date", "start_qualifier", "stop_date", "stop_qualifier",
    "start_text", "stop_text", "schedule_text", "status",
)


def normalized_key(code: str | None, value: str) -> str:
    """Identity key: lower(code) when a code is present, else lower(value)."""
    code = (code or "").strip()
    if code:
        return code.lower()
    return (value or "").strip().lower()


def _iso_date(value: Any) -> str | None:
    """Coerce a datetime/date/str to an ISO date string (YYYY-MM-DD) or None."""
    if value is None:
        return None
    if hasattr(value, "date"):       # datetime
        return value.date().isoformat()
    if hasattr(value, "isoformat"):  # date
        return value.isoformat()
    s = str(value).strip()
    return s[:10] if s else None     # tolerate "2026-01-01T..." strings


def normalize_bounds(
    start_date: Any, start_qualifier: str | None,
    stop_date: Any, stop_qualifier: str | None,
) -> tuple[str | None, str, str | None, str]:
    """Apply default qualifiers and resolve impossible combinations.

    - Missing qualifier -> 'exact' if a date is present, else 'unknown'.
    - stop_qualifier 'ongoing' clears any stop_date (an ongoing item has no end).
    """
    start_date = _iso_date(start_date)
    stop_date = _iso_date(stop_date)
    start_q = start_qualifier or ("exact" if start_date else "unknown")
    stop_q = stop_qualifier or ("exact" if stop_date else "unknown")
    if stop_q == "ongoing":
        stop_date = None
    return start_date, start_q, stop_date, stop_q


def ai_item_from_condition(p: PatientFact) -> dict[str, Any]:
    s_date, s_q, e_date, e_q = normalize_bounds(
        p.onsetDate, p.onsetQualifier, p.resolvedDate, p.resolvedQualifier
    )
    return {
        "type": "condition",
        "normalized_key": normalized_key(p.normalizedCode, p.value),
        "display_value": p.value,
        "normalized_code": p.normalizedCode,
        "coding_system": p.codingSystem,
        "start_date": s_date, "start_qualifier": s_q,
        "stop_date": e_date, "stop_qualifier": e_q,
        "start_text": p.onsetText, "stop_text": p.resolvedText,
        "schedule_text": None,
        "status": p.status,
    }


def ai_item_from_medication(m: MedicationChange) -> dict[str, Any]:
    s_date, s_q, e_date, e_q = normalize_bounds(
        m.startDate, m.startQualifier, m.stopDate, m.stopQualifier
    )
    coding_system = "RxNorm" if m.rxNorm else None
    return {
        "type": "medication",
        "normalized_key": normalized_key(m.rxNorm, m.name),
        "display_value": m.name,
        "normalized_code": m.rxNorm,
        "coding_system": coding_system,
        "start_date": s_date, "start_qualifier": s_q,
        "stop_date": e_date, "stop_qualifier": e_q,
        "start_text": m.startText, "stop_text": m.stopText,
        "schedule_text": m.schedule,
        "status": m.action,
    }


def merge_curated(
    existing: dict[str, Any] | None, ai: dict[str, Any], *, resurface: bool
) -> tuple[dict[str, Any], bool]:
    """Compute the curated row to persist.

    Returns (row, is_new). When existing is None -> fresh ai_suggested row.
    Otherwise merge field-by-field: human-edited fields are preserved verbatim;
    every other AI-fillable field takes the AI value when AI supplies one
    (non-None), else keeps the existing value. Resurface flips a dismissed row
    back to active/ai_suggested while keeping the merged (human-preserving) values.
    """
    if existing is None:
        row = {
            "type": ai["type"],
            "normalized_key": ai["normalized_key"],
            "record_state": "active",
            "review_status": "ai_suggested",
            "origin": "ai",
            "human_edited_fields": [],
        }
        for f in AI_FILLABLE_FIELDS:
            row[f] = ai.get(f)
        return row, True   # is_new=True for a brand-new identity

    edited = set(existing.get("human_edited_fields") or [])
    row = dict(existing)
    for f in AI_FILLABLE_FIELDS:
        if f in edited:
            continue                       # human wins — never overwrite
        ai_val = ai.get(f)
        if ai_val is not None:
            row[f] = ai_val                # fill-empty + refresh in one rule
    if resurface:
        row["record_state"] = "active"
        row["review_status"] = "ai_suggested"
    return row, False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_curated_merge.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/curated.py backend/tests/test_curated_merge.py
git commit -m "feat(curated): pure merge logic for the curated longitudinal layer"
```

---

## Task 5: Extend the test harness `FakeStore` for `curated_facts`

The integration tests in Tasks 6–9 hit `curated_facts` through `db_session()`. The hand-written `FakeStore` (conftest.py lines 75-547) must recognize the curated statements. To keep matching stable, the DB layer (Task 6) issues a fixed set of named SQL constants; here we teach the fake to back them with an in-memory list.

**Files:**
- Modify: `backend/tests/conftest.py`
- Test: `backend/tests/test_curated_api.py` (a single smoke test added now, expanded in Task 9)

- [ ] **Step 1: Write the failing smoke test**

```python
# backend/tests/test_curated_api.py
def test_curated_list_empty_for_new_patient(app_client, fake_store):
    fake_store.patients["HN1"] = {"patient_id": "HN1", "name": "Jane"}
    r = app_client.get("/api/patient/HN1/curated", params={"type": "medication"})
    assert r.status_code == 200
    assert r.json() == {"items": []}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_curated_api.py -v`
Expected: FAIL (endpoint 404 / store has no curated table). This also fails because Task 6/7 aren't done — that's expected; this task only makes the *store* ready. Re-running after Task 7 is what turns it green; for now confirm the failure is "no such route" or a store KeyError, not a conftest import error.

- [ ] **Step 3: Add a `curated_facts` table to `FakeStore.__init__`**

In `backend/tests/conftest.py`, inside `FakeStore.__init__` where the other in-memory tables are declared (e.g. alongside `self.facts = []`), add:

```python
        self.curated_facts: list[dict] = []   # curated longitudinal rows
```

- [ ] **Step 4: Add statement matchers to `FakeStore.execute`**

In `FakeStore.execute`, before the final fall-through, add handlers keyed on the named SQL constants defined in Task 6. Match on a stable substring so whitespace changes don't break it:

```python
        # ---- curated_facts -------------------------------------------------
        if "from curated_facts" in low and "select" in low:
            rows = [
                dict(r) for r in self.curated_facts
                if r["patient_id"] == params.get("pid", params.get("patient_id"))
            ]
            if "type = :type" in low:
                rows = [r for r in rows if r["type"] == params["type"]]
            if "normalized_key = :nk" in low:
                rows = [r for r in rows if r["normalized_key"] == params["nk"]]
            if "record_state = 'active'" in low:
                rows = [r for r in rows if r["record_state"] == "active"]
            if "id = cast(:cid as uuid)" in low or "id = :cid" in low:
                rows = [r for r in rows if str(r["id"]) == str(params["cid"])]
            return FakeResult(rows)

        if low.startswith("insert into curated_facts"):
            row = dict(params)
            row.setdefault("id", f"curated-{len(self.curated_facts) + 1}")
            # human_edited_fields arrives as a JSON string param; store as list.
            import json as _json
            hef = row.get("human_edited_fields")
            if isinstance(hef, str):
                row["human_edited_fields"] = _json.loads(hef)
            self.curated_facts.append(row)
            return FakeResult([{"id": row["id"]}])

        if low.startswith("update curated_facts"):
            import json as _json
            for r in self.curated_facts:
                if str(r["id"]) == str(params.get("cid", params.get("id"))):
                    for k, v in params.items():
                        if k in ("cid", "id"):
                            continue
                        if k == "human_edited_fields" and isinstance(v, str):
                            v = _json.loads(v)
                        r[k] = v
            return FakeResult([])
```

> Adjust the attribute names (`self.patients`, `FakeResult`) to whatever the existing `FakeStore` uses — read lines 75-547 first and mirror the exact helper names. `low` is the lower-cased SQL; if the existing `execute` doesn't already compute it, add `low = sql.lower()` at the top (the existing matchers will show the variable name in use).

- [ ] **Step 5: Commit (store scaffolding only)**

```bash
git add backend/tests/conftest.py backend/tests/test_curated_api.py
git commit -m "test: back curated_facts in the FakeStore harness"
```

> The smoke test stays red until Task 7 adds the route. That's acceptable for a scaffolding commit; Task 7 turns it green.

---

## Task 6: Curated DB layer + reconcile + graph propagation

**Files:**
- Modify: `backend/app/services/curated.py` (append the DB + graph functions)
- Test: `backend/tests/test_reconcile.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_reconcile.py
from app.services.curated import reconcile_curated, list_curated
from app.schemas.extraction import ClinicalExtractionResult, MedicationChange, PatientFact


def _extraction(**kw):
    return ClinicalExtractionResult(
        patientId="HN1", encounterId="E1", documentId="D1", **kw
    )


def test_reconcile_inserts_new_curated_rows(fake_store, stub_neo4j):
    fake_store.patients["HN1"] = {"patient_id": "HN1", "name": "Jane"}
    ex = _extraction(
        problems=[PatientFact(type="condition", value="Breast cancer",
                              normalizedCode="C50.9", codingSystem="ICD10",
                              onsetDate="2025-09-01", onsetQualifier="estimated")],
        medications=[MedicationChange(name="paclitaxel", rxNorm="56946",
                                      action="start", schedule="q3wk x 6 cycles")],
    )
    reconcile_curated("HN1", ex)
    rows = {r["normalized_key"]: r for r in fake_store.curated_facts}
    assert "c50.9" in rows and rows["c50.9"]["review_status"] == "ai_suggested"
    assert rows["56946"]["schedule_text"] == "q3wk x 6 cycles"
    # graph propagation happened
    assert any("Condition" in q or "Medication" in q for q, _ in stub_neo4j)


def test_reconcile_preserves_human_edits_on_rementioning(fake_store, stub_neo4j):
    fake_store.patients["HN1"] = {"patient_id": "HN1", "name": "Jane"}
    fake_store.curated_facts.append({
        "id": "cur1", "patient_id": "HN1", "type": "medication",
        "normalized_key": "56946", "display_value": "Paclitaxel",
        "normalized_code": "56946", "coding_system": "RxNorm",
        "start_date": "2026-01-10", "start_qualifier": "exact",
        "stop_date": None, "stop_qualifier": "ongoing",
        "start_text": None, "stop_text": None, "schedule_text": "q3wk x 6 cycles",
        "status": "start", "record_state": "dismissed",
        "review_status": "human_confirmed", "origin": "ai",
        "human_edited_fields": ["start_date"], "last_evidence_fact_id": None,
    })
    ex = _extraction(medications=[
        MedicationChange(name="paclitaxel", rxNorm="56946", action="continue",
                         startDate="2099-01-01")
    ])
    reconcile_curated("HN1", ex)
    row = next(r for r in fake_store.curated_facts if r["normalized_key"] == "56946")
    assert row["record_state"] == "active"          # resurfaced
    assert row["review_status"] == "ai_suggested"
    assert row["start_date"] == "2026-01-10"         # human edit preserved
    assert row["status"] == "continue"               # AI refresh on non-human field


def test_list_curated_returns_only_active(fake_store):
    fake_store.patients["HN1"] = {"patient_id": "HN1"}
    fake_store.curated_facts.extend([
        {"id": "a", "patient_id": "HN1", "type": "condition", "normalized_key": "x",
         "display_value": "X", "record_state": "active", "review_status": "ai_suggested",
         "human_edited_fields": []},
        {"id": "b", "patient_id": "HN1", "type": "condition", "normalized_key": "y",
         "display_value": "Y", "record_state": "dismissed", "review_status": "ai_suggested",
         "human_edited_fields": []},
    ])
    items = list_curated("HN1", "condition")
    keys = {i["normalized_key"] for i in items}
    assert keys == {"x"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_reconcile.py -v`
Expected: FAIL (`reconcile_curated` / `list_curated` not defined).

- [ ] **Step 3: Append the DB + graph layer to `curated.py`**

Add to the imports at the top of `backend/app/services/curated.py`:

```python
import json
import logging

from sqlalchemy import text

from app.db.postgres import db_session
from app.schemas.extraction import ClinicalExtractionResult

logger = logging.getLogger(__name__)
```

Then append:

```python
# --- SQL (named constants so the test FakeStore can match them) --------------

_SELECT_BY_IDENTITY = text("""
SELECT * FROM curated_facts
WHERE patient_id = :pid AND type = :type AND normalized_key = :nk
""")

_SELECT_ACTIVE_BY_TYPE = text("""
SELECT * FROM curated_facts
WHERE patient_id = :pid AND type = :type AND record_state = 'active'
ORDER BY display_value ASC
""")

_SELECT_BY_ID = text("SELECT * FROM curated_facts WHERE id = CAST(:cid AS uuid)")

_INSERT = text("""
INSERT INTO curated_facts (
    patient_id, type, normalized_key, display_value, normalized_code, coding_system,
    start_date, start_qualifier, stop_date, stop_qualifier, start_text, stop_text,
    schedule_text, status, record_state, review_status, origin,
    human_edited_fields, last_evidence_fact_id, updated_by
) VALUES (
    :patient_id, :type, :normalized_key, :display_value, :normalized_code, :coding_system,
    :start_date, :start_qualifier, :stop_date, :stop_qualifier, :start_text, :stop_text,
    :schedule_text, :status, :record_state, :review_status, :origin,
    CAST(:human_edited_fields AS jsonb), :last_evidence_fact_id, :updated_by
)
RETURNING id
""")

_UPDATE = text("""
UPDATE curated_facts SET
    display_value = :display_value, normalized_code = :normalized_code,
    coding_system = :coding_system, start_date = :start_date,
    start_qualifier = :start_qualifier, stop_date = :stop_date,
    stop_qualifier = :stop_qualifier, start_text = :start_text, stop_text = :stop_text,
    schedule_text = :schedule_text, status = :status, record_state = :record_state,
    review_status = :review_status, human_edited_fields = CAST(:human_edited_fields AS jsonb),
    last_evidence_fact_id = :last_evidence_fact_id, updated_by = :updated_by,
    updated_at = now()
WHERE id = CAST(:cid AS uuid)
""")


def _to_dict(row) -> dict[str, Any]:
    d = dict(row)
    if "id" in d and d["id"] is not None:
        d["id"] = str(d["id"])
    hef = d.get("human_edited_fields")
    if isinstance(hef, str):
        d["human_edited_fields"] = json.loads(hef)
    elif hef is None:
        d["human_edited_fields"] = []
    return d


def list_curated(patient_id: str, type_: str) -> list[dict[str, Any]]:
    with db_session() as s:
        rows = s.execute(_SELECT_ACTIVE_BY_TYPE, {"pid": patient_id, "type": type_}).mappings().all()
    return [_to_dict(r) for r in rows]


def get_curated(cid: str) -> dict[str, Any] | None:
    with db_session() as s:
        row = s.execute(_SELECT_BY_ID, {"cid": cid}).mappings().first()
    return _to_dict(row) if row else None


def _persist_merged(s, *, patient_id: str, existing: dict | None, row: dict,
                    is_new: bool, evidence_fact_id: str | None, updated_by: str | None) -> str:
    payload = {
        "patient_id": patient_id,
        "type": row["type"],
        "normalized_key": row["normalized_key"],
        "display_value": row["display_value"],
        "normalized_code": row.get("normalized_code"),
        "coding_system": row.get("coding_system"),
        "start_date": row.get("start_date"),
        "start_qualifier": row.get("start_qualifier") or "unknown",
        "stop_date": row.get("stop_date"),
        "stop_qualifier": row.get("stop_qualifier") or "unknown",
        "start_text": row.get("start_text"),
        "stop_text": row.get("stop_text"),
        "schedule_text": row.get("schedule_text"),
        "status": row.get("status"),
        "record_state": row.get("record_state", "active"),
        "review_status": row.get("review_status", "ai_suggested"),
        "origin": row.get("origin", "ai"),
        "human_edited_fields": json.dumps(row.get("human_edited_fields") or []),
        "last_evidence_fact_id": evidence_fact_id,
        "updated_by": updated_by,
    }
    if is_new:
        res = s.execute(_INSERT, payload).mappings().first()
        return str(res["id"]) if res else ""
    payload["cid"] = existing["id"]
    s.execute(_UPDATE, payload)
    return str(existing["id"])


def reconcile_curated(patient_id: str, extraction: ClinicalExtractionResult) -> None:
    """Upsert each AI problem/medication into curated_facts by identity, then push
    the resulting values into Neo4j. Best-effort and isolated per item — one failure
    is logged and never aborts the others or the ingest."""
    ai_items: list[dict[str, Any]] = []
    for p in getattr(extraction, "problems", []) or []:
        ai_items.append(ai_item_from_condition(p))
    for m in getattr(extraction, "medications", []) or []:
        ai_items.append(ai_item_from_medication(m))

    for ai in ai_items:
        try:
            with db_session() as s:
                existing_row = s.execute(
                    _SELECT_BY_IDENTITY,
                    {"pid": patient_id, "type": ai["type"], "nk": ai["normalized_key"]},
                ).mappings().first()
                existing = _to_dict(existing_row) if existing_row else None
                resurface = bool(existing) and existing.get("record_state") == "dismissed"
                merged, is_new = merge_curated(existing, ai, resurface=resurface)
                cid = _persist_merged(
                    s, patient_id=patient_id, existing=existing, row=merged,
                    is_new=is_new, evidence_fact_id=None, updated_by=None,
                )
            persisted = get_curated(cid) if cid else merged
            propagate_curated_to_graph(patient_id, persisted)
        except Exception:  # noqa: BLE001 — resilience matches graph-write behavior
            logger.exception("curated reconcile failed for %s", ai.get("normalized_key"))


# --- Neo4j propagation -------------------------------------------------------

_CYPHER_CONDITION = """
MERGE (c:Condition {patientId: $patientId, value: $value})
  ON CREATE SET c.firstSeen = datetime()
  SET c.onsetDate = $startDate, c.resolvedDate = $stopDate,
      c.startQualifier = $startQualifier, c.stopQualifier = $stopQualifier,
      c.status = coalesce($status, c.status),
      c.curatedReviewStatus = $reviewStatus, c.lastSeen = datetime()
"""

_CYPHER_MEDICATION = """
MERGE (m:Medication {patientId: $patientId, name: $value})
  ON CREATE SET m.firstSeen = datetime()
  SET m.startDate = $startDate, m.stopDate = $stopDate,
      m.startQualifier = $startQualifier, m.stopQualifier = $stopQualifier,
      m.scheduleText = $scheduleText, m.lastAction = coalesce($status, m.lastAction),
      m.curatedReviewStatus = $reviewStatus, m.lastSeen = datetime()
"""


def propagate_curated_to_graph(patient_id: str, row: dict[str, Any]) -> None:
    """Push curated values into the matching Neo4j node. Best-effort."""
    from app.db.neo4j import run_cypher  # local import: keeps pure layer import-light

    params = {
        "patientId": patient_id,
        "value": row["display_value"],
        "startDate": row.get("start_date"),
        "stopDate": row.get("stop_date"),
        "startQualifier": row.get("start_qualifier"),
        "stopQualifier": row.get("stop_qualifier"),
        "scheduleText": row.get("schedule_text"),
        "status": row.get("status"),
        "reviewStatus": row.get("review_status"),
    }
    cypher = _CYPHER_CONDITION if row["type"] == "condition" else _CYPHER_MEDICATION
    try:
        run_cypher(cypher, params)
    except Exception:  # noqa: BLE001
        logger.exception("curated graph propagation failed for %s", row.get("display_value"))
```

> **Verify the Neo4j helper import path** before running: the explore map shows graph writes go through a `run_cypher` / `neo4j_session` helper and `stub_neo4j` patches it. Confirm the module (likely `app.db.neo4j` or `app.services.graph_updater`) and import `run_cypher` from wherever `stub_neo4j` monkeypatches it, so the stub intercepts these calls in tests.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_reconcile.py -v`
Expected: PASS (3 tests). If `propagate_curated_to_graph` import fails, fix the `run_cypher` import path per the note above.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/curated.py backend/tests/test_reconcile.py
git commit -m "feat(curated): reconcile + graph propagation DB layer"
```

---

## Task 7: Curated API schemas + endpoints

**Files:**
- Create: `backend/app/schemas/curated.py`
- Modify: `backend/app/routers/patient.py` (imports + five endpoints)
- Test: `backend/tests/test_curated_api.py` (expand the file from Task 5)

- [ ] **Step 1: Write the failing tests (replace the Task-5 smoke test file contents)**

```python
# backend/tests/test_curated_api.py
import pytest


@pytest.fixture()
def patient(fake_store):
    fake_store.patients["HN1"] = {"patient_id": "HN1", "name": "Jane"}
    return "HN1"


def test_list_empty(app_client, patient):
    r = app_client.get(f"/api/patient/{patient}/curated", params={"type": "medication"})
    assert r.status_code == 200
    assert r.json() == {"items": []}


def test_manual_insert_then_list(app_client, patient, stub_neo4j):
    body = {
        "type": "medication", "displayValue": "Warfarin",
        "startDate": "2026-02-01", "startQualifier": "exact",
        "stopQualifier": "ongoing", "scheduleText": "5mg daily", "status": "start",
    }
    r = app_client.post(f"/api/patient/{patient}/curated", json=body)
    assert r.status_code == 200, r.text
    created = r.json()
    assert created["origin"] == "human"
    assert created["reviewStatus"] == "human_confirmed"
    assert created["displayValue"] == "Warfarin"

    r2 = app_client.get(f"/api/patient/{patient}/curated", params={"type": "medication"})
    items = r2.json()["items"]
    assert len(items) == 1 and items[0]["displayValue"] == "Warfarin"


def test_patch_marks_field_human_edited(app_client, patient, fake_store, stub_neo4j):
    fake_store.curated_facts.append({
        "id": "cur1", "patient_id": "HN1", "type": "medication",
        "normalized_key": "warfarin", "display_value": "Warfarin",
        "normalized_code": None, "coding_system": None, "start_date": "2026-01-01",
        "start_qualifier": "exact", "stop_date": None, "stop_qualifier": "ongoing",
        "start_text": None, "stop_text": None, "schedule_text": None, "status": "start",
        "record_state": "active", "review_status": "ai_suggested", "origin": "ai",
        "human_edited_fields": [], "last_evidence_fact_id": None,
    })
    r = app_client.patch("/api/curated/cur1", json={"startDate": "2025-12-25"})
    assert r.status_code == 200, r.text
    row = next(r for r in fake_store.curated_facts if r["id"] == "cur1")
    assert row["start_date"] == "2025-12-25"
    assert "start_date" in row["human_edited_fields"]
    assert row["review_status"] == "human_confirmed"


def test_patch_ongoing_clears_stop_date(app_client, patient, fake_store, stub_neo4j):
    fake_store.curated_facts.append({
        "id": "cur2", "patient_id": "HN1", "type": "medication",
        "normalized_key": "warfarin", "display_value": "Warfarin",
        "normalized_code": None, "coding_system": None, "start_date": "2026-01-01",
        "start_qualifier": "exact", "stop_date": "2026-03-01", "stop_qualifier": "exact",
        "start_text": None, "stop_text": None, "schedule_text": None, "status": "start",
        "record_state": "active", "review_status": "ai_suggested", "origin": "ai",
        "human_edited_fields": [], "last_evidence_fact_id": None,
    })
    r = app_client.patch("/api/curated/cur2", json={"stopQualifier": "ongoing"})
    assert r.status_code == 200, r.text
    row = next(r for r in fake_store.curated_facts if r["id"] == "cur2")
    assert row["stop_date"] is None


def test_delete_then_restore(app_client, patient, fake_store, stub_neo4j):
    fake_store.curated_facts.append({
        "id": "cur3", "patient_id": "HN1", "type": "condition",
        "normalized_key": "asthma", "display_value": "Asthma",
        "normalized_code": None, "coding_system": None, "start_date": None,
        "start_qualifier": "unknown", "stop_date": None, "stop_qualifier": "ongoing",
        "start_text": None, "stop_text": None, "schedule_text": None, "status": "active",
        "record_state": "active", "review_status": "ai_suggested", "origin": "ai",
        "human_edited_fields": [], "last_evidence_fact_id": None,
    })
    r = app_client.delete("/api/curated/cur3")
    assert r.status_code == 200
    row = next(r for r in fake_store.curated_facts if r["id"] == "cur3")
    assert row["record_state"] == "dismissed"
    r2 = app_client.post("/api/curated/cur3/restore")
    assert r2.status_code == 200
    assert row["record_state"] == "active"


def test_patch_missing_id_404(app_client, patient):
    r = app_client.patch("/api/curated/nope", json={"startDate": "2026-01-01"})
    assert r.status_code == 404


def test_delete_missing_id_404(app_client, patient):
    r = app_client.delete("/api/curated/nope")
    assert r.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_curated_api.py -v`
Expected: FAIL (routes not defined → 404 on POST/PATCH/etc).

- [ ] **Step 3: Create the API schemas**

```python
# backend/app/schemas/curated.py
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.extraction import StartQualifier, StopQualifier

CuratedType = Literal["condition", "medication"]


class CuratedItem(BaseModel):
    """Response shape for one curated row (camelCase for the Vue frontend)."""
    model_config = ConfigDict(populate_by_name=True)

    id: str
    type: CuratedType
    displayValue: str = Field(alias="display_value")
    normalizedCode: str | None = Field(default=None, alias="normalized_code")
    codingSystem: str | None = Field(default=None, alias="coding_system")
    startDate: str | None = Field(default=None, alias="start_date")
    startQualifier: str = Field(alias="start_qualifier")
    stopDate: str | None = Field(default=None, alias="stop_date")
    stopQualifier: str = Field(alias="stop_qualifier")
    startText: str | None = Field(default=None, alias="start_text")
    stopText: str | None = Field(default=None, alias="stop_text")
    scheduleText: str | None = Field(default=None, alias="schedule_text")
    status: str | None = None
    recordState: str = Field(alias="record_state")
    reviewStatus: str = Field(alias="review_status")
    origin: str
    humanEditedFields: list[str] = Field(default_factory=list, alias="human_edited_fields")


class CuratedList(BaseModel):
    items: list[CuratedItem]


class CuratedCreate(BaseModel):
    type: CuratedType
    displayValue: str
    normalizedCode: str | None = None
    codingSystem: str | None = None
    startDate: str | None = None
    startQualifier: StartQualifier | None = None
    stopDate: str | None = None
    stopQualifier: StopQualifier | None = None
    startText: str | None = None
    stopText: str | None = None
    scheduleText: str | None = None
    status: str | None = None


class CuratedPatch(BaseModel):
    """All optional — only supplied fields are touched and marked human-edited."""
    displayValue: str | None = None
    startDate: str | None = None
    startQualifier: StartQualifier | None = None
    stopDate: str | None = None
    stopQualifier: StopQualifier | None = None
    startText: str | None = None
    stopText: str | None = None
    scheduleText: str | None = None
    status: str | None = None
```

- [ ] **Step 4: Add the CRUD helpers to `curated.py`**

Append to `backend/app/services/curated.py`:

```python
# camelCase patch field -> curated column. Drives which columns a PATCH touches.
PATCH_FIELD_TO_COLUMN: dict[str, str] = {
    "displayValue": "display_value",
    "startDate": "start_date",
    "startQualifier": "start_qualifier",
    "stopDate": "stop_date",
    "stopQualifier": "stop_qualifier",
    "startText": "start_text",
    "stopText": "stop_text",
    "scheduleText": "schedule_text",
    "status": "status",
}

_UPDATE_STATE = text("""
UPDATE curated_facts SET record_state = :state, updated_at = now()
WHERE id = CAST(:cid AS uuid)
""")


def insert_curated(patient_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Manual human insert: origin=human, review_status=human_confirmed."""
    code = payload.get("normalizedCode")
    value = payload["displayValue"]
    s_date, s_q, e_date, e_q = normalize_bounds(
        payload.get("startDate"), payload.get("startQualifier"),
        payload.get("stopDate"), payload.get("stopQualifier"),
    )
    row = {
        "type": payload["type"],
        "normalized_key": normalized_key(code, value),
        "display_value": value,
        "normalized_code": code,
        "coding_system": payload.get("codingSystem"),
        "start_date": s_date, "start_qualifier": s_q,
        "stop_date": e_date, "stop_qualifier": e_q,
        "start_text": payload.get("startText"), "stop_text": payload.get("stopText"),
        "schedule_text": payload.get("scheduleText"), "status": payload.get("status"),
        "record_state": "active", "review_status": "human_confirmed", "origin": "human",
        "human_edited_fields": list(PATCH_FIELD_TO_COLUMN.values()),
    }
    with db_session() as s:
        cid = _persist_merged(
            s, patient_id=patient_id, existing=None, row=row, is_new=True,
            evidence_fact_id=None, updated_by="human",
        )
    persisted = get_curated(cid)
    if persisted:
        propagate_curated_to_graph(patient_id, persisted)
    return persisted


def update_curated(cid: str, patch: dict[str, Any]) -> dict[str, Any] | None:
    """Apply a partial edit. Touched columns join human_edited_fields; review flips
    to human_confirmed. stop_qualifier=ongoing normalizes the stop date away."""
    existing = get_curated(cid)
    if existing is None:
        return None
    edited = set(existing.get("human_edited_fields") or [])
    merged = dict(existing)
    for field, col in PATCH_FIELD_TO_COLUMN.items():
        if field in patch and patch[field] is not None:
            merged[col] = patch[field]
            edited.add(col)
    s_date, s_q, e_date, e_q = normalize_bounds(
        merged.get("start_date"), merged.get("start_qualifier"),
        merged.get("stop_date"), merged.get("stop_qualifier"),
    )
    merged.update(start_date=s_date, start_qualifier=s_q, stop_date=e_date, stop_qualifier=e_q)
    merged["review_status"] = "human_confirmed"
    merged["human_edited_fields"] = sorted(edited)
    with db_session() as s:
        _persist_merged(
            s, patient_id=existing.get("patient_id") or "", existing=existing,
            row=merged, is_new=False, evidence_fact_id=existing.get("last_evidence_fact_id"),
            updated_by="human",
        )
    persisted = get_curated(cid)
    if persisted:
        propagate_curated_to_graph(persisted.get("patient_id") or "", persisted)
    return persisted


def set_record_state(cid: str, state: str) -> dict[str, Any] | None:
    existing = get_curated(cid)
    if existing is None:
        return None
    with db_session() as s:
        s.execute(_UPDATE_STATE, {"cid": cid, "state": state})
    return get_curated(cid)
```

> The `_persist_merged` `_UPDATE` statement (Task 6) does not write `patient_id`/`type`/`normalized_key`; `update_curated`/manual insert above pass `patient_id` only to `propagate_curated_to_graph`. Ensure `get_curated`'s SELECT returns `patient_id` (it uses `SELECT *`, so it does). If the FakeStore rows omit `patient_id`, add it — the Task-7 tests set it.

- [ ] **Step 5: Wire the endpoints into `patient.py`**

Add to the imports block at the top of `backend/app/routers/patient.py`:

```python
from app.schemas.curated import CuratedCreate, CuratedItem, CuratedList, CuratedPatch
from app.services.curated import (
    insert_curated,
    list_curated,
    set_record_state,
    update_curated,
)
```

Append these endpoints to the router (after the existing `PATCH /facts/{fact_id}/review`):

```python
@router.get("/patient/{patient_id}/curated", response_model=CuratedList)
def get_curated_list(patient_id: str, type: str = Query(..., pattern="^(condition|medication)$")):
    rows = list_curated(patient_id, type)
    return {"items": [CuratedItem.model_validate(r) for r in rows]}


@router.post("/patient/{patient_id}/curated", response_model=CuratedItem)
def create_curated(patient_id: str, body: CuratedCreate):
    row = insert_curated(patient_id, body.model_dump())
    return CuratedItem.model_validate(row)


@router.patch("/curated/{cid}", response_model=CuratedItem)
def patch_curated(cid: str, body: CuratedPatch):
    row = update_curated(cid, body.model_dump(exclude_unset=True))
    if row is None:
        raise HTTPException(status_code=404, detail="curated fact not found")
    return CuratedItem.model_validate(row)


@router.delete("/curated/{cid}")
def delete_curated(cid: str):
    row = set_record_state(cid, "dismissed")
    if row is None:
        raise HTTPException(status_code=404, detail="curated fact not found")
    return {"id": cid, "recordState": "dismissed"}


@router.post("/curated/{cid}/restore", response_model=CuratedItem)
def restore_curated(cid: str):
    row = set_record_state(cid, "active")
    if row is None:
        raise HTTPException(status_code=404, detail="curated fact not found")
    return CuratedItem.model_validate(row)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_curated_api.py -v`
Expected: PASS (8 tests). The Task-5 smoke test (`test_curated_list_empty_for_new_patient`, if still present) now also passes.

- [ ] **Step 7: Run the full backend suite to confirm no regression**

Run: `cd backend && python -m pytest tests/ -q`
Expected: PASS (all green).

- [ ] **Step 8: Commit**

```bash
git add backend/app/schemas/curated.py backend/app/routers/patient.py backend/app/services/curated.py backend/tests/test_curated_api.py
git commit -m "feat(api): curated CRUD endpoints (list/insert/patch/delete/restore)"
```

---

## Task 8: Wire reconcile into the ingest flow

**Files:**
- Modify: `backend/app/services/ingest.py` (after `_persist_post_extraction`, where facts are persisted; lines ~212-227 and the ingest body ~230-324)
- Test: `backend/tests/test_reconcile.py` (append an ingest-integration assertion)

- [ ] **Step 1: Write the failing test (append)**

```python
def test_reconcile_called_after_facts_persisted(fake_store, stub_neo4j, monkeypatch):
    """The ingest flow runs reconcile_curated once per successful extraction."""
    import app.services.ingest as ingest

    calls = []
    monkeypatch.setattr(
        ingest, "reconcile_curated",
        lambda pid, extraction: calls.append((pid, extraction)),
    )
    ex = _extraction(
        problems=[PatientFact(type="condition", value="Asthma")],
        medications=[MedicationChange(name="albuterol")],
    )
    ingest._persist_post_extraction(
        patient={"patientId": "HN1"},
        encounter={"encounterId": "E1"},
        document={"documentId": "D1"},
        extraction=ex, valid=True, errors=[],
    )
    assert len(calls) == 1
    assert calls[0][0] == "HN1"
```

> If `_persist_post_extraction` is `async`, adapt the test to `await` it (wrap with `pytest.mark.asyncio` per the suite's convention — check an existing async test). The explore map shows it as a sync function called within the async ingest, so the sync form above should match.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_reconcile.py::test_reconcile_called_after_facts_persisted -v`
Expected: FAIL (`ingest` has no attribute `reconcile_curated`).

- [ ] **Step 3: Import and call reconcile in ingest**

In `backend/app/services/ingest.py`, add to the imports:

```python
from app.services.curated import reconcile_curated
```

In `_persist_post_extraction`, after the facts are inserted and the `FACTS_PERSISTED` audit is written (after the `if rows: s.execute(...)` / audit block, still inside the function but **after** the `with db_session()` block closes so facts are committed), add:

```python
    # Reconcile AI mentions into the curated longitudinal layer. Best-effort:
    # reconcile_curated isolates per-item failures internally.
    if valid:
        reconcile_curated(patient["patientId"], extraction)
```

> Place this so it runs only on the `valid` path and after the facts-commit `with` block returns. If the function early-returns on `not valid` (the explore map shows it does), the `if valid:` guard is belt-and-suspenders — keep it for clarity.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_reconcile.py -v`
Expected: PASS.

- [ ] **Step 5: Run an end-to-end ingest test to confirm curated rows appear**

Run: `cd backend && python -m pytest tests/ -k "ingest" -q`
Expected: PASS (existing ingest tests still green; curated reconcile runs without error against the FakeStore).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/ingest.py backend/tests/test_reconcile.py
git commit -m "feat(ingest): reconcile curated facts after each extraction"
```

---

## Task 9: Backend phase checkpoint

- [ ] **Step 1: Full suite + lint**

Run: `cd backend && python -m pytest tests/ -q`
Expected: all PASS.

If the project has a linter/formatter configured (check `backend/pyproject.toml` for ruff/black), run it:
Run: `cd backend && ruff check app tests && ruff format --check app tests` (skip if not configured).

- [ ] **Step 2: Manual sanity (optional, requires running stack)**

If a local stack is available: apply migration 006, ingest a note mentioning a dated medication, then `GET /api/patient/<id>/curated?type=medication` and confirm the row appears with the date. Otherwise rely on the test suite.

- [ ] **Step 3: Commit any cleanup, then proceed to frontend.**

---

## Task 10: Date-range formatter util (frontend)

**Files:**
- Create: `frontend/src/utils/dateRange.js`
- Test: `frontend/tests/utils.dateRange.spec.js`

- [ ] **Step 1: Write the failing test**

```javascript
// frontend/tests/utils.dateRange.spec.js
import { describe, expect, it } from 'vitest'
import { formatEndpoint, formatDateRange } from '../src/utils/dateRange.js'

describe('formatEndpoint', () => {
  it('exact shows the date', () => {
    expect(formatEndpoint('2024-02-07', 'exact')).toBe('2024-02-07')
  })
  it('estimated prefixes with ~', () => {
    expect(formatEndpoint('2025-09-01', 'estimated')).toBe('~2025-09-01')
  })
  it('before with a date', () => {
    expect(formatEndpoint('2024-03-01', 'before')).toBe('before 2024-03-01')
  })
  it('ongoing ignores any date', () => {
    expect(formatEndpoint(null, 'ongoing')).toBe('ongoing')
    expect(formatEndpoint('2024-01-01', 'ongoing')).toBe('ongoing')
  })
  it('unknown with no date', () => {
    expect(formatEndpoint(null, 'unknown')).toBe('unknown')
  })
})

describe('formatDateRange', () => {
  it('estimated start to ongoing', () => {
    expect(
      formatDateRange({ startDate: '2025-09-01', startQualifier: 'estimated', stopQualifier: 'ongoing' })
    ).toBe('~2025-09-01 → ongoing')
  })
  it('exact to exact', () => {
    expect(
      formatDateRange({
        startDate: '2024-01-01', startQualifier: 'exact',
        stopDate: '2024-02-07', stopQualifier: 'exact',
      })
    ).toBe('2024-01-01 → 2024-02-07')
  })
  it('before start to exact stop', () => {
    expect(
      formatDateRange({
        startQualifier: 'before', stopDate: '2024-03-01', stopQualifier: 'exact',
      })
    ).toBe('before → 2024-03-01')
  })
  it('unknown start to ongoing collapses to a single label', () => {
    expect(
      formatDateRange({ startQualifier: 'unknown', stopQualifier: 'ongoing' })
    ).toBe('unknown → ongoing')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run tests/utils.dateRange.spec.js`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement the formatter**

```javascript
// frontend/src/utils/dateRange.js
// Renders a temporal endpoint or full range from a curated row's
// {start,stop}{Date,Qualifier}. Pure + framework-free so it is unit-testable.

export function formatEndpoint(date, qualifier) {
  switch (qualifier) {
    case 'ongoing':
      return 'ongoing'
    case 'unknown':
      return date ? String(date) : 'unknown'
    case 'before':
      return date ? `before ${date}` : 'before'
    case 'estimated':
      return date ? `~${date}` : 'estimated'
    case 'exact':
    default:
      return date ? String(date) : (qualifier || '')
  }
}

export function formatDateRange(row = {}) {
  const start = formatEndpoint(row.startDate, row.startQualifier || 'unknown')
  const stop = formatEndpoint(row.stopDate, row.stopQualifier || 'unknown')
  return `${start} → ${stop}`
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run tests/utils.dateRange.spec.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/utils/dateRange.js frontend/tests/utils.dateRange.spec.js
git commit -m "feat(ui): date-range formatter for curated temporal bounds"
```

---

## Task 11: API client methods (frontend)

**Files:**
- Modify: `frontend/src/api/client.js`

- [ ] **Step 1: Add the five calls (mirror the existing `reviewFact` style)**

Append to `frontend/src/api/client.js`:

```javascript
export const getCurated = (id, type, signal) =>
  api.get(`/api/patient/${encodeURIComponent(id)}/curated`, { params: { type }, signal }).then(data)

export const createCurated = (id, body) =>
  api.post(`/api/patient/${encodeURIComponent(id)}/curated`, body).then(data)

export const updateCurated = (cid, body) =>
  api.patch(`/api/curated/${encodeURIComponent(cid)}`, body).then(data)

export const deleteCurated = (cid) =>
  api.delete(`/api/curated/${encodeURIComponent(cid)}`).then(data)

export const restoreCurated = (cid) =>
  api.post(`/api/curated/${encodeURIComponent(cid)}/restore`).then(data)
```

> Confirm `data` is the existing response-unwrap helper in this file (the explore map shows `.then(data)` is the established pattern). If it's named differently, match it.

- [ ] **Step 2: Verify the build still type-checks / imports resolve**

Run: `cd frontend && npx vitest run tests/utils.dateRange.spec.js`
Expected: PASS (no import errors introduced).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/client.js
git commit -m "feat(ui): curated API client methods"
```

---

## Task 12: `CuratedPanel.vue` component

A panel listing active curated items of one type with inline edit, add, delete/restore. Reuses `SectionHeader`, `REVIEW_META`, the `ui` store, and the dialog/form pattern from `PricingTable.vue`.

**Files:**
- Create: `frontend/src/components/CuratedPanel.vue`
- Test: `frontend/tests/CuratedPanel.spec.js`

- [ ] **Step 1: Write the failing test**

```javascript
// frontend/tests/CuratedPanel.spec.js
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../src/api/client.js', () => ({
  getCurated: vi.fn(),
  createCurated: vi.fn(),
  updateCurated: vi.fn(),
  deleteCurated: vi.fn(),
  restoreCurated: vi.fn(),
}))

const ROW = {
  id: 'cur1', type: 'medication', displayValue: 'Paclitaxel',
  startDate: '2026-01-10', startQualifier: 'exact', stopDate: null,
  stopQualifier: 'ongoing', scheduleText: 'q3wk x 6 cycles', status: 'start',
  recordState: 'active', reviewStatus: 'ai_suggested', humanEditedFields: [],
}

describe('CuratedPanel', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('lists items from the API with a rendered date range', async () => {
    const { getCurated } = await import('../src/api/client.js')
    getCurated.mockResolvedValue({ items: [ROW] })
    const Panel = (await import('../src/components/CuratedPanel.vue')).default
    const w = mount(Panel, {
      props: { patientId: 'HN1', type: 'medication', title: 'Medications' },
      global: { plugins: [createPinia()] },
    })
    await flushPromises()
    expect(getCurated).toHaveBeenCalledWith('HN1', 'medication', undefined)
    expect(w.text()).toContain('Paclitaxel')
    expect(w.text()).toContain('2026-01-10 → ongoing')
  })

  it('calls deleteCurated when a row is removed', async () => {
    const { getCurated, deleteCurated } = await import('../src/api/client.js')
    getCurated.mockResolvedValue({ items: [ROW] })
    deleteCurated.mockResolvedValue({ id: 'cur1', recordState: 'dismissed' })
    const Panel = (await import('../src/components/CuratedPanel.vue')).default
    const w = mount(Panel, {
      props: { patientId: 'HN1', type: 'medication', title: 'Medications' },
      global: { plugins: [createPinia()] },
    })
    await flushPromises()
    await w.find('[data-test="curated-delete"]').trigger('click')
    await flushPromises()
    expect(deleteCurated).toHaveBeenCalledWith('cur1')
  })

  it('submits a manual insert', async () => {
    const { getCurated, createCurated } = await import('../src/api/client.js')
    getCurated.mockResolvedValue({ items: [] })
    createCurated.mockResolvedValue({ ...ROW, origin: 'human' })
    const Panel = (await import('../src/components/CuratedPanel.vue')).default
    const w = mount(Panel, {
      props: { patientId: 'HN1', type: 'medication', title: 'Medications' },
      global: { plugins: [createPinia()] },
    })
    await flushPromises()
    await w.find('[data-test="curated-add"]').trigger('click')
    w.vm.form.displayValue = 'Warfarin'
    await w.vm.save()
    await flushPromises()
    expect(createCurated).toHaveBeenCalledWith('HN1', expect.objectContaining({
      type: 'medication', displayValue: 'Warfarin',
    }))
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run tests/CuratedPanel.spec.js`
Expected: FAIL (component not found).

- [ ] **Step 3: Implement the component**

```vue
<!-- frontend/src/components/CuratedPanel.vue -->
<template>
  <v-card class="fill-height">
    <SectionHeader :title="title" :icon="icon" :color="color">
      <template #append>
        <v-btn size="small" variant="text" prepend-icon="mdi-plus"
               data-test="curated-add" @click="openAdd">Add</v-btn>
      </template>
    </SectionHeader>
    <v-divider />

    <v-list v-if="items.length" density="compact">
      <v-list-item v-for="it in items" :key="it.id">
        <v-list-item-title>{{ it.displayValue }}</v-list-item-title>
        <v-list-item-subtitle>
          {{ formatDateRange(it) }}
          <span v-if="it.scheduleText"> · {{ it.scheduleText }}</span>
        </v-list-item-subtitle>
        <template #append>
          <v-chip size="x-small" variant="tonal"
                  :color="meta(it.reviewStatus).color"
                  :prepend-icon="meta(it.reviewStatus).icon">
            {{ meta(it.reviewStatus).label }}
          </v-chip>
          <v-btn icon="mdi-pencil" size="x-small" variant="text"
                 data-test="curated-edit" aria-label="Edit" @click="openEdit(it)" />
          <v-btn icon="mdi-delete" size="x-small" variant="text" color="error"
                 data-test="curated-delete" aria-label="Delete" @click="remove(it)" />
        </template>
      </v-list-item>
    </v-list>
    <EmptyState v-else icon="mdi-tray" :title="`No ${title.toLowerCase()} yet`" />

    <v-dialog v-model="dialog" max-width="520">
      <v-card>
        <SectionHeader :title="isNew ? `Add ${singular}` : `Edit ${singular}`" icon="mdi-pencil-outline" />
        <v-divider />
        <v-card-text>
          <v-text-field v-model="form.displayValue" label="Name / value" />
          <div class="d-flex ga-2">
            <v-text-field v-model="form.startDate" label="Start (YYYY-MM-DD)" />
            <v-select v-model="form.startQualifier" :items="START_QUALIFIERS" label="Start qualifier" />
          </div>
          <div class="d-flex ga-2">
            <v-text-field v-model="form.stopDate" label="Stop (YYYY-MM-DD)" />
            <v-select v-model="form.stopQualifier" :items="STOP_QUALIFIERS" label="Stop qualifier" />
          </div>
          <v-text-field v-model="form.scheduleText" label="Schedule (free text)" />
          <v-text-field v-model="form.status" :label="type === 'medication' ? 'Action' : 'Status'" />
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="dialog = false">Cancel</v-btn>
          <v-btn color="primary" @click="save">Save</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </v-card>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import SectionHeader from './SectionHeader.vue'
import EmptyState from './EmptyState.vue'
import { REVIEW_META, FACT_TYPE_META } from '../constants/clinical.js'
import { formatDateRange } from '../utils/dateRange.js'
import { useUiStore } from '../stores/ui.js'
import {
  getCurated, createCurated, updateCurated, deleteCurated,
} from '../api/client.js'

const props = defineProps({
  patientId: { type: String, required: true },
  type: { type: String, required: true },   // 'condition' | 'medication'
  title: { type: String, required: true },
})

const START_QUALIFIERS = ['exact', 'estimated', 'before', 'unknown']
const STOP_QUALIFIERS = ['exact', 'estimated', 'ongoing', 'unknown']

const ui = useUiStore()
const items = ref([])
const dialog = ref(false)
const isNew = ref(true)
const editingId = ref(null)
const form = reactive(emptyForm())
const meta = (s) => REVIEW_META[s] || { color: 'grey', icon: 'mdi-help-circle-outline', label: s || 'unknown' }
const icon = FACT_TYPE_META[props.type]?.icon || 'mdi-clipboard-text'
const color = FACT_TYPE_META[props.type]?.color || 'primary'
const singular = props.type === 'medication' ? 'medication' : 'problem'

function emptyForm() {
  return {
    displayValue: '', startDate: '', startQualifier: 'unknown',
    stopDate: '', stopQualifier: 'ongoing', scheduleText: '', status: '',
  }
}

async function load() {
  const res = await getCurated(props.patientId, props.type, undefined)
  items.value = res.items || []
}

function openAdd() {
  Object.assign(form, emptyForm())
  isNew.value = true
  editingId.value = null
  dialog.value = true
}

function openEdit(it) {
  Object.assign(form, {
    displayValue: it.displayValue, startDate: it.startDate || '',
    startQualifier: it.startQualifier, stopDate: it.stopDate || '',
    stopQualifier: it.stopQualifier, scheduleText: it.scheduleText || '',
    status: it.status || '',
  })
  isNew.value = false
  editingId.value = it.id
  dialog.value = true
}

function payload() {
  return {
    type: props.type,
    displayValue: form.displayValue,
    startDate: form.startDate || null,
    startQualifier: form.startQualifier,
    stopDate: form.stopDate || null,
    stopQualifier: form.stopQualifier,
    scheduleText: form.scheduleText || null,
    status: form.status || null,
  }
}

async function save() {
  if (!form.displayValue) { ui.error('Name is required'); return }
  try {
    if (isNew.value) {
      await createCurated(props.patientId, payload())
      ui.success('Added')
    } else {
      const { type, ...patch } = payload()
      await updateCurated(editingId.value, patch)
      ui.success('Saved')
    }
    dialog.value = false
    await load()
  } catch {
    ui.error('Save failed')
  }
}

async function remove(it) {
  try {
    await deleteCurated(it.id)
    await load()
    ui.success('Removed')
  } catch {
    ui.error('Delete failed')
  }
}

defineExpose({ form, save })
onMounted(load)
</script>
```

> `defineExpose({ form, save })` is what lets the unit test drive `w.vm.form` / `w.vm.save()`. Keep it. `SectionHeader`'s `#append` slot is assumed from the explore map (it renders a title/icon header); if `SectionHeader` doesn't expose an append slot, place the Add button in a sibling element instead and adjust the test's `[data-test="curated-add"]` target accordingly.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run tests/CuratedPanel.spec.js`
Expected: PASS (3 tests). If Vuetify components need stubbing for mount, follow the pattern in `tests/JobsPopover.spec.js` (global stubs) — reuse the same `stubs` object the existing component tests use.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/CuratedPanel.vue frontend/tests/CuratedPanel.spec.js
git commit -m "feat(ui): CuratedPanel with inline edit/add/delete for temporal facts"
```

---

## Task 13: Mount the panels on the patient page

**Files:**
- Modify: `frontend/src/views/PatientDetail.vue` (Overview tab — alongside the existing `FactCard` "Active problems" / medications cards)

- [ ] **Step 1: Import and place the panels**

In `PatientDetail.vue`'s `<script setup>`, add:

```javascript
import CuratedPanel from '../components/CuratedPanel.vue'
```

In the Overview tab template, add a row hosting the two panels (use the existing `v-row`/`v-col cols="12" md="6"` grid the Overview already uses):

```vue
<v-row>
  <v-col cols="12" md="6">
    <CuratedPanel :patient-id="props.id" type="condition" title="Problems" />
  </v-col>
  <v-col cols="12" md="6">
    <CuratedPanel :patient-id="props.id" type="medication" title="Medications" />
  </v-col>
</v-row>
```

> Keep the existing AI `FactCard`s (they show the raw evidence trail). The curated panels are the human-truth view above/beside them — place them at the top of the Overview tab so the curated layer reads as primary. Confirm `props.id` is the patient id prop name in this view (the router passes `props: true` with `:id`).

- [ ] **Step 2: Run the frontend test suite**

Run: `cd frontend && npx vitest run`
Expected: PASS (all green, including the new specs).

- [ ] **Step 3: Build to confirm no template errors**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/views/PatientDetail.vue
git commit -m "feat(ui): mount Problems & Medications curated panels on patient page"
```

---

## Task 14: Final verification & PR

- [ ] **Step 1: Full backend + frontend suites**

Run: `cd backend && python -m pytest tests/ -q`
Run: `cd frontend && npx vitest run`
Expected: both green.

- [ ] **Step 2: Manual end-to-end (optional, requires the stack)**

Apply migration 006, ingest a note with a dated medication and a chronic problem, open the patient page, confirm: panels render with date ranges; edit a start date and confirm the chip flips to "Confirmed" and the edit survives re-ingesting the same note; delete an item and confirm re-ingesting resurfaces it as "AI suggested" with the prior date edit intact.

- [ ] **Step 3: Open the PR**

```bash
git push -u origin feat/temporal-curated-facts
gh pr create --base main --title "feat: temporal problems & medications with human curation" --body "$(cat <<'EOF'
Implements docs/superpowers/specs/2026-06-04-temporal-curated-facts-design.md.

## What
- New `curated_facts` longitudinal layer (migration 006) — human-truth view that always wins over AI.
- Extractor now captures medication dates/qualifiers/schedule and problem onset/resolved qualifiers.
- Reconcile step upserts AI mentions by identity, never clobbering human-edited fields; soft-deleted items resurface on re-mention preserving prior date edits.
- Curated CRUD API (list/insert/patch/delete/restore) with Neo4j propagation.
- Frontend Problems & Medications panels with inline edit/add/delete and a tested date-range formatter.

## Test plan
- [ ] Backend: `cd backend && python -m pytest tests/ -q` green
- [ ] Frontend: `cd frontend && npx vitest run` green
- [ ] Migration 006 is idempotent (re-applies cleanly)
- [ ] Reconcile fills empty fields from AI but never overwrites human edits
- [ ] Soft-delete hides a row; re-mention resurfaces it as ai_suggested with human date edits preserved
- [ ] Manual insert / edit / delete / restore endpoints work (incl. 404s)
- [ ] Date-range formatter renders every open/estimated/exact/ongoing combination
- [ ] Panels render on the patient page and edits flip the review chip to Confirmed

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review (author checklist — done at write time)

**Spec coverage:**
- Storage layers (facts immutable, curated_facts new, Neo4j override) → Tasks 1, 6, 7.
- Identity / normalized_key → Task 4 (`normalized_key`), used in 6/7.
- Data model columns incl. open/uncertain bounds → Task 1 + `normalize_bounds` (Task 4).
- AI seeding: schema additions → Task 2; prompt → Task 3; reconcile (new/active/dismissed cases) → Tasks 4 (merge) + 6 (DB) + 8 (wiring); Neo4j push → Task 6.
- API (GET/POST/PATCH/DELETE/restore + Neo4j propagation + 404s) → Task 7.
- Frontend panels, inline edit, add, delete/restore, date-range formatter → Tasks 10–13.
- Error handling: per-item reconcile isolation (Task 6), ongoing-clears-stop normalization (Tasks 4/7), 404s (Task 7).
- Testing matrix (reconcile merge, qualifier round-trip, resurface, CRUD, migration idempotency, formatter combos) → Tasks 1,2,4,6,7,10,12.

**Type consistency:** curated column names are identical across migration (Task 1), pure layer (Task 4), DB SQL (Task 6), API schema aliases (Task 7), and FakeStore (Task 5). camelCase API field names match between `CuratedItem`/`CuratedCreate`/`CuratedPatch` (Task 7), the client (Task 11), and the component (Task 12). `merge_curated`/`normalize_bounds`/`normalized_key` signatures are used consistently.

**Known follow-ups flagged inline (not placeholders):** confirm the extraction prompt constant name (Task 3), the `run_cypher` import path that `stub_neo4j` patches (Task 6), `SectionHeader`'s append slot (Task 12), and the `_persist_post_extraction` sync/async shape (Task 8) — each has a written fallback so the task is executable either way.
