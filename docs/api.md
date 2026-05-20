# API surface and data model

> **Audience:** integrators wiring an EMR feed into this stack, or building UI / scripts on top of it.

The HTTP API, the Neo4j graph shape, and the markdown vault layout — everything you need to talk to a running stack.

The full OpenAPI schema is at `http://localhost:8000/docs` (Swagger UI) and `/redoc`. The table below groups endpoints by what they touch.

---

## HTTP API

### Ingest

| Verb | Path | Purpose |
|---|---|---|
| POST | `/api/emr/ingest` | Accept text / JSON / FHIR EMR document. Idempotent on `(patientId, source.documentId, source.version)`. Pass `?async=true` (the default) to enqueue; `?async=false` for inline. |
| GET  | `/api/jobs` | List background jobs (filter by `?status=&type=&limit=&offset=`). |
| GET  | `/api/jobs/{jobId}` | Background job status + per-stage progress. |
| POST | `/api/jobs/{jobId}/requeue` | Reset a failed job to pending. |

### Patient lookup

| Verb | Path | Purpose |
|---|---|---|
| GET  | `/api/patients` | Search/list patients. |
| GET  | `/api/patient/{id}` | Aggregated structured facts for one patient. |
| GET  | `/api/patient/{id}/timeline` | Encounters timeline (single SQL round-trip with counts). |
| GET  | `/api/patient/{id}/encounters` | Encounters list for the encounters tab. |
| GET  | `/api/patient/{id}/encounter/{encId}/documents` | Documents for one encounter. |
| GET  | `/api/patient/{id}/document/{docId}?includeRaw=false` | Raw EMR + facts + latest AI output for one document. |
| GET  | `/api/patient/{id}/graph` | Patient knowledge graph (nodes + edges). |
| GET  | `/api/patient/{id}/notes` | List the vault files for one patient (with backlinks). |
| GET  | `/api/patient/{id}/note?path=…` | Read one vault file by relative path. |

### AI: summary / coding / RAG

