# Glossary

> **Audience:** anyone bumping into a term used elsewhere in the docs without definition.

Defined in the order most readers will encounter them. Cross-linked from the doc pages where each term first appears.

---

**Patient (`Patient`).** The longitudinal record of one person across all encounters. Identified by `patient_id` (also called HN — see below). Stored as a row in the Postgres `patients` table and a node in the Neo4j graph.

**HN (Hospital Number, `patient_id`).** The clinical identifier used by the hospital to refer to a patient. In this codebase it doubles as the primary key — the value you pass to `/api/patient/{id}/...`. Format is free-form (`HN-DEMO-1`, `HN123456`, etc.); the sample data uses `HN-XXXX`.

**Encounter (`Encounter`).** One clinical episode — a visit, an admission, an ED stay, etc. One patient has many encounters. Identified by `encounter_id`. Encounters carry a `received_at` timestamp and an `encounter_type` (`visit / admission / ed / discharge / followup`).

**Document (`Document`).** One inbound EMR — a note, an FHIR bundle, a discharge summary. Documents belong to encounters. The verbatim text is stored in Postgres for audit; the structured facts extracted from it live in their own tables and in the graph.

**Fact.** A single structured datum extracted from a document — a condition, a medication, an observation, a plan, a procedure, an allergy, a diagnosis, or a coding candidate. Every fact carries:

- `evidenceText` — the literal span from the source document that justifies it.
- `confidence` — float in [0, 1] from the AI.
- `reviewStatus` — `ai_suggested` (default) / `human_confirmed` / `rejected`.

A clinician confirms or rejects facts via `PATCH /api/facts/{factId}/review`.

**Summary.** AI-generated prose describing the patient. The `summary_type` controls the angle:

- `brief` — one-paragraph TL;DR.
- `detailed` — full clinical synthesis.
- `discharge` — discharge-summary shape.
- `problem_oriented` — organised by active problem.
- `timeline` — chronological narrative.
- `coding_support` — for the coder, lists supporting evidence for each diagnosis.

Summaries can be patient-level (`/api/patient/{id}/summary`) or encounter-level (`/api/patient/{id}/encounter/{eid}/summary`).

**Coding suggestion.** Candidate ICD-10 / SNOMED CT / LOINC / RxNorm codes for the patient (or encounter), with rationale and confidence. Always advisory — a coder must review.

**RAG (Retrieval-Augmented Generation).** The Q&A flow on the Vector demo page. Given a patient and a question, the backend does vector recall over that patient's notes, then asks the LLM to answer using only the retrieved excerpts, with citations.

**De-identification / redactor.** The pipeline in `backend/app/services/deidentify.py` that strips PHI from every outbound AI payload. Three modes — `off / regex_only / safe_harbor`. See [docs/compliance.md → De-identification](compliance.md#de-identification).

**Pseudonym (`PATIENT-A1`, `PROVIDER-A1`, `HN-A1`).** The opaque token the redactor substitutes in place of a name / HN / provider name before sending the prompt outbound. Deterministic per-request — the same name → the same token throughout one HTTP call.

**Safe Harbor 18.** The 18 categories of identifier HIPAA §164.514(b)(2) requires removed for data to be considered de-identified — names, geographic subdivisions smaller than state, dates more specific than year, phone/fax, email, SSN, MRN, plan beneficiary, account, license, vehicle, device, URL, IP, biometric, photo, etc.

**PHI (Protected Health Information).** Under HIPAA, individually identifiable health information held by a covered entity. The redactor's job is to ensure the prompts that leave this system are not PHI.

**BAA (Business Associate Agreement).** The contract HIPAA requires between a covered entity and any third-party processor of PHI. Some AI providers offer one (OpenAI Enterprise, Anthropic Enterprise); most don't (OpenRouter, vanilla OpenAI tier).

**Vault.** The Obsidian-style markdown filesystem the backend writes to (`/data/vault`). Every patient gets a folder with `index.md`, per-visit notes, per-problem notes, etc. Read-only-from-outside by convention; the backend owns writes.

**Audit row.** A row in the `ai_outputs` table. One per AI call. Captures model, tokens, latency, cost, raw response, validity, and (post #11) the de-identification flag + per-category counts.

**Job.** A queued background task — most commonly an ingest. Lives in the `jobs` table with status `pending / running / completed / failed` and per-stage progress in `progress` JSONB.

**Effective settings.** What the backend actually uses for the current request: defaults from `config.py`, merged with `app_config` overrides (DB-backed, hot-reloadable). Visible at `GET /api/config` with secrets masked.

**Mock provider.** `AI_PROVIDER=mock` — a deterministic keyword-based extractor that never makes an outbound call. Default for `cp .env.example .env`. Safe for any data because nothing leaves the host.

**OpenAI-compatible endpoint.** Any service that speaks the OpenAI Chat Completions / Embeddings JSON shape. Includes OpenAI itself, OpenRouter, Groq, Azure OpenAI, DeepSeek, vLLM, Ollama, LM Studio. All work via `AI_PROVIDER=openai` with the appropriate `AI_BASE_URL` — see [docs/ai-providers.md](ai-providers.md).
