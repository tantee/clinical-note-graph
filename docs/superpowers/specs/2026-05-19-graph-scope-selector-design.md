# Graph view: scope selector + decluttering

**Status:** Design approved — awaiting final user review before plan write-up.
**Owner:** —
**Created:** 2026-05-19
**Related issues:** (to be created in GitHub before implementation)
**Depends on:** PR #4 (encounter-scoped AI summary + coding) — merged.

---

## 1. Problem

Today's graph view dumps every node and edge for the entire patient onto a
force-directed canvas. A patient with even modest complexity ends up as a
visual hairball — same condition mentioned in 3 encounters shows as 3
separate nodes, document nodes clutter the middle, no way to focus on
a specific admission.

This design rebuilds the graph view around the principle **"fewer nodes by
default, filters on demand"**, adds a context-aware default
(patient-page = overview, encounter-page = scoped), and surfaces three
scope modes — all / latest encounter / pick — through a compact toolbar.

## 2. Non-goals

- Switching the underlying layout algorithm (force-directed via vis-network
  is kept). Hierarchical or radial layouts can be a follow-up if the
  decluttering doesn't deliver enough visual clarity.
- Graph-write-time deduplication (collapsing same-condition nodes in
  Neo4j on ingest). Dedupe happens at query time in Python. A schema
  change to MERGE on `(patientId, normalized_code)` is out of scope.
- Cross-fact relationships (e.g., `Hypertension —treated-by→ Lisinopril`).
  Current model shows Patient/Encounter → Fact edges only; that stays.
- Deep-linking the graph's filter state via URL query params. State is
  component-local for v1.

## 3. Scope semantics

Two contextual entry points to the same `<GraphView>` component:

- **From the patient page Graph tab** → `scope='patient'`. Defaults to
  deduped facts across all encounters; no encounter nodes; no document
  nodes. Answers "what's on this patient's chart overall".
- **From the encounter dialog Graph tab** → `scope='encounter'`. Renders
  Patient + the focal encounter + that encounter's facts. The toolbar's
  scope-chip group is hidden (the encounter IS the scope); only the
  Filters cog is shown.

A user on the patient page can drill into a single encounter via the
toolbar's "Pick…" chip without leaving the page — this opens a small
modal listing the patient's encounters and switches `scope='encounters'`
once the user applies the selection. The Pick modal supports multi-select.