| Verb | Path | Purpose |
|---|---|---|
| POST | `/api/patient/{id}/summary` | Generate a patient-level summary. Body: `summary_type` ∈ `brief / detailed / discharge / problem_oriented / timeline / coding_support`. |
| GET  | `/api/patient/{id}/summary/latest` | Read the most recent saved summary without re-running the AI. |
| POST | `/api/patient/{id}/coding/suggest` | Generate ICD-10 / SNOMED CT / LOINC / RxNorm candidates for the patient. |
| GET  | `/api/patient/{id}/coding/latest` | Read the most recent saved coding result. |
| POST | `/api/patient/{id}/encounter/{encId}/summary` | Encounter-scoped summary (only that encounter's facts feed the prompt). |
| GET  | `/api/patient/{id}/encounter/{encId}/summary/latest` | Latest encounter-scoped summary. |
| POST | `/api/patient/{id}/encounter/{encId}/coding/suggest` | Encounter-scoped coding suggestions. |
| GET  | `/api/patient/{id}/encounter/{encId}/coding/latest` | Latest encounter-scoped coding. |
| POST | `/api/rag/ask` | Retrieval-augmented Q&A over one patient's notes (vector recall + LLM answer with citations). |

### Search / facts / export

| Verb | Path | Purpose |
|---|---|---|
| GET  | `/api/search?q=…&patientId=…` | Vector search across facts + notes for a patient. |
| GET  | `/api/search/patients?q=…` | Vector search across the patient population (powers the global search bar). |
| PATCH| `/api/facts/{factId}/review?status=` | Set `ai_suggested / human_confirmed / rejected`. |
| POST | `/api/export` | Export `summary` · `coding` · `graph` · `markdown_vault` (zip) · `fhir_bundle` · `custom`. Every response also carries `vaultPath` — the bundle is mirrored to `patients/<HN>/exports/<name>-<ts>.<ext>`. See [Configuring export profiles](features.md#configuring-export-profiles) for the `custom` config shape. |

### Configuration

| Verb | Path | Purpose |
|---|---|---|
| GET    | `/api/config` | Read effective settings (secrets masked). |
| PATCH  | `/api/config` | Patch overrides (stored in `app_config`, merged on every read — no restart). |
| GET    | `/api/config/export-profiles` | List export profiles. |
| PUT    | `/api/config/export-profiles/{id}` | Upsert an export profile. Body: `{profileId, name, config}`. Config keys: `fields[]`, `format` (`json`\|`markdown`), `includeEvidence`. See [Configuring export profiles](features.md#configuring-export-profiles). |
| DELETE | `/api/config/export-profiles/{id}` | Remove an export profile. |
| GET    | `/api/config/pricing` | List model pricing rates. |
| PUT    | `/api/config/pricing/{model}` | Upsert one model rate. |
| DELETE | `/api/config/pricing/{model}` | Delete one model rate. |
| POST   | `/api/config/pricing/refresh-openrouter` | Refresh rates from OpenRouter's public model list. |

### Debug

All `/api/debug/*` endpoints sit behind the same `X-API-Key` middleware as `/api/config` and `/api/emr`.

| Verb | Path | Purpose |
|---|---|---|
| GET | `/api/debug/summary?start=&end=` | KPI totals over a range. |
| GET | `/api/debug/by-model?start=&end=` | Per-model spend / latency breakdown. |
| GET | `/api/debug/by-day?start=&end=` | Stacked-bar dataset. |
| GET | `/api/debug/ai-calls?…` | AI call log (filterable). |
| GET | `/api/debug/ai-calls/{id}` | Single AI call detail. |
| GET | `/api/debug/ai-calls.csv?…` | Streamed CSV export of the AI call log. |

### Health

| Verb | Path | Purpose |
|---|---|---|
| GET | `/health` | 200 whenever the backend process is up. |
| GET | `/ready` | 200 only when Postgres responds. |

---

## Examples

- Curl walkthrough: [`examples/ingest.sh`](../examples/ingest.sh)
- Cypher queries: [`examples/graph-query.cypher`](../examples/graph-query.cypher)
- Coding response shape: [`examples/coding-response.json`](../examples/coding-response.json)
- Generated vault layout: [`examples/example-vault/`](../examples/example-vault/)

---

## Graph model (Neo4j)

```
(Patient)-[:HAS_ENCOUNTER]->(Encounter)
(Encounter)-[:HAS_DOCUMENT]->(Document)
(Encounter)-[:MENTIONS]->(Condition)
(Encounter)-[:PRESCRIBED]->(Medication)
(Encounter)-[:HAS_OBSERVATION]->(Observation)
(Encounter)-[:HAS_PLAN]->(Plan)
(Encounter)-[:PERFORMED]->(Procedure)
(Patient)-[:HAS_ALLERGY]->(Allergy)
(Medication)-[:TREATS]->(Condition)
(Plan)-[:ADDRESSES]->(Condition)
(CodingCandidate)-[:CODES]->(Condition)
(Document)-[:EXTRACTED {evidence, confidence}]->(Condition|Medication|Observation|…)
```

All upserts are **longitudinal** — new documents add facts and link them; they never delete prior facts. Contradictions surface as `warnings[]` in `ClinicalExtractionResult` and are visible in the UI.

---

## Markdown vault layout

```
/data/vault/patients/{patientId}/
   index.md
   visits/{date}-{encounterType}.md
   problems/{slug}.md
   medications/{slug}.md
   labs/{slug}.md
   sources/{documentId}.md
```

Every file has YAML frontmatter, `[[wikilinks]]`, an Evidence section quoting the raw EMR, a Timeline, and `updatedAt`. The vault is compatible with vanilla Obsidian — mount the volume into your Obsidian vault folder for a side-by-side workflow.
