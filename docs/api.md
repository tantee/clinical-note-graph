# API surface and data model

The HTTP API, the Neo4j graph shape, and the markdown vault layout — everything an integrator needs to know to talk to a running stack.

## HTTP API

| Verb | Path | Purpose |
|---|---|---|
| POST | `/api/emr/ingest` | Accept text / JSON / FHIR EMR document. Idempotent on `(patientId, source.documentId, source.version)`. Pass `?async=true` to enqueue. |
| GET  | `/api/jobs/{jobId}` | Background job status |
| GET  | `/api/patients` | Search/list patients |
| GET  | `/api/patient/{id}` | Aggregated structured facts |
| GET  | `/api/patient/{id}/timeline` | Encounters timeline (single SQL round-trip with counts) |
| GET  | `/api/patient/{id}/encounter/{eid}/documents` | Documents for one encounter |
| GET  | `/api/patient/{id}/document/{docId}?includeRaw=false` | Raw EMR + facts + latest AI output for one document |
| GET  | `/api/patient/{id}/graph` | Graph (nodes + edges) |
| GET  | `/api/patient/{id}/notes` · `/note?path=…` | List + read vault files; backlinks included |
| POST | `/api/patient/{id}/summary` | brief / detailed / discharge / problem_oriented / timeline / coding_support |
| POST | `/api/patient/{id}/coding/suggest` | ICD-10 / SNOMED CT / LOINC / RxNorm candidates |
| PATCH| `/api/facts/{factId}/review?status=` | Set `ai_suggested` / `human_confirmed` / `rejected` |
| GET  | `/api/search?q=…&patientId=…` | Vector search across facts + notes |
| POST | `/api/export` | summary · coding · graph · markdown_vault (zip) · fhir_bundle · custom (uses export profile) |
| GET/PATCH | `/api/config` | Read effective settings (masked secrets), patch overrides |
| GET/PUT/DELETE | `/api/config/export-profiles[/{id}]` | Manage export profiles |
| GET  | `/api/jobs?status=&type=&limit=&offset=` | List background jobs (queue) |
| POST | `/api/jobs/{id}/requeue`                 | Reset a failed job to pending |
| GET  | `/api/debug/summary?start=&end=`         | KPI totals over a range |
| GET  | `/api/debug/by-model?start=&end=`        | Per-model breakdown |
| GET  | `/api/debug/by-day?start=&end=`          | Stacked-bar dataset |
| GET  | `/api/debug/ai-calls?…`                  | AI call log (filterable) |
| GET  | `/api/debug/ai-calls/{id}`               | Single call detail |
| GET  | `/api/debug/ai-calls.csv?…`              | Streamed CSV export |
| GET  | `/api/config/pricing`                    | List model rates |
| PUT  | `/api/config/pricing/{model}`            | Upsert one rate |
| DEL  | `/api/config/pricing/{model}`            | Delete a rate |
| POST | `/api/config/pricing/refresh-openrouter` | Refresh rates from OpenRouter |

Full schema at `http://localhost:8000/docs` (OpenAPI).

Examples:
- Curl walkthrough: [`examples/ingest.sh`](../examples/ingest.sh)
- Cypher queries: [`examples/graph-query.cypher`](../examples/graph-query.cypher)
- Coding response: [`examples/coding-response.json`](../examples/coding-response.json)
- Generated vault: [`examples/example-vault/`](../examples/example-vault/)

## Graph model

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
(Document)-[:EXTRACTED {evidence,confidence}]->(Condition|Medication|Observation|…)
```

All upserts are **longitudinal** — new documents add facts and link them; they never delete prior facts. Contradictions surface as `warnings[]` in `ClinicalExtractionResult` and are visible in the UI.

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

Every file has YAML frontmatter, `[[wikilinks]]`, an Evidence section quoting the raw EMR, a Timeline, and `updatedAt`. Compatible with vanilla Obsidian — mount the volume into your Obsidian vault folder for a side-by-side workflow.