## 4. Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Frontend                                                                  │
│                                                                           │
│ GraphView.vue (reworked)                                                  │
│   Single component, props: patientId, scope, encounterIds                 │
│   Toolbar: [All | Latest enc. | Pick…]  [Fit] [⚙ Filters]                 │
│   Canvas:  vis-network (kept)                                             │
│   Drawer:  filter side-panel (node types, dedupe, review status)          │
│   Pick…:   v-dialog with multi-select encounter list                      │
│                                                                           │
│ PatientDetail.vue                                                         │
│   Graph tab → <GraphView scope="patient" :patient-id>                     │
│   Watches route.params.eid → renders <EncounterDialog> when present       │
│                                                                           │
│ EncounterDialog.vue  (refactored from PR #4's EncounterDetail.vue)       │
│   <v-dialog fullscreen :model-value="!!eid">                              │
│     Toolbar: close + encounter title + Summarize button                   │
│     v-tabs: Detail | Graph                                                │
│       Detail = existing PR #4 layout (summary/coding/background/docs)     │
│       Graph  = <GraphView scope="encounter" :encounter-ids=[eid]>         │
│                                                                           │
│ Router                                                                    │
│   /patient/:id                       → PatientDetail                      │
│   /patient/:id/encounter/:eid        → PatientDetail (eid triggers dialog)│
│                                                                           │
├──────────────────────────────────────────────────────────────────────────┤
│ Backend                                                                   │
│ GET /api/patient/{pid}/graph (extended)                                   │
│   ?scope=patient|encounter|encounters    (default: patient)               │
│   &encounterId=<eid>                      (repeatable when scope≠patient) │
│   &dedupe=true|false                      (default: true, except          │
│                                            scope=encounter where false)   │
│   &includeEncounters=true|false           (default: false for patient,    │
│                                            true for encounter)            │
│   &includeDocuments=true|false            (default: false)                │
│   &reviewStatus=all|confirmed|hide_rejected (default: hide_rejected)      │
│                                                                           │
│ fetch_graph(patient_id, *, scope, encounter_ids, dedupe,                  │
│             include_encounters, include_documents, review_status)         │
│                                                                           │
│ fetch_patient_graph(patient_id) — legacy entry point, delegates to        │
│   fetch_graph with patient-level defaults; existing callers unchanged.    │
│                                                                           │
│ _dedupe_nodes_edges(rows) — pure function, factored out for TDD.          │
└──────────────────────────────────────────────────────────────────────────┘
```

## 5. API surface

Single endpoint at `/api/patient/{pid}/graph`, extended with query
parameters. No new routes.

| Param | Type | Default | Purpose |
|---|---|---|---|
| `scope` | `'patient' \| 'encounter' \| 'encounters'` | `'patient'` | Which subgraph to return. |
| `encounterId` | list of string | — | Required when `scope` is `'encounter'` or `'encounters'`. Repeatable. |
| `dedupe` | bool | `true` (false for single-encounter) | Collapse same-condition / same-med facts across encounters. |
| `includeEncounters` | bool | `false` for patient scope, `true` for encounter scope | Whether to return Encounter nodes. |
| `includeDocuments` | bool | `false` | Whether to return Document nodes. |
| `reviewStatus` | `'all' \| 'confirmed' \| 'hide_rejected'` | `'hide_rejected'` | Filter facts by their review_status. |

**Response shape** (unchanged from current `fetch_patient_graph`):

```json
{
  "nodes": [
    { "id": "...", "label": "Patient \| Encounter \| Document \| Condition \| ...",
      "data": {...} }
  ],
  "edges": [
    { "from": "...", "to": "...", "type": "HAS_ENCOUNTER \| MENTIONS \| ..." }
  ]
}
```

**Backward compatibility:** `GET /patient/{pid}/graph` with no query params
behaves like before EXCEPT that facts are now deduped by default — this is
a user-visible improvement, not a regression. Existing callers continue
to work; the response is just cleaner.

## 6. Dedupe logic

Pure function `_dedupe_nodes_edges(rows: list[dict]) -> dict`:

```python
def dedupe_key(node: dict) -> tuple | None:
    """Return the dedupe key, or None to skip dedupe for this node type."""
    label = node["label"]
    data = node.get("data", {})
    if label == "Condition":
        return ("Condition", data.get("normalized_code") or data.get("value", "").casefold())
    if label == "Medication":
        # rxNorm preferred; falls back to lowercase name. Two different rxNorms
        # for the same generic name stay separate.
        return ("Medication", data.get("rxNorm") or data.get("name", "").casefold())
    if label == "Allergy":
        return ("Allergy", data.get("normalized_code") or data.get("value", "").casefold())
    if label == "Observation":
        # Observations are tricky — same name with different values across time
        # is informative, not duplicate. Dedupe by (name, valueBucket) where
        # valueBucket is the value rounded to int OR null when value is text.
        return None  # don't dedupe observations in v1
    return None  # Patient, Encounter, Document, Plan, Procedure — pass-through
```

When two nodes share a key, the first wins; edges incoming to the second
are rewritten to point at the first. The rewrite is straightforward —
build an id-remap dict, walk the edge list once.

## 7. Performance

Hard cap of **500 nodes before dedupe** in the backend. When exceeded,
the endpoint returns `HTTP 422` with body:

```json
{ "detail": "Graph too large; narrow the scope", "nodeCount": 783 }
```

The frontend surfaces this as an `<v-alert type='warning' variant='tonal' closable>`
banner suggesting Dedupe / single-encounter / "Confirmed only" review
filter. The canvas remains empty (no partial render).

vis-network physics: `stabilization: { iterations: 200 }` so the canvas
settles in ~200ms. No infinite physics loops.

## 8. UI components

### 8.1 GraphView.vue (rework)

Replaces the existing single-card patient-graph layout. New structure:

- Toolbar (top): chip group (All / Latest enc. / Pick…) + fit/refresh
  icons + filter-cog icon. Chip group hidden when `scope='encounter'`.
- Canvas: existing vis-network container (`graph-canvas` class).
- Filter side-panel: `<v-navigation-drawer location='right' temporary>`.
  Contents: node-type switches (Encounters, Documents, Plans, Procedures,
  Allergies, Observations), dedupe switch, review-status radio.
- Pick-encounters dialog: `<v-dialog max-width='480'>` with
  `<v-list select-strategy='multiple'>`. A `<v-text-field>` at the top
  filters the list by encounter date or type.
- Empty / error states: existing `<EmptyState>` and `<v-alert>` patterns.
- 422 oversized banner: `<v-alert>` rendered above the canvas; clearable.

The component is local-state only. Filter changes debounce by 200ms and
trigger a new `getGraph` call; in-flight calls are aborted via
`AbortController.signal` when scope/filters change.

### 8.2 EncounterDialog.vue (refactor from EncounterDetail.vue)

Currently `EncounterDetail.vue` is a route component rendered in the
standard app shell. This feature refactors it to a fullscreen dialog
component that PatientDetail renders based on route.params.eid.

Structure:

```vue
<v-dialog :model-value="!!eid" fullscreen
          transition="dialog-bottom-transition"
          @update:model-value="$emit('close')">
  <v-card class="d-flex flex-column">
    <v-toolbar>...</v-toolbar>
    <v-tabs v-model="tab">
      <v-tab value="detail">Detail</v-tab>
      <v-tab value="graph">Graph</v-tab>
    </v-tabs>
    <v-window v-model="tab" class="flex-grow-1 overflow-y-auto">
      <v-window-item value="detail">{{ existing layout }}</v-window-item>
      <v-window-item value="graph">
        <GraphView scope="encounter" :patient-id :encounter-ids="[eid]" />
      </v-window-item>
    </v-window>
  </v-card>
</v-dialog>
```

All the existing Detail-tab behaviors carry over from PR #4's
`EncounterDetail.vue`: summary card, coding card, background panel, docs
list, Summarize/Code buttons with scroll-into-view, latest-summary
auto-load on mount.

### 8.3 PatientDetail.vue (small change)

Watches `route.params.eid`. Renders `<EncounterDialog>` when present.
Closing the dialog navigates to the parent route via
`router.push({ name: 'patient', params: { id } })`.

The existing Patients-list-expand and Encounters-tab navigation already
push to `/patient/:id/encounter/:eid` — they keep working unchanged. The
dialog opens because PatientDetail now reacts to the URL param.

### 8.4 Router

```javascript
{
  path: '/patient/:id',
  name: 'patient',
  component: () => import('./views/PatientDetail.vue'),
  props: true,
},
{
  path: '/patient/:id/encounter/:eid',
  name: 'encounter',
  component: () => import('./views/PatientDetail.vue'),  // same component
  props: true,
},
```

Both routes resolve to `PatientDetail.vue`. The encounter route just
passes the `eid` prop in addition to `id`, which PatientDetail uses to
open the dialog. Replaces PR #4's standalone `EncounterDetail.vue`
route component.

### 8.5 API client

```javascript
export const getGraph = (id, options = {}, signal) =>
  api.get(`/api/patient/${encodeURIComponent(id)}/graph`,
          { params: options, signal }).then(data)
```

Backward compat: `getGraph(id)` (no options) still works. New callers
pass `{scope, encounterId, dedupe, includeEncounters, includeDocuments,
reviewStatus}`.

## 9. Error handling

| Case | Behavior |
|---|---|
| 422 oversized | Banner above empty canvas; suggest Dedupe / single encounter / Confirmed-only. |
| Empty graph (0 nodes) | EmptyState with contextual hint ("No facts yet" vs "Encounter has no facts"). |
| 5xx / network | Existing axios interceptor surfaces snackbar; component shows `<v-alert>` with Retry button. |
| 404 on encounter scope | Component shows "Encounter not found" message. |
| Rapid filter toggles | AbortController cancels prior fetch; only the latest result renders. |
| Dialog closes mid-fetch | `onBeforeUnmount` aborts pending fetches. |
| Theme toggle mid-render | Existing `watch(() => ui.theme, render)` re-renders. |

## 10. Testing

### Backend (`backend/tests/`) — pytest

1. **`test_graph_dedupe.py`** (unit, TDD) — `_dedupe_nodes_edges`:
   collapse by `normalized_code`, collapse by value when code is null,
   medication dedupe by rxNorm, observations not deduped, edges remap
   correctly when two nodes collapse.
2. **`test_graph_routes.py`** (integration) — all query-param combinations,
   422 when synthetic input exceeds 500 nodes, 404 when encounterId
   doesn't belong to patient, reviewStatus filtering, default
   patient-scope shape.
3. **`test_graph_legacy_signature.py`** (regression) — `fetch_patient_graph`
   still works for existing callers.

### Frontend — Vitest

4. **`GraphView.spec.js`** — scope='patient' shows chip group;
   scope='encounter' hides chip group; filter toggle re-fires `getGraph`
   with new params; 422 renders oversized banner.
5. **`EncounterDialog.spec.js`** — dialog renders when eid present;
   tab switch mounts GraphView with encounter scope; close event fires.
6. **`PatientDetail.dialog.spec.js`** (extends existing spec) —
   route `/patient/HN-1/encounter/E1` renders EncounterDialog;
   route `/patient/HN-1` does not.

### E2E — Playwright

7. **`graph-scope.spec.ts`** — patient page → Graph tab → canvas renders;
   filter cog opens drawer; navigate to encounter URL → dialog opens →
   Graph tab renders without chip group. (Same Vuetify-v4 selector
   tuning caveats as PR #4's e2e test apply.)

### Budget

Unit < 50ms; integration < 500ms per file; vitest < 1s per spec;
playwright ~30s.

## 11. Implementation order (preview — full plan from writing-plans)

1. Backend: `_dedupe_nodes_edges` (TDD) + `fetch_graph` refactor + extend
   route handler with query params.
2. Backend: 422 cap + tests.
3. Frontend client: `getGraph(id, options)` extension.
4. Frontend: GraphView rework — toolbar + filter drawer + Pick dialog.
5. Frontend: EncounterDialog refactor (renames EncounterDetail.vue,
   wraps in v-dialog fullscreen, adds tabs).
6. Frontend: PatientDetail watches `route.params.eid`; router adds the
   encounter alias route.
7. Tests: backend pytest + frontend Vitest + Playwright e2e.
8. PR.

## 12. Out-of-scope follow-ups

- Hierarchical / radial layout option (if force-directed still feels
  chaotic after dedupe + filtering).
- Graph-write-time dedupe in Neo4j (MERGE on patientId+normalized_code).
- Cross-fact relationships (`Condition —treated_by→ Medication`).
- Deep-linking filter state via query params.
- Per-encounter color coding when multi-encounter scope is selected.
- Background panel parity inside the Graph tab (currently only on the
  Detail tab of the encounter dialog).
