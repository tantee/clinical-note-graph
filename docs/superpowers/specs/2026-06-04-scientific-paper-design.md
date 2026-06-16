# Scientific Paper Design — Clinical Note Graph

**Date:** 2026-06-04
**Status:** Approved outline; drafting section by section.

## Decisions

- **Venue/style:** Medical informatics journal (JAMIA / JMIR Medical Informatics style), structured IMRaD.
- **Evaluation:** Real eval data to be added by author. Public benchmark dataset. Four arms: coding accuracy, extraction quality, human review/time-savings, cost & latency.
- **Focus:** Full pipeline (ingestion → LLM extraction → graph → coding) with **medical coding as the climax**.
- **Deliverable:** Outline first (this doc), then full manuscript drafted **section by section** with author feedback between sections.
- **Prompts:** verbatim in an appendix; Methods body summarizes.

## Working titles

1. *From Clinical Narrative to Coded Knowledge Graph: A Schema-Constrained LLM Pipeline for Automated Medical Coding*
2. *LLM-Assisted Construction of a Longitudinal Clinical Knowledge Graph with Human-in-the-Loop Medical Coding*

## Structure

- **Structured abstract** (Background / Objective / Methods / Results / Conclusions), ~250–300 words, written last.
- **1. Introduction** — coding burden + documentation overload; gap (trustworthy, terminology-grounded, auditable structured data; LLM hallucination/abstention failure modes); contributions (schema-constrained end-to-end pipeline; non-abstaining-but-calibrated coding policy; dual relational+graph longitudinal model with provenance; multi-dimensional benchmark evaluation); objective statement.
- **2. Methods**
  1. System overview (architecture figure; five-stage pipeline; dual-store rationale)
  2. Data ingestion & normalization (text/JSON/FHIR; idempotency; async jobs; de-identification boundary)
  3. Schema-constrained fact extraction (prompt; strict Pydantic schema; evidence grounding; fail-closed validation)
  4. **Medical coding pipeline (climax)** (multi-terminology ICD-10/SNOMED/LOINC/RxNorm; "always return a code" policy; confidence calibration; deterministic single-retry; primary/secondary/complication/comorbidity; rationale + warnings)
  5. Deduplication & longitudinal merge (normalized-code/value key; patient-level collapse, encounter-level preservation; contradiction surfacing)
  6. Knowledge graph construction (node/edge taxonomy; LLM-declared relationships; provenance edges; node guard)
  7. Human-in-the-loop review (status lifecycle; AI-as-suggestion stance; audit trail)
  8. Implementation (stack; provider abstraction; reproducibility)
  9. Evaluation design (public benchmark; 9a coding accuracy, 9b extraction quality, 9c human review/time, 9d cost & latency)
- **3. Results** — mirrors 9a–9d, placeholder tables/figures with `[INSERT: …]` markers.
- **4. Discussion** — principal findings; prior-work comparison; limitations; future work.
- **5. Conclusion**
- **Back matter** — references; Appendix (verbatim prompts); data/code availability; ethics statement.

## Source-of-truth code references (for accuracy)

- Ingestion: `backend/app/routers/emr.py`, `backend/app/services/ingest.py`, `backend/app/services/fhir_adapter.py`
- Prompts: `backend/app/prompts/templates.py`
- Coding: `backend/app/services/coding.py`
- Extraction schema: `backend/app/schemas/extraction.py`
- Graph: `backend/app/services/graph_updater.py`; node guard `backend/app/routers/patient.py` (`_MAX_NODES_PRE_DEDUPE = 500`)
- Dedup: `backend/app/services/patient_facts.py`
- DB schema: `backend/db/init/001_schema.sql`

## Manuscript location

`docs/paper/manuscript.md`
