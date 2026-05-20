# Vector DB demo page: RAG + patient search

**Status:** Design approved — awaiting final user review before plan write-up.
**Owner:** —
**Created:** 2026-05-20
**Related issues:** (to be created in GitHub before implementation)
**Depends on:** main (PR #4 merged); does NOT depend on PR #7 (graph scope selector).

---

## 1. Problem

The project already populates the pgvector `embeddings` table at ingest time (per fact, per markdown note — typically ~40 embeddings per encounter). Two clinically useful capabilities ride on top of those embeddings, but neither is exposed in the UI:

1. **RAG (retrieval-augmented Q&A)** over a single patient's notes — "what was the discharge plan for this admission?" answered from cited excerpts.
2. **Patient discovery by vector search** — free-text query → ranked patient list ("find patients with uncontrolled diabetes on metformin").

This design adds both as a demo/showcase page at `/vector-demo` for stakeholder demos, plus a small clinical search affordance in the main app bar.

## 2. Non-goals

- A clinician-facing RAG flow in the main app. The RAG capability lives on the demo page only for v1.
- A new schema or migration. The existing `embeddings` table is sufficient.
- Auth / row-level security. The prototype has none; all patients searchable by anyone.
- Cross-patient RAG ("ask one question across N patients"). Patient-scoped RAG only.
- LLM-side reranking or hybrid keyword+vector search. Pure cosine similarity.
- Streaming responses. One-shot HTTP request/response per question.

## 3. Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Frontend                                                                  │
│                                                                           │
│  App.vue (app-bar)                                                        │
│   + <PatientSearchInput> — debounced text input + dropdown, top 8 hits   │
│     - click result → /patients/:id                                        │
│                                                                           │
│  VectorDemoView.vue (NEW route /vector-demo)                              │
│   v-tabs: [ RAG (Q&A) | Patient search ]                                  │
│                                                                           │
│   <RagPanel>                                                              │
│     - patient v-autocomplete (re-uses /api/patients)                      │
│     - mode toggle: one_shot ↔ chat                                        │
│     - question input (Ctrl+Enter submits)                                 │
│     - chat history pane (only in chat mode)                               │
│     - answer card (markdown) + citation badges                            │
│     - "behind the scenes" pane: top-K chunks with scores                  │
│                                                                           │
│   <PatientSearchPanel>                                                    │
│     - text input + Enter to submit                                        │
│     - ranked result cards (HN, name, score, top 3 snippets)               │
│     - "behind the scenes" pane: model, latency, count                     │
│                                                                           │
│   <CitationBadge> (shared) — clickable [N] chip → opens source            │
│                                                                           │
├──────────────────────────────────────────────────────────────────────────┤
│ Backend                                                                   │
│                                                                           │
│  POST /api/rag/ask        → RagAskResponse  (LLM-composed answer)         │
│  GET  /api/search/patients → PatientSearchResponse (pure SQL+vector)      │
│                                                                           │
│  Service: backend/app/services/rag.py                                     │
│    - ask(req)                  — orchestrates embed → search → LLM        │
│    - search_patients(q, limit) — orchestrates embed → grouped SQL         │
│    - parse_cited_indices(md)   — regex on [N] markers                     │
│    - build_citations(chunks, answer) — flags which chunks were cited      │
│                                                                           │
│  Provider: backend/app/services/ai_provider.py                            │
│    + AIProvider.rag_ask(system, user, history) — new abstract method     │
│    + OpenAIProvider.rag_ask, MockProvider.rag_ask                         │
│    + new RAG_SYSTEM prompt in app/prompts/templates.py                    │
│                                                                           │
│  Helper: backend/app/services/embeddings.py                               │
│    + vector_search_with_scores(qvec, patient_id, top_k)                   │
│      — new function returning (content, ref_type, ref_id, score) tuples.  │
│      Existing vector_search(q, patient_id, limit) stays untouched.        │
│                                                                           │
│  Audit                                                                    │
│    - RAG calls write ai_outputs row with call_type='rag'                  │
│    - Embedding calls already write call_type='embed' (existing)           │
│    - Patient search: no LLM call beyond the one embed; no extra row       │
└──────────────────────────────────────────────────────────────────────────┘
```

## 4. Behavior

### 4.1 Demo page mode

- Default tab is RAG.
- Switching tabs preserves component state (chat history, last results).
- "Behind the scenes" pane shows every retrieved chunk and the embedding-model name. Unapologetically technical — the page is for demos.

### 4.2 RAG flow

1. User selects a patient (autocomplete on existing `/api/patients`).
2. User selects mode (one_shot is default).
3. User types question (`Ctrl+Enter` or Ask button submits).
4. Backend embeds the question, runs `vector_search_with_scores`, builds the numbered-excerpts prompt, calls the LLM (with optional chat history), returns answer + citations.
5. Frontend renders markdown answer; clickable `[N]` badges open the source note (or navigate to the patient page when the ref is a fact).
6. In chat mode, the question + answer are appended to a local history array. Subsequent submits include the trimmed history.

### 4.3 Patient search flow

1. User types a query in the Patient-search tab text input.
2. On Enter or Submit, backend embeds the query and runs a grouped-by-`patient_id` SQL using cosine similarity (`embedding <=> :qvec`).
3. Backend returns up to `limit` patients, each with its max similarity score and top-3 snippets (already-stored content from the embeddings table; ≤300 chars each).
4. Frontend renders cards. Clicking a card navigates to `/patients/:id`.

### 4.4 App-bar nav search flow

1. User types in the persistent text input in the app-bar.
2. Input is debounced 300ms; below 2 characters no fetch fires.
3. Same `/api/search/patients` endpoint as the demo tab, `limit=8`.
4. Dropdown shows results with HN · name · top-snippet preview · score.
5. Click result → router push to `/patients/:id`; dropdown closes.

## 5. API surface

### POST /api/rag/ask

```python
class RagAskMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str

class RagAskRequest(BaseModel):
    patientId: str = Field(..., min_length=1)
    question: str  = Field(..., min_length=1, max_length=2000)
    mode: Literal["one_shot", "chat"] = "one_shot"
    history: list[RagAskMessage] = Field(default_factory=list, max_length=20)
    topK: int = Field(default=8, ge=1, le=20)


class RagCitation(BaseModel):
    n: int
    refType: str   # 'note' | 'fact'
    refId: str
    content: str   # ≤ 300 chars
    score: float
    cited: bool


class RagAskResponse(BaseModel):
    patientId: str
    question: str
    answer: str            # markdown
    citations: list[RagCitation]
    modelUsed: str
    embeddingModel: str
    latencyMs: int
    costUsd: float | None
```

**Status codes:** 200 / 400 (Pydantic) / 404 (patient) / 422 (no embeddings) / 502 (LLM upstream).

### GET /api/search/patients

Query params: `q` (string, 1–500 chars), `limit` (int, 1–50, default 10).

```python
class PatientSearchSnippet(BaseModel):
    refType: str
    refId: str
    content: str  # ≤ 300 chars
    score: float

class PatientSearchHit(BaseModel):
    patientId: str
    name: str | None
    score: float
    snippets: list[PatientSearchSnippet]

class PatientSearchResponse(BaseModel):
    query: str
    embeddingModel: str
    latencyMs: int
    results: list[PatientSearchHit]
```

**Status codes:** 200 (empty results OK) / 422 (Pydantic validation) / 502 (embedding upstream).

### Backward compatibility

- `GET /api/search` (the existing chunk-level endpoint) is **not removed**. Stays available for any callers that already use it.
- `vector_search(q, patient_id, limit)` (the existing function) is **not modified**. The new `vector_search_with_scores` is additive.

## 6. Patient-search SQL

```sql
WITH ranked AS (
  SELECT
    e.patient_id,
    1 - (e.embedding <=> CAST(:qvec AS vector)) AS score,
    e.content,
    e.ref_type, e.ref_id,
    ROW_NUMBER() OVER (
      PARTITION BY e.patient_id
      ORDER BY e.embedding <=> CAST(:qvec AS vector) ASC
    ) AS rn
  FROM embeddings e
  WHERE e.patient_id IS NOT NULL
)
SELECT
  r.patient_id,
  p.name,
  MAX(r.score) AS score,
  JSON_AGG(JSON_BUILD_OBJECT(
    'refType', r.ref_type, 'refId', r.ref_id,
    'content', LEFT(r.content, 300), 'score', r.score
  ) ORDER BY r.score DESC) FILTER (WHERE r.rn <= 3) AS top_snippets
FROM ranked r
LEFT JOIN patients p ON p.patient_id = r.patient_id
GROUP BY r.patient_id, p.name
ORDER BY MAX(r.score) DESC
LIMIT :limit;
```

The window function keeps only the top-3 snippets per patient, then `MAX(score)` ranks patients by their best-matching chunk.

## 7. RAG prompt

```
RAG_SYSTEM (new constant in app/prompts/templates.py):

You are a clinical retrieval assistant. The user has asked a question about
ONE patient. You will be given a question and a numbered list of excerpts
retrieved from that patient's notes via vector similarity search.

Rules:
1. Answer using ONLY the excerpts. If the excerpts don't contain enough
   information to answer, say so explicitly — do not guess or use general
   medical knowledge to fill gaps.
2. Cite supporting excerpts inline using [N] where N is the excerpt number.
   Multiple citations OK: "Patient has diabetes [1][3]."
3. Keep answers concise and clinically precise. Prefer specific findings
   over generalities.
4. Output Markdown. No greeting, no preamble — go straight to the answer.
5. End with the standard AI-assisted disclaimer if the answer required any
   inference beyond direct quote.
```

The user message follows this shape:

```
Question: {question}

Relevant excerpts from this patient's notes:
[1] {chunks[0].content}
[2] {chunks[1].content}
...
[N] {chunks[N-1].content}

Answer the question using ONLY the excerpts. Cite as [N].
```

In chat mode, the prior history is interleaved as alternating `role: user` / `role: assistant` messages in the chat-completions payload BEFORE the new user turn (so the system prompt is first, then history, then the question + excerpts). The latest excerpts are re-fetched for every question — we re-retrieve, not just rely on the model's memory of prior excerpts. The history trim rule: at most 6 turns AND ≤ 3000 chars total content.

## 8. Citations

`build_citations(chunks, answer)`:

```python
def build_citations(chunks: list[dict], answer: str) -> list[RagCitation]:
    cited = parse_cited_indices(answer)
    return [
        RagCitation(
            n=i + 1,
            refType=c["ref_type"],
            refId=c["ref_id"],
            content=(c["content"] or "")[:300],
            score=float(c["score"]),
            cited=(i + 1) in cited,
        )
        for i, c in enumerate(chunks)
    ]


def parse_cited_indices(markdown: str) -> set[int]:
    """Extract the set of [N] indices referenced in the answer text."""
    return {int(m.group(1)) for m in re.finditer(r"\[(\d+)\]", markdown)}
```

The response returns ALL chunks (`cited=True` for those the LLM referenced; `cited=False` otherwise). The frontend renders only the cited ones in the citation footer of the answer card, but the "behind the scenes" pane shows all of them so users can see what was retrieved vs what was used.

Citation navigation (frontend `CitationBadge`):
- `refType='note'` → router push to `/patients/:id?note=<refId>` — PatientDetail's Notes tab pre-selects the file on `?note=` query param.
- `refType='fact'` → router push to `/patients/:id` (EMR-vs-facts tab); selecting a specific document is a follow-up since fact refIds are synthesized (no direct document link).

## 9. UI components

- `frontend/src/views/VectorDemoView.vue` — route component, tabs container.
- `frontend/src/components/vector-demo/RagPanel.vue` — RAG tab.
- `frontend/src/components/vector-demo/PatientSearchPanel.vue` — Patient search tab.
- `frontend/src/components/vector-demo/CitationBadge.vue` — shared citation chip.
- `frontend/src/components/PatientSearchInput.vue` — app-bar nav input.
- `frontend/src/utils/citations.js` — `parseCitedIndices(markdown)` helper.

Router (`frontend/src/router.js`):
```javascript
{ path: '/vector-demo', component: () => import('./views/VectorDemoView.vue'), name: 'vector-demo' },
```

`App.vue` additions:
- `<PatientSearchInput />` in the spacer area, hidden on small screens (`d-none d-md-inline-flex`).
- `<v-btn variant="text" to="/vector-demo" prepend-icon="mdi-database-search-outline">Vector</v-btn>` next to the existing nav buttons.

API client (`frontend/src/api/client.js`):
```javascript
export const ragAsk = (body) => api.post('/api/rag/ask', body).then(data)
export const searchPatientsByVector = (q, limit = 10, signal) =>
  api.get('/api/search/patients', { params: { q, limit }, signal }).then(data)
```

`PatientDetail.vue` reads `route.query.note` and pre-selects that file on the Notes tab when present (small watcher addition).

## 10. Error handling

| Case | Behavior |
|---|---|
| Patient not found | 404; UI error alert |
| No embeddings for patient | 422 with actionable detail; UI shows Ingest link |
| LLM upstream error | 502; UI error alert with Retry |
| Rapid submits | AbortController cancels prior; only newest renders |
| Chat history overflow | Trim oldest turns until ≤ 6 turns AND ≤ 3000 chars; show "Trimmed earlier turns" note |
| Hallucinated `[N]` index | Filtered out by `citedIndices ∩ valid range` |
| Patient-search empty query | 422 from Pydantic; nav input has client-side min-length=2 guard |
| Patient-search no results | 200 with `results: []`; UI EmptyState |
| Embedding service down | 502; existing axios interceptor surfaces snackbar |

## 11. Testing

### Backend pytest

- **`test_rag_citations.py`** (unit, TDD) — 5 cases for `parse_cited_indices` + `build_citations`.
- **`test_rag_routes.py`** (integration) — 7 cases: RAG happy path, 404 patient, 422 no-embeddings, chat-mode history forwarded to provider, patient-search ranking, empty `q` validation, no-results.
- Conftest extension: `fake_store.prime_search_results(rows)` helper that returns synthetic patient-search SQL output (avoid implementing pgvector cosine in the FakeStore).
- `MockProvider.rag_ask` returns deterministic answer that cites `[1]`.

### Frontend Vitest

- **`RagPanel.spec.js`** — 3 cases: Ask disabled without patient; answer + citations render; chat-mode history persists.
- **`PatientSearchPanel.spec.js`** — 2 cases: result cards render; 0 results → EmptyState.
- **`PatientSearchInput.spec.js`** — 2 cases: debounced fetch; result links use `:to`.

### E2E Playwright

- **`vector-demo.spec.ts`** — single smoke: tabs present, Patient-search returns results for "diabetes", app-bar dropdown returns at least one result.

### Test budget

- pytest unit: <50ms; integration: <800ms.
- Vitest: <1s per file.
- Playwright: ~30s.

## 12. Out-of-scope follow-ups

- Hybrid keyword + vector search (BM25 + cosine).
- Cross-patient RAG ("ask one question across N patients").
- Streaming RAG responses (SSE) for low-latency UX.
- LLM-side reranking (Cohere rerank, etc.).
- Persisting RAG conversations server-side (currently in-memory only).
- A clinician-facing RAG affordance in the main app (the demo page is the only RAG surface in v1).
- Pre-selecting a specific document on the EMR-vs-facts tab when a `refType='fact'` citation is clicked (currently just navigates to the patient page).
- A `AI_MODEL_RAG` per-task env override (RAG currently uses `AI_MODEL`).
