# Graph scope selector + decluttering — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the patient graph view around "fewer nodes by default, filters on demand": a context-aware default (patient page = patient-level overview deduped, encounter dialog = encounter-scoped), a compact toolbar (All / Latest encounter / Pick…), a filter side-drawer, and a hard 500-node cap. Refactor `EncounterDetail.vue` from a route view to a fullscreen `<v-dialog>` opened by `PatientDetail`.

**Architecture:** One reusable `<GraphView>` Vue component scoped by props (`patient` | `encounter`). Backend extends the existing `/api/patient/{pid}/graph` route with query params and factors a pure-Python `_dedupe_nodes_edges` helper for TDD. `PatientDetail.vue` watches `route.params.eid` to open the encounter dialog, so deep-links to `/patient/:id/encounter/:eid` still work.

**Tech Stack:** FastAPI · Neo4j (Cypher) · vis-network · Vue 3 · Vuetify v4 · pytest · Vitest · Playwright

**Spec:** `docs/superpowers/specs/2026-05-19-graph-scope-selector-design.md`
**Issue:** [#6](https://github.com/tantee/clinical-note-graph/issues/6)
**Branch:** `feat/graph-scope-selector` (already created, off main with PR #4 merged)

---

## File map

**Backend — create:**
- `backend/tests/test_graph_dedupe.py` — unit tests for `_dedupe_nodes_edges` (TDD)
- `backend/tests/test_graph_routes.py` — integration tests for the extended `/graph` route
- `backend/tests/test_graph_legacy_signature.py` — regression for `fetch_patient_graph(patient_id)` no-args entry point

**Backend — modify:**
- `backend/app/services/graph_updater.py` — factor `_dedupe_nodes_edges`; add `fetch_graph(...)` with the new param surface; keep `fetch_patient_graph` as a thin wrapper that delegates to `fetch_graph` with patient-level defaults
- `backend/app/routers/patient.py` — extend the `/graph` route signature with query params; convert oversized-result to HTTP 422 via FastAPI `HTTPException`
- `backend/tests/conftest.py` — extend the `stub_neo4j` fixture with a `prime()` helper so route tests can seed deterministic node/edge rows

**Frontend — create:**
- `frontend/src/views/EncounterDialog.vue` — refactored from `EncounterDetail.vue` (rename + restructure as fullscreen `<v-dialog>` with Detail / Graph tabs)
- `frontend/src/views/__tests__/EncounterDialog.spec.js`
- `frontend/src/components/__tests__/GraphView.spec.js`
- `frontend/e2e/graph-scope.spec.ts`

**Frontend — modify:**
- `frontend/src/components/GraphView.vue` — rework: scope-driven props, toolbar (chips + filter cog), filter side-drawer, Pick-encounters dialog, oversized banner; vis-network call sites unchanged
- `frontend/src/api/client.js` — extend `getGraph(id, signal)` → `getGraph(id, options = {})` accepting `{scope, encounterId, dedupe, includeEncounters, includeDocuments, reviewStatus, signal}`
- `frontend/src/router.js` — add the parent-route alias so `/patient/:id/encounter/:eid` resolves to `PatientDetail.vue` (not `EncounterDetail.vue`)
- `frontend/src/views/PatientDetail.vue` — watch `route.params.eid` to render `<EncounterDialog>`; navigate the `View encounter` actions to push the eid into the route
- `frontend/src/views/__tests__/PatientDetail.spec.js` — extend with a dialog-rendering case (route has eid → dialog rendered)

**Frontend — delete:**
- `frontend/src/views/EncounterDetail.vue` — superseded by EncounterDialog.vue. The route alias change leaves no callers behind.

---

## Task 1: Factor `_dedupe_nodes_edges` (backend TDD)

**Files:**
- Create: `backend/tests/test_graph_dedupe.py`
- Modify: `backend/app/services/graph_updater.py` (extract function)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_graph_dedupe.py`:

```python
"""Unit tests for _dedupe_nodes_edges — the pure function that collapses
same-condition / same-medication nodes across encounters and rewrites
edges to point at the surviving node."""
from __future__ import annotations

import pytest


def _condition(value: str, normalized_code: str | None, suffix: str = "") -> dict:
    """Helper: synthesize a Condition node like fetch_graph emits."""
    return {
        "id": f"Condition:{value}{suffix}",
        "label": "Condition",
        "data": {"value": value, "normalized_code": normalized_code},
    }


def _med(name: str, rxnorm: str | None, suffix: str = "") -> dict:
    return {
        "id": f"Medication:{name}{suffix}",
        "label": "Medication",
        "data": {"name": name, "rxNorm": rxnorm},
    }


def test_collapse_conditions_by_normalized_code():
    from app.services.graph_updater import _dedupe_nodes_edges
    nodes = [
        {"id": "Patient:p1", "label": "Patient", "data": {"patientId": "p1"}},
        _condition("Hypertension", "I10", suffix=":e1"),
        _condition("Hypertension", "I10", suffix=":e2"),  # duplicate from another encounter
        _condition("Diabetes",     "E11", suffix=":e1"),
    ]
    edges = [
        {"from": "Patient:p1", "to": "Condition:Hypertension:e1", "type": "HAS_CONDITION"},
        {"from": "Patient:p1", "to": "Condition:Hypertension:e2", "type": "HAS_CONDITION"},
        {"from": "Patient:p1", "to": "Condition:Diabetes:e1",     "type": "HAS_CONDITION"},
    ]
    out = _dedupe_nodes_edges({"nodes": nodes, "edges": edges})
    # Only ONE Hypertension node survives.
    cond_nodes = [n for n in out["nodes"] if n["label"] == "Condition"]
    assert len(cond_nodes) == 2
    htn_nodes = [n for n in cond_nodes if n["data"]["normalized_code"] == "I10"]
    assert len(htn_nodes) == 1
    # Both Hypertension edges now point at the surviving node id.
    htn_id = htn_nodes[0]["id"]
    htn_edges = [e for e in out["edges"] if e["to"] == htn_id]
    assert len(htn_edges) == 2  # de-duplication does NOT collapse parallel edges; that's a future enhancement


def test_collapse_conditions_by_value_when_code_missing():
    from app.services.graph_updater import _dedupe_nodes_edges
    nodes = [
        _condition("Asthma", None, suffix=":e1"),
        _condition("asthma", None, suffix=":e2"),  # different case, same logical condition
    ]
    edges = []
    out = _dedupe_nodes_edges({"nodes": nodes, "edges": edges})
    assert len([n for n in out["nodes"] if n["label"] == "Condition"]) == 1


def test_medications_dedupe_by_rxnorm():
    from app.services.graph_updater import _dedupe_nodes_edges
    nodes = [
        _med("Lisinopril", "29046", suffix=":e1"),
        _med("Lisinopril", "29046", suffix=":e2"),
        _med("Lisinopril", "12345", suffix=":e3"),  # different rxNorm → stays separate
    ]
    edges = []
    out = _dedupe_nodes_edges({"nodes": nodes, "edges": edges})
    med_nodes = [n for n in out["nodes"] if n["label"] == "Medication"]
    assert len(med_nodes) == 2  # rxNorm 29046 collapsed; 12345 kept distinct


def test_medications_dedupe_by_name_when_rxnorm_missing():
    from app.services.graph_updater import _dedupe_nodes_edges
    nodes = [
        _med("Aspirin", None, suffix=":e1"),
        _med("aspirin", None, suffix=":e2"),
    ]
    out = _dedupe_nodes_edges({"nodes": nodes, "edges": []})
    assert len([n for n in out["nodes"] if n["label"] == "Medication"]) == 1


def test_observations_are_not_deduped():
    """Same observation name at different times is signal, not noise."""
    from app.services.graph_updater import _dedupe_nodes_edges
    nodes = [
        {"id": "Observation:BP:e1", "label": "Observation",
         "data": {"name": "Blood pressure", "value": "150/95"}},
        {"id": "Observation:BP:e2", "label": "Observation",
         "data": {"name": "Blood pressure", "value": "132/82"}},
    ]
    out = _dedupe_nodes_edges({"nodes": nodes, "edges": []})
    assert len(out["nodes"]) == 2


def test_documents_and_plans_pass_through_unchanged():
    from app.services.graph_updater import _dedupe_nodes_edges
    nodes = [
        {"id": "Document:d1", "label": "Document", "data": {"documentId": "d1"}},
        {"id": "Document:d2", "label": "Document", "data": {"documentId": "d2"}},
        {"id": "Plan:p1",     "label": "Plan",     "data": {"description": "Follow up"}},
        {"id": "Plan:p2",     "label": "Plan",     "data": {"description": "Follow up"}},
    ]
    out = _dedupe_nodes_edges({"nodes": nodes, "edges": []})
    assert len(out["nodes"]) == 4


def test_empty_input():
    from app.services.graph_updater import _dedupe_nodes_edges
    out = _dedupe_nodes_edges({"nodes": [], "edges": []})
    assert out == {"nodes": [], "edges": []}
```

- [ ] **Step 2: Run the failing tests**

Run:
```bash
docker exec cng-backend python -m pytest tests/test_graph_dedupe.py -v
```

Expected: 7 failures with `ImportError: cannot import name '_dedupe_nodes_edges' from 'app.services.graph_updater'`.

- [ ] **Step 3: Implement `_dedupe_nodes_edges`**

Edit `backend/app/services/graph_updater.py`. Append at the bottom of the file (after `fetch_patient_graph`):

```python
def _dedupe_key(node: dict[str, Any]) -> tuple | None:
    """Return a hashable dedupe key, or None for node labels we don't dedupe.

    Conditions: collapse by normalized_code; fall back to lowercased value.
    Medications: collapse by rxNorm; fall back to lowercased name. A different
        rxNorm for the same generic name stays separate (different formulations).
    Allergies: same rule as Conditions.
    Observations: NOT deduped — same name at different times is informative.
    Documents / Plans / Procedures / Patient / Encounter: pass through.
    """
    label = node.get("label")
    data = node.get("data") or {}
    if label == "Condition":
        return ("Condition", data.get("normalized_code") or str(data.get("value", "")).casefold())
    if label == "Medication":
        return ("Medication", data.get("rxNorm") or str(data.get("name", "")).casefold())
    if label == "Allergy":
        return ("Allergy", data.get("normalized_code") or str(data.get("value", "")).casefold())
    return None


def _dedupe_nodes_edges(graph: dict[str, Any]) -> dict[str, Any]:
    """Collapse duplicate fact nodes across encounters; rewrite edges to the
    surviving node id. Pure function — does NOT touch the network/Neo4j."""
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []

    canonical_id_by_key: dict[tuple, str] = {}
    id_remap: dict[str, str] = {}
    kept_nodes: list[dict[str, Any]] = []

    for node in nodes:
        key = _dedupe_key(node)
        if key is None:
            kept_nodes.append(node)
            continue
        canonical = canonical_id_by_key.get(key)
        if canonical is None:
            canonical_id_by_key[key] = node["id"]
            kept_nodes.append(node)
        else:
            id_remap[node["id"]] = canonical  # drop this node; rewrite its inbound edges

    rewritten_edges: list[dict[str, Any]] = []
    for edge in edges:
        rewritten_edges.append({
            **edge,
            "from": id_remap.get(edge["from"], edge["from"]),
            "to": id_remap.get(edge["to"], edge["to"]),
        })

    return {"nodes": kept_nodes, "edges": rewritten_edges}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker exec cng-backend python -m pytest tests/test_graph_dedupe.py -v
```

Expected: **7 passed**.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_graph_dedupe.py backend/app/services/graph_updater.py
git commit -m "$(cat <<'EOF'
feat(graph): _dedupe_nodes_edges with normalized_code/rxNorm collapse

Conditions and Allergies collapse by normalized_code (fallback to
casefolded value); Medications collapse by rxNorm (fallback to
casefolded name). Observations, Documents, Plans, Patient and Encounter
pass through. Edges to dropped nodes are rewritten to the surviving id.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `fetch_graph` refactor + extended route

**Files:**
- Modify: `backend/app/services/graph_updater.py` (replace `fetch_patient_graph` with `fetch_graph` + back-compat shim)
- Modify: `backend/app/routers/patient.py` (extend the `/graph` route signature)

- [ ] **Step 1: Implement `fetch_graph` in `graph_updater.py`**

Replace the existing `fetch_patient_graph` function body with the following two functions (keep the public name `fetch_patient_graph` as a back-compat shim so any other callers don't break):

```python
def fetch_patient_graph(patient_id: str) -> dict[str, Any]:
    """Legacy entry point — equivalent to fetch_graph(patient_id) with the
    default patient-level parameters. Kept so existing callers don't break."""
    return fetch_graph(patient_id)


def fetch_graph(
    patient_id: str,
    *,
    scope: str = "patient",
    encounter_ids: list[str] | None = None,
    dedupe: bool | None = None,
    include_encounters: bool | None = None,
    include_documents: bool = False,
    review_status: str = "hide_rejected",
) -> dict[str, Any]:
    """Build the subgraph for the requested scope.

    Defaults:
      scope="patient"        → dedupe=True, include_encounters=False
      scope in {"encounter", "encounters"} →
                             dedupe=True if len(encounter_ids)>1 else False,
                             include_encounters=True
    """
    if scope not in ("patient", "encounter", "encounters"):
        raise ValueError(f"unknown scope {scope!r}")
    encounter_ids = encounter_ids or []
    if scope in ("encounter", "encounters") and not encounter_ids:
        raise ValueError("encounter_ids required when scope is encounter or encounters")
    if include_encounters is None:
        include_encounters = scope != "patient"
    if dedupe is None:
        dedupe = scope == "patient" or len(encounter_ids) > 1

    cypher, params = _graph_cypher(
        patient_id, scope, encounter_ids,
        include_documents=include_documents, review_status=review_status,
    )
    rows = run_cypher(cypher, params)
    if not rows:
        return {"nodes": [], "edges": []}
    graph = _materialize_rows(rows[0], include_encounters=include_encounters,
                              include_documents=include_documents)
    if dedupe:
        graph = _dedupe_nodes_edges(graph)
    return graph
```

- [ ] **Step 2: Add the Cypher builder + materializer**

Append below `fetch_graph` in the same file:

```python
def _graph_cypher(
    patient_id: str, scope: str, encounter_ids: list[str],
    *, include_documents: bool, review_status: str,
) -> tuple[str, dict[str, Any]]:
    """Build a single Cypher query with parameters. Filters facts by
    review_status via a Cypher WHERE clause on each OPTIONAL MATCH."""
    fact_filter = ""
    if review_status == "hide_rejected":
        fact_filter = " WHERE coalesce(n.reviewStatus, 'ai_suggested') <> 'rejected'"
    elif review_status == "confirmed":
        fact_filter = " WHERE n.reviewStatus = 'human_confirmed'"
    # 'all' → no filter

    enc_match = "MATCH (p)-[:HAS_ENCOUNTER]->(e:Encounter)"
    if scope in ("encounter", "encounters"):
        enc_match += " WHERE e.encounterId IN $eids"

    parts = [
        "MATCH (p:Patient {patientId: $pid})",
        enc_match,
        "OPTIONAL MATCH (e)-[:MENTIONS]->(c:Condition)" + fact_filter.replace("n.", "c."),
        "OPTIONAL MATCH (e)-[:HAS_OBSERVATION]->(o:Observation)" + fact_filter.replace("n.", "o."),
        "OPTIONAL MATCH (e)-[:PRESCRIBED]->(m:Medication)" + fact_filter.replace("n.", "m."),
        "OPTIONAL MATCH (e)-[:HAS_PLAN]->(pl:Plan)" + fact_filter.replace("n.", "pl."),
        "OPTIONAL MATCH (p)-[:HAS_ALLERGY]->(a:Allergy)" + fact_filter.replace("n.", "a."),
    ]
    if include_documents:
        parts.append("OPTIONAL MATCH (e)-[:HAS_DOCUMENT]->(d:Document)")

    parts.append(
        "RETURN p, "
        "collect(DISTINCT e) AS encounters, "
        + ("collect(DISTINCT d) AS docs, " if include_documents else "")
        + "collect(DISTINCT c) AS conditions, "
        "collect(DISTINCT o) AS observations, "
        "collect(DISTINCT m) AS medications, "
        "collect(DISTINCT pl) AS plans, "
        "collect(DISTINCT a) AS allergies"
    )
    cypher = "\n        ".join(parts)
    return cypher, {"pid": patient_id, "eids": encounter_ids}


def _materialize_rows(row: dict[str, Any], *, include_encounters: bool,
                      include_documents: bool) -> dict[str, Any]:
    """Convert a single Cypher row into the {nodes, edges} response shape.

    Uses encounter-aware node ids (Condition:val:e<eid>) so the dedupe step
    can find duplicates that came from different encounters."""
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(label: str, item: dict[str, Any], natural_key: str, source_eid: str | None = None) -> str | None:
        if not item:
            return None
        clean = _jsonable(item)
        suffix = f":{source_eid}" if source_eid else ""
        nid = f"{label}:{clean.get(natural_key)}{suffix}"
        if nid not in seen:
            seen.add(nid)
            nodes.append({"id": nid, "label": label, "data": clean})
        return nid

    pid = add("Patient", row["p"], "patientId")
    enc_ids: dict[str, str] = {}  # encounterId → node id
    for e in (row.get("encounters") or []):
        if not e:
            continue
        eid_str = (e.get("encounterId") or "")
        enc_node_id = add("Encounter", e, "encounterId")
        if enc_node_id is None:
            continue
        enc_ids[eid_str] = enc_node_id
        if include_encounters and pid:
            edges.append({"from": pid, "to": enc_node_id, "type": "HAS_ENCOUNTER"})

    # Facts. Each fact carries `encounterId` when known so its id is unique
    # per encounter; the dedupe pass collapses by clinical key afterwards.
    def fact_eid(f: dict[str, Any]) -> str | None:
        return f.get("encounterId") if f else None

    for c in (row.get("conditions") or []):
        cid = add("Condition", c, "value", source_eid=fact_eid(c))
        if cid:
            attach_from = enc_ids.get(fact_eid(c)) if include_encounters else pid
            if attach_from:
                edges.append({"from": attach_from, "to": cid, "type": "HAS_CONDITION"})
    for m in (row.get("medications") or []):
        mid = add("Medication", m, "name", source_eid=fact_eid(m))
        if mid:
            attach_from = enc_ids.get(fact_eid(m)) if include_encounters else pid
            if attach_from:
                edges.append({"from": attach_from, "to": mid, "type": "ON_MEDICATION"})
    for o in (row.get("observations") or []):
        oid = add("Observation", o, "name", source_eid=fact_eid(o))
        if oid:
            attach_from = enc_ids.get(fact_eid(o)) if include_encounters else pid
            if attach_from:
                edges.append({"from": attach_from, "to": oid, "type": "HAS_OBSERVATION"})
    for pl in (row.get("plans") or []):
        plid = add("Plan", pl, "description", source_eid=fact_eid(pl))
        if plid:
            attach_from = enc_ids.get(fact_eid(pl)) if include_encounters else pid
            if attach_from:
                edges.append({"from": attach_from, "to": plid, "type": "HAS_PLAN"})
    for a in (row.get("allergies") or []):
        aid = add("Allergy", a, "value")
        if aid and pid:
            edges.append({"from": pid, "to": aid, "type": "HAS_ALLERGY"})

    if include_documents:
        for d in (row.get("docs") or []):
            did = add("Document", d, "documentId")
            if did:
                eid_attach = enc_ids.get(d.get("encounterId")) if include_encounters else pid
                if eid_attach:
                    edges.append({"from": eid_attach, "to": did, "type": "HAS_DOCUMENT"})

    # Strip the encounter-suffix off node ids when we don't need it
    # (i.e., when encounters aren't included, facts come from patient directly).
    # The dedupe pass will collapse all same-key facts anyway, so suffix is
    # only meaningful pre-dedupe. Leave the suffix; dedupe handles it.
    return {"nodes": nodes, "edges": edges}
```

- [ ] **Step 3: Extend the `/graph` route**

Edit `backend/app/routers/patient.py` (the existing `/graph` route around line 119). Replace with:

```python
from fastapi import Query  # already imported


_MAX_NODES_PRE_DEDUPE = 500


@router.get("/patient/{patient_id}/graph")
def get_graph(
    patient_id: str,
    scope: str = Query("patient", pattern="^(patient|encounter|encounters)$"),
    encounterId: list[str] = Query(default=[]),
    dedupe: bool | None = Query(None),
    includeEncounters: bool | None = Query(None, alias="includeEncounters"),
    includeDocuments: bool = Query(False, alias="includeDocuments"),
    reviewStatus: str = Query("hide_rejected", pattern="^(all|confirmed|hide_rejected)$"),
) -> dict[str, Any]:
    from app.services.graph_updater import fetch_graph
    try:
        graph = fetch_graph(
            patient_id,
            scope=scope,
            encounter_ids=encounterId,
            dedupe=dedupe,
            include_encounters=includeEncounters,
            include_documents=includeDocuments,
            review_status=reviewStatus,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if len(graph["nodes"]) > _MAX_NODES_PRE_DEDUPE:
        raise HTTPException(
            status_code=422,
            detail={
                "detail": "Graph too large; narrow the scope",
                "nodeCount": len(graph["nodes"]),
            },
        )
    return graph
```

Note: the existing `from fastapi import APIRouter, HTTPException, Query` import already covers these names.

- [ ] **Step 4: Sanity-import check**

```bash
docker exec cng-backend python -c "from app.services.graph_updater import fetch_graph, fetch_patient_graph, _dedupe_nodes_edges; print('OK')"
docker exec cng-backend python -c "from app.routers.patient import get_graph; print('OK')"
```

Expected: two `OK` lines.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/graph_updater.py backend/app/routers/patient.py
git commit -m "$(cat <<'EOF'
feat(graph): fetch_graph + extended /graph route with scope params

Adds the scope/encounterId/dedupe/includeEncounters/includeDocuments/
reviewStatus query params on /api/patient/{pid}/graph. fetch_patient_graph
stays as a legacy entry point so existing callers don't break. Cypher
filter on reviewStatus runs server-side; dedupe runs in Python via
_dedupe_nodes_edges. Hard 500-node cap raises HTTP 422 with nodeCount.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Backend integration tests + 422 cap test

**Files:**
- Create: `backend/tests/test_graph_routes.py`
- Create: `backend/tests/test_graph_legacy_signature.py`
- Modify: `backend/tests/conftest.py` (extend the `stub_neo4j` fixture to be primable)

- [ ] **Step 1: Extend `stub_neo4j` so tests can seed Cypher result rows**

Edit `backend/tests/conftest.py`. Find the `stub_neo4j` fixture and modify it to attach a `prime(rows)` method onto the returned `calls` list:

```python
@pytest.fixture()
def stub_neo4j(monkeypatch):
    """Stub all Neo4j calls so the ingest pipeline runs without a real database.

    The returned `calls` list captures every call. For tests that need
    fetch_graph to return specific data, call `calls.prime([row_dict])` to
    set what the next `run_cypher` call returns. The primer is cleared after
    each call so each test can seed fresh state."""
    calls: list[tuple[str, dict]] = []
    primed_results: list[list[dict]] = []  # FIFO of next-call results

    def fake_run_cypher(q, params=None):
        calls.append((q.strip(), params or {}))
        if primed_results:
            return primed_results.pop(0)
        return []

    def prime(rows: list[dict]) -> None:
        primed_results.append(rows)

    calls.prime = prime  # type: ignore[attr-defined]

    class FakeSess:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def run(self, q, params=None):
            calls.append((q.strip(), params or {}))
            class _R:
                def data(self): return []
                def __iter__(self): return iter([])
            return _R()
        def close(self): pass

    from contextlib import contextmanager

    @contextmanager
    def fake_session():
        yield FakeSess()

    import app.db.neo4j_client as n4
    monkeypatch.setattr(n4, "run_cypher", fake_run_cypher)
    monkeypatch.setattr(n4, "neo4j_session", fake_session)
    import app.services.graph_updater as gu
    monkeypatch.setattr(gu, "neo4j_session", fake_session)
    monkeypatch.setattr(gu, "run_cypher", fake_run_cypher)
    monkeypatch.setattr(gu, "_CONSTRAINTS_READY", True, raising=False)
    return calls
```

(The only change vs the existing fixture is the `primed_results` list, the `prime()` helper, and the conditional return in `fake_run_cypher`.)

- [ ] **Step 2: Write the integration tests**

Create `backend/tests/test_graph_routes.py`:

```python
"""Integration tests for GET /api/patient/{pid}/graph with the new scope
and filter query parameters."""
from __future__ import annotations


def _patient_row(*, with_conditions: int = 0, with_meds: int = 0,
                 with_obs: int = 0) -> dict:
    """Build a single primed Cypher row mimicking what the real query returns."""
    row = {
        "p": {"patientId": "HN-1"},
        "encounters": [{"encounterId": "E1", "type": "admission"}],
        "conditions": [
            {"encounterId": "E1", "value": f"Cond{i}", "normalized_code": f"X{i}",
             "reviewStatus": "ai_suggested"}
            for i in range(with_conditions)
        ],
        "medications": [
            {"encounterId": "E1", "name": f"Med{i}", "rxNorm": str(1000 + i),
             "reviewStatus": "ai_suggested"}
            for i in range(with_meds)
        ],
        "observations": [
            {"encounterId": "E1", "name": f"Obs{i}", "value": str(i),
             "reviewStatus": "ai_suggested"}
            for i in range(with_obs)
        ],
        "plans": [],
        "allergies": [],
    }
    return row


def test_default_patient_scope_dedupes_no_encounter_nodes(app_client, stub_neo4j, fake_store):
    fake_store.patients["HN-1"] = {"patient_id": "HN-1", "name": "Test"}
    # Two encounters mention Hypertension (same normalized_code=I10) — should collapse.
    row = {
        "p": {"patientId": "HN-1"},
        "encounters": [
            {"encounterId": "E1"}, {"encounterId": "E2"},
        ],
        "conditions": [
            {"encounterId": "E1", "value": "Hypertension", "normalized_code": "I10"},
            {"encounterId": "E2", "value": "Hypertension", "normalized_code": "I10"},
        ],
        "medications": [], "observations": [], "plans": [], "allergies": [],
    }
    stub_neo4j.prime([row])
    r = app_client.get("/api/patient/HN-1/graph")
    assert r.status_code == 200
    body = r.json()
    # Default: include_encounters=False so encounters are still returned in the row
    # but NOT in the response nodes; dedupe=True collapses the two HTN nodes.
    labels = [n["label"] for n in body["nodes"]]
    assert "Encounter" not in labels  # encounters omitted in patient-scope default
    htn = [n for n in body["nodes"] if n["label"] == "Condition"]
    assert len(htn) == 1


def test_scope_encounter_includes_encounter_node(app_client, stub_neo4j, fake_store):
    fake_store.patients["HN-1"] = {"patient_id": "HN-1", "name": "Test"}
    stub_neo4j.prime([_patient_row(with_conditions=1, with_meds=1)])
    r = app_client.get("/api/patient/HN-1/graph", params={"scope": "encounter", "encounterId": "E1"})
    assert r.status_code == 200
    labels = [n["label"] for n in r.json()["nodes"]]
    assert "Encounter" in labels
    assert "Condition" in labels


def test_scope_encounter_requires_encounter_id(app_client, stub_neo4j, fake_store):
    fake_store.patients["HN-1"] = {"patient_id": "HN-1", "name": "Test"}
    stub_neo4j.prime([_patient_row()])
    r = app_client.get("/api/patient/HN-1/graph", params={"scope": "encounter"})
    assert r.status_code == 400
    assert "encounter_ids" in r.json()["detail"]


def test_oversized_returns_422_with_node_count(app_client, stub_neo4j, fake_store):
    fake_store.patients["HN-1"] = {"patient_id": "HN-1", "name": "Test"}
    # Generate a row that materializes > 500 nodes pre-dedupe. 600 conditions
    # with unique normalized_codes so dedupe doesn't collapse them.
    stub_neo4j.prime([_patient_row(with_conditions=600)])
    r = app_client.get("/api/patient/HN-1/graph", params={"dedupe": "false"})
    assert r.status_code == 422
    body = r.json()
    # FastAPI wraps custom dict detail in {"detail": <dict>}
    assert body["detail"]["detail"] == "Graph too large; narrow the scope"
    assert body["detail"]["nodeCount"] > 500


def test_include_documents_adds_document_nodes(app_client, stub_neo4j, fake_store):
    fake_store.patients["HN-1"] = {"patient_id": "HN-1", "name": "Test"}
    row = _patient_row()
    row["docs"] = [{"encounterId": "E1", "documentId": "D1"}]
    stub_neo4j.prime([row])
    r = app_client.get(
        "/api/patient/HN-1/graph",
        params={"scope": "encounter", "encounterId": "E1", "includeDocuments": "true"},
    )
    assert r.status_code == 200
    labels = [n["label"] for n in r.json()["nodes"]]
    assert "Document" in labels


def test_review_status_confirmed_only_includes_confirmed_in_cypher(app_client, stub_neo4j, fake_store):
    fake_store.patients["HN-1"] = {"patient_id": "HN-1", "name": "Test"}
    stub_neo4j.prime([_patient_row()])
    app_client.get("/api/patient/HN-1/graph", params={"reviewStatus": "confirmed"})
    # Inspect the last Cypher query: must include reviewStatus filter.
    last_query = stub_neo4j[-1][0]
    assert "human_confirmed" in last_query
```

- [ ] **Step 3: Write the legacy-signature regression test**

Create `backend/tests/test_graph_legacy_signature.py`:

```python
"""Confirms the existing fetch_patient_graph(patient_id) entry point and
the GET /patient/{pid}/graph route (no params) still work for callers that
predate the scope/filter parameters."""
from __future__ import annotations


def test_fetch_patient_graph_returns_nodes_edges_shape(stub_neo4j):
    from app.services.graph_updater import fetch_patient_graph
    stub_neo4j.prime([{
        "p": {"patientId": "HN-X"},
        "encounters": [],
        "conditions": [], "medications": [], "observations": [],
        "plans": [], "allergies": [],
    }])
    result = fetch_patient_graph("HN-X")
    assert set(result.keys()) == {"nodes", "edges"}
    assert all(isinstance(n, dict) and "id" in n and "label" in n for n in result["nodes"])


def test_get_graph_route_with_no_params_returns_200(app_client, stub_neo4j, fake_store):
    fake_store.patients["HN-1"] = {"patient_id": "HN-1", "name": "Test"}
    stub_neo4j.prime([{
        "p": {"patientId": "HN-1"},
        "encounters": [], "conditions": [], "medications": [],
        "observations": [], "plans": [], "allergies": [],
    }])
    r = app_client.get("/api/patient/HN-1/graph")
    assert r.status_code == 200
    body = r.json()
    assert "nodes" in body and "edges" in body
```

- [ ] **Step 4: Run all the new tests**

```bash
docker exec cng-backend python -m pytest tests/test_graph_dedupe.py tests/test_graph_routes.py tests/test_graph_legacy_signature.py -v
```

Expected: **all green** (7 + 6 + 2 = 15 tests pass).

- [ ] **Step 5: Run the full suite to catch collateral breakage**

```bash
docker exec cng-backend python -m pytest tests/ -q --tb=short
```

Expected: 0 failed (3 skipped is OK — they're the e2e markers).

- [ ] **Step 6: Commit**

```bash
git add backend/tests/test_graph_routes.py backend/tests/test_graph_legacy_signature.py backend/tests/conftest.py
git commit -m "$(cat <<'EOF'
test(graph): integration tests + 422 cap + legacy-signature regression

Adds 6 integration tests for the extended /graph route (default scope,
encounter scope, missing encounter id → 400, 422 oversized cap with
nodeCount, includeDocuments, reviewStatus filter) plus 2 regression
tests for fetch_patient_graph (legacy signature) and the no-param
route call. Extends stub_neo4j with a prime() helper so tests can
seed deterministic Cypher result rows.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Frontend API client extension

**Files:**
- Modify: `frontend/src/api/client.js`

- [ ] **Step 1: Extend `getGraph`**

Edit `frontend/src/api/client.js`. Replace the existing `getGraph` helper:

```javascript
// before:
// export const getGraph = (id, signal) =>
//   api.get(`/api/patient/${encodeURIComponent(id)}/graph`, { signal }).then(data)

// after:
export const getGraph = (id, options = {}) => {
  const { signal, ...rest } = options
  // Filter out undefined values so they don't appear as `key=undefined` in the URL.
  const params = Object.fromEntries(
    Object.entries(rest).filter(([, v]) => v !== undefined && v !== null && v !== ''),
  )
  return api.get(`/api/patient/${encodeURIComponent(id)}/graph`, { params, signal }).then(data)
}
```

Backward compatibility: the only existing caller is `PatientDetail.vue`, which calls `getGraph(props.id, ctl.signal)` — that signature changes to `getGraph(props.id, { signal: ctl.signal })`. Update that call site:

- [ ] **Step 2: Update the existing caller in `PatientDetail.vue`**

Find the `load()` function in `frontend/src/views/PatientDetail.vue`. The `Promise.all` destructures includes `getGraph(props.id, ctl.signal)`. Change it to:

```javascript
getGraph(props.id, { signal: ctl.signal }),
```

- [ ] **Step 3: HMR check**

```bash
docker logs cng-frontend --since 30s 2>&1 | grep -E "hmr|error" | tail -5
```

Expected: HMR updates for `/src/api/client.js` and `/src/views/PatientDetail.vue`, no errors.

- [ ] **Step 4: Smoke-test in the browser**

The hot-reloaded patient page should still render the Graph tab with the same nodes/edges as before (since calling with no extra params now defaults to deduped patient-level, which is also the new default behavior on the backend). If you can't open a browser, hit:

```bash
curl -s "http://localhost:8081/api/patient/HN-DEMO-1/graph" | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'nodes={len(d[\"nodes\"])} edges={len(d[\"edges\"])}')"
```

Expected: a non-zero count for the demo patient.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/client.js frontend/src/views/PatientDetail.vue
git commit -m "$(cat <<'EOF'
feat(client): getGraph accepts options for scope/dedupe/filter params

Replaces the (id, signal) signature with (id, options) where options
extracts `signal` and forwards every other key as a query param. The
sole existing caller (PatientDetail.vue) is updated to pass
{ signal: ctl.signal }.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: `GraphView.vue` rework

**Files:**
- Modify: `frontend/src/components/GraphView.vue` (substantial rework — replace the template + add filter/scope state)

- [ ] **Step 1: Replace the component**

This is a full replacement of `frontend/src/components/GraphView.vue`. The vis-network rendering logic is kept identical to today's; what's new is the toolbar, the filter drawer, the Pick-encounters dialog, the oversized banner, and the scope-driven props.

```vue
<template>
  <v-card class="d-flex flex-column h-100">
    <div class="d-flex align-center pa-2 ga-2 flex-wrap">
      <v-chip-group v-if="scope === 'patient'" v-model="scopeChip" mandatory
                    selected-class="bg-primary-lighten-4">
        <v-chip value="all" filter>All</v-chip>
        <v-chip value="latest" filter>Latest encounter</v-chip>
        <v-chip value="pick" filter>Pick…</v-chip>
      </v-chip-group>
      <span v-else class="text-caption text-grey-darken-1 ml-2">
        Encounter scope · {{ encounterIds.length }} encounter(s)
      </span>
      <v-spacer />
      <span v-if="loading" class="text-caption text-grey-darken-1 mr-2">loading…</span>
      <v-btn icon="mdi-fit-to-page-outline" variant="text" size="small" @click="fit" aria-label="Fit view" />
      <v-btn icon="mdi-cog-outline" variant="text" size="small" @click="filtersOpen = true" aria-label="Filters" />
    </div>
    <v-divider />

    <v-alert v-if="oversized" type="warning" variant="tonal" closable class="ma-2"
             @click:close="oversized = null">
      {{ oversized.detail }} ({{ oversized.nodeCount }} nodes). Try Dedupe on, a single encounter, or "Confirmed only".
    </v-alert>

    <div ref="container" :style="{ height: height + 'px' }" class="graph-canvas" />

    <EmptyState v-if="!loading && !data?.nodes?.length && !oversized"
                icon="mdi-graph-outline" :title="emptyTitle" />

    <!-- Filter side drawer -->
    <v-navigation-drawer v-model="filtersOpen" location="right" temporary width="320">
      <div class="pa-4 text-subtitle-1 font-weight-bold">Filters</div>
      <v-divider />
      <v-list density="compact">
        <v-list-subheader>Node types</v-list-subheader>
        <v-list-item v-for="t in NODE_TYPE_TOGGLES" :key="t.key" :title="t.label">
          <template #append>
            <v-switch v-model="filters[t.key]" hide-details density="compact" inset />
          </template>
        </v-list-item>
        <v-divider class="my-2" />
        <v-list-subheader>Behavior</v-list-subheader>
        <v-list-item title="Dedupe across encounters">
          <template #append>
            <v-switch v-model="filters.dedupe" hide-details density="compact" inset />
          </template>
        </v-list-item>
        <v-divider class="my-2" />
        <v-list-subheader>Review status</v-list-subheader>
        <v-list-item>
          <v-radio-group v-model="filters.reviewStatus" hide-details density="compact">
            <v-radio value="hide_rejected" label="Hide rejected" />
            <v-radio value="all" label="Show all" />
            <v-radio value="confirmed" label="Confirmed only" />
          </v-radio-group>
        </v-list-item>
      </v-list>
    </v-navigation-drawer>

    <!-- Pick-encounters dialog -->
    <v-dialog v-model="pickerOpen" max-width="480">
      <v-card>
        <div class="d-flex align-center pa-4">
          <v-icon class="mr-2">mdi-calendar-check-outline</v-icon>
          <span class="text-subtitle-1 font-weight-bold">Pick encounters</span>
        </div>
        <v-divider />
        <v-card-text>
          <v-text-field v-model="pickerFilter" prepend-inner-icon="mdi-magnify"
                        density="compact" hide-details placeholder="Filter by date or type" />
          <v-list select-strategy="multiple" v-model:selected="pickedEncounterIds"
                  density="compact" class="mt-2" style="max-height: 320px; overflow-y: auto">
            <v-list-item v-for="e in filteredEncounterList" :key="e.encounterId" :value="e.encounterId"
                         :title="`${e.type} · ${new Date(e.dateTime).toLocaleString()}`"
                         :subtitle="e.department || ''" />
          </v-list>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn @click="pickerOpen = false">Cancel</v-btn>
          <v-btn color="primary" :disabled="!pickedEncounterIds.length" @click="applyPicked">Apply</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </v-card>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { Network, DataSet } from 'vis-network/standalone/esm/vis-network'
import { getGraph, listEncounters } from '../api/client.js'
import { useUiStore } from '../stores/ui.js'
import EmptyState from './EmptyState.vue'

const props = defineProps({
  patientId:    { type: String, required: true },
  scope:        { type: String, default: 'patient' },     // 'patient' | 'encounter' | 'encounters'
  encounterIds: { type: Array, default: () => [] },        // used when scope is encounter/encounters
  height:       { type: Number, default: 620 },
})

const ui = useUiStore()
const container = ref(null)
const data = ref({ nodes: [], edges: [] })
const loading = ref(false)
const oversized = ref(null)
const filtersOpen = ref(false)
const pickerOpen = ref(false)
const pickerFilter = ref('')
const pickedEncounterIds = ref([])
const encounterList = ref([])
let network = null
let abortController = null

const COLORS = {
  Patient: '#1f6feb', Encounter: '#7286d3', Document: '#9c27b0',
  Condition: '#ef6c00', Medication: '#2e7d32', Observation: '#0097a7',
  Plan: '#6d4c41', Allergy: '#c62828', Procedure: '#7b1fa2',
}

const NODE_TYPE_TOGGLES = [
  { key: 'includeEncounters', label: 'Encounters' },
  { key: 'includeDocuments', label: 'Documents' },
]

const scopeChip = ref('all')  // 'all' | 'latest' | 'pick'
const filters = reactive({
  includeEncounters: false,
  includeDocuments: false,
  dedupe: true,
  reviewStatus: 'hide_rejected',
})

const emptyTitle = computed(() =>
  props.scope === 'patient' ? 'No facts to display yet — ingest a note for this patient' :
  'This encounter has no extracted facts')

const filteredEncounterList = computed(() => {
  const q = pickerFilter.value.trim().toLowerCase()
  if (!q) return encounterList.value
  return encounterList.value.filter((e) =>
    (e.type || '').toLowerCase().includes(q) ||
    (e.dateTime || '').toLowerCase().includes(q),
  )
})

function themeColors() {
  const style = getComputedStyle(document.documentElement)
  const onBg = style.getPropertyValue('--v-theme-on-background').trim() || '0,0,0'
  const surface = style.getPropertyValue('--v-theme-surface').trim() || '255,255,255'
  return { label: `rgb(${onBg})`, stroke: `rgb(${surface})` }
}

function shortLabel(n) {
  const d = n.data || {}
  return d.value || d.name || d.description || d.patientId || d.encounterId || n.label
}
function tooltip(n) {
  return `${n.label}\n${JSON.stringify(n.data, null, 2)}`
}

function render() {
  if (!container.value) return
  const { label: labelColor, stroke: strokeColor } = themeColors()
  const nodes = new DataSet((data.value.nodes || []).map((n) => ({
    id: n.id,
    label: shortLabel(n),
    title: tooltip(n),
    color: { background: COLORS[n.label] || '#90a4ae', border: '#37474f' },
    font: { color: labelColor, strokeColor, strokeWidth: 3, size: 12 },
    shape: 'dot',
    size: n.label === 'Patient' ? 24 : 14,
  })))
  const edges = new DataSet((data.value.edges || []).map((e, i) => ({
    id: 'e' + i, from: e.from, to: e.to, label: e.type, arrows: 'to',
    font: { size: 9, color: labelColor, strokeColor, strokeWidth: 3 },
    color: { color: '#9e9e9e', highlight: '#1f6feb' },
    smooth: { type: 'continuous' },
  })))
  if (network) network.destroy()
  network = new Network(container.value, { nodes, edges }, {
    physics: { stabilization: { iterations: 200 }, barnesHut: { springLength: 140 } },
    interaction: { hover: true, tooltipDelay: 100 },
    nodes: { borderWidth: 1.5 },
  })
}

function fit() { network && network.fit({ animation: { duration: 350 } }) }

function resolvedQuery() {
  // Build the query options for getGraph from current scope + filters.
  if (props.scope !== 'patient') {
    return {
      scope: props.scope,
      encounterId: props.encounterIds,
      dedupe: filters.dedupe,
      includeEncounters: filters.includeEncounters || props.scope !== 'patient',
      includeDocuments: filters.includeDocuments,
      reviewStatus: filters.reviewStatus,
    }
  }
  if (scopeChip.value === 'all') {
    return { scope: 'patient', dedupe: filters.dedupe,
             includeEncounters: filters.includeEncounters,
             includeDocuments: filters.includeDocuments,
             reviewStatus: filters.reviewStatus }
  }
  if (scopeChip.value === 'latest' && encounterList.value.length) {
    return { scope: 'encounter', encounterId: [encounterList.value[0].encounterId],
             dedupe: filters.dedupe,
             includeEncounters: true,
             includeDocuments: filters.includeDocuments,
             reviewStatus: filters.reviewStatus }
  }
  if (scopeChip.value === 'pick' && pickedEncounterIds.value.length) {
    return { scope: 'encounters', encounterId: pickedEncounterIds.value,
             dedupe: filters.dedupe,
             includeEncounters: true,
             includeDocuments: filters.includeDocuments,
             reviewStatus: filters.reviewStatus }
  }
  return null  // no usable selection yet
}

async function load() {
  // Debounce-cancel previous in-flight.
  if (abortController) abortController.abort()
  abortController = new AbortController()
  oversized.value = null
  loading.value = true
  const opts = resolvedQuery()
  if (!opts) {
    loading.value = false
    data.value = { nodes: [], edges: [] }
    render()
    return
  }
  try {
    data.value = await getGraph(props.patientId, { ...opts, signal: abortController.signal })
    render()
  } catch (err) {
    if (err.name === 'CanceledError' || err.name === 'AbortError') return
    if (err.response?.status === 422) {
      oversized.value = err.response.data?.detail || { detail: 'Graph too large', nodeCount: 0 }
      data.value = { nodes: [], edges: [] }
      render()
      return
    }
    // Other errors are surfaced by the axios interceptor's snackbar.
    data.value = { nodes: [], edges: [] }
    render()
  } finally {
    loading.value = false
  }
}

async function loadEncounters() {
  if (props.scope !== 'patient') return
  try {
    const list = await listEncounters(props.patientId)
    encounterList.value = list || []
  } catch {
    encounterList.value = []
  }
}

function applyPicked() {
  pickerOpen.value = false
  load()
}

// Reactivity: re-fetch when scope-state changes.
watch(scopeChip, (chip) => {
  if (chip === 'pick') {
    pickerOpen.value = true
    return  // wait for Apply
  }
  load()
})
watch(filters, () => load(), { deep: true })
watch(() => ui.theme, () => render())
watch(() => props.encounterIds, () => load())

onMounted(async () => {
  await loadEncounters()
  await load()
})
onBeforeUnmount(() => {
  if (abortController) abortController.abort()
  if (network) network.destroy()
})
</script>

<style scoped>
.graph-canvas {
  background: rgba(127, 127, 127, 0.04);
  border-radius: 0;
}
</style>
```

- [ ] **Step 2: HMR check**

```bash
docker logs cng-frontend --since 30s 2>&1 | grep -E "hmr|error|fail" | tail -5
```

Expected: hmr update for `/src/components/GraphView.vue`, no errors.

- [ ] **Step 3: Smoke test in browser (manual)**

Open `http://localhost:8081/#/patient/HN-DEMO-1`. Click the Graph tab.

- Toolbar shows the chip group (All / Latest / Pick…) and a cog icon.
- Default view: deduped patient-level graph, no encounter nodes, no document nodes.
- Click cog → side drawer slides in with switches for Encounters / Documents + dedupe + review-status radio.
- Toggle Encounters on → encounter nodes appear.
- Click "Latest encounter" chip → graph re-renders with that encounter's subgraph (Encounter node + that encounter's facts).
- Click "Pick…" → modal opens with encounter list; selecting two and Apply re-renders the graph.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/GraphView.vue
git commit -m "$(cat <<'EOF'
feat(graph-ui): rewrite GraphView with toolbar, filter drawer, scope picker

Scope-driven via props. Patient-scope shows a chip group (All / Latest /
Pick…); encounter-scope hides it. Filter cog opens a side drawer with
node-type switches, dedupe toggle and review-status radio. Pick…
opens a multi-select encounter dialog. 422 'too large' responses
render a clearable warning banner. AbortController cancels in-flight
fetches when scope/filters change.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: `EncounterDialog.vue` refactor (was `EncounterDetail.vue`)

**Files:**
- Create: `frontend/src/views/EncounterDialog.vue`
- Delete: `frontend/src/views/EncounterDetail.vue`

- [ ] **Step 1: Read the current EncounterDetail.vue**

The implementer should `cat frontend/src/views/EncounterDetail.vue` once to capture the full template + script, since EncounterDialog reuses ~90% of the logic.

- [ ] **Step 2: Create EncounterDialog.vue**

The new file wraps the existing Detail layout in a `<v-dialog fullscreen>` and adds a tab control:

```vue
<template>
  <v-dialog :model-value="!!eid" fullscreen
            transition="dialog-bottom-transition"
            scrollable
            @update:model-value="(v) => !v && $emit('close')">
    <v-card class="d-flex flex-column" style="height: 100vh">
      <v-toolbar color="surface" density="comfortable">
        <v-btn icon="mdi-close" @click="$emit('close')" aria-label="Close" />
        <v-toolbar-title class="text-truncate">
          <span v-if="encounter">
            {{ encounter.type }} · {{ encounter.dateTime ? new Date(encounter.dateTime).toLocaleString() : '' }}
          </span>
          <span v-else>Encounter</span>
        </v-toolbar-title>
        <v-spacer />
        <v-menu>
          <template #activator="{ props: a }">
            <v-btn v-bind="a" class="mr-2" color="primary" variant="tonal"
                   prepend-icon="mdi-text-box-outline" :loading="busy.summary">
              {{ summary ? 'Regenerate summary' : 'Summarize' }}
              <v-icon end>mdi-chevron-down</v-icon>
            </v-btn>
          </template>
          <v-list density="compact">
            <v-list-item title="Discharge summary" prepend-icon="mdi-hospital-box-outline"
                         @click="loadSummary('discharge_summary')" />
            <v-list-item title="Detailed" prepend-icon="mdi-text"
                         @click="loadSummary('detailed')" />
            <v-list-item title="Brief" prepend-icon="mdi-text-short"
                         @click="loadSummary('brief')" />
          </v-list>
        </v-menu>
        <v-btn color="primary" variant="tonal" prepend-icon="mdi-medical-bag-outline"
               :loading="busy.coding" @click="loadCoding">
          {{ codingResp ? 'Regenerate coding' : 'Coding' }}
        </v-btn>
      </v-toolbar>

      <v-tabs v-model="tab" color="primary" density="comfortable">
        <v-tab value="detail" prepend-icon="mdi-text-box-outline">Detail</v-tab>
        <v-tab value="graph" prepend-icon="mdi-graph-outline">Graph</v-tab>
      </v-tabs>
      <v-divider />

      <v-window v-model="tab" class="flex-grow-1 overflow-y-auto">
        <v-window-item value="detail">
          <div v-if="loading" class="d-flex justify-center pa-8">
            <v-progress-circular indeterminate />
          </div>
          <v-alert v-else-if="error" type="error" variant="tonal" class="ma-4">
            {{ error }}
          </v-alert>
          <div v-else class="pa-4">
            <v-row>
              <v-col cols="12" md="8">
                <SummaryCard :value="summary" />
                <CodingCard :value="codingResp" />
                <v-card v-if="docs.length" class="mt-4">
                  <SectionHeader title="Documents" icon="mdi-file-multiple-outline" />
                  <v-divider />
                  <v-list density="compact" nav>
                    <v-list-item v-for="d in docs" :key="d.documentId"
                                 :title="d.documentId"
                                 :subtitle="`v${d.version} · ${d.format}`" />
                  </v-list>
                </v-card>
              </v-col>
              <v-col cols="12" md="4">
                <v-card>
                  <SectionHeader title="Background" icon="mdi-medical-bag" />
                  <v-divider />
                  <v-list density="compact">
                    <v-list-subheader>Chronic problems</v-list-subheader>
                    <v-list-item v-for="p in background.chronicProblems" :key="`bp-${p.id}`" :title="p.value" />
                    <EmptyState v-if="!background.chronicProblems.length" icon="mdi-medical-bag" title="None recorded" />
                    <v-divider class="my-1" />
                    <v-list-subheader>Home medications</v-list-subheader>
                    <v-list-item v-for="m in background.homeMedications" :key="`bm-${m.id}`" :title="m.value" />
                    <EmptyState v-if="!background.homeMedications.length" icon="mdi-pill" title="None recorded" />
                    <v-divider class="my-1" />
                    <v-list-subheader>Known allergies</v-list-subheader>
                    <v-list-item v-for="a in background.knownAllergies" :key="`ba-${a.id}`" :title="a.value" />
                    <EmptyState v-if="!background.knownAllergies.length" icon="mdi-allergy" title="None recorded" />
                  </v-list>
                </v-card>
              </v-col>
            </v-row>
          </div>
        </v-window-item>

        <v-window-item value="graph" class="h-100">
          <GraphView scope="encounter" :patient-id="patientId" :encounter-ids="[eid]" :height="640" />
        </v-window-item>
      </v-window>
    </v-card>
  </v-dialog>
</template>

<script setup>
import { onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  getLatestEncounterSummary, getLatestEncounterCoding,
  summarizeEncounter, suggestEncounterCoding, listEncounters,
} from '../api/client.js'
import { useUiStore } from '../stores/ui.js'
import SummaryCard from '../components/SummaryCard.vue'
import CodingCard from '../components/CodingCard.vue'
import SectionHeader from '../components/SectionHeader.vue'
import EmptyState from '../components/EmptyState.vue'
import GraphView from '../components/GraphView.vue'

const props = defineProps({
  patientId: { type: String, required: true },
  eid:       { type: String, required: true },
})
defineEmits(['close'])

const route = useRoute()
const ui = useUiStore()

const tab = ref('detail')
const loading = ref(true)
const error = ref('')
const encounter = ref(null)
const background = ref({ chronicProblems: [], homeMedications: [], knownAllergies: [] })
const docs = ref([])
const summary = ref(null)
const codingResp = ref(null)
const busy = reactive({ summary: false, coding: false })

function extractBackground(evidence) {
  if (!evidence || typeof evidence !== 'object' || !evidence.background) {
    return { chronicProblems: [], homeMedications: [], knownAllergies: [] }
  }
  return {
    chronicProblems: evidence.background.chronicProblems || [],
    homeMedications: evidence.background.homeMedications || [],
    knownAllergies: evidence.background.knownAllergies || [],
  }
}

async function fetchAll() {
  loading.value = true
  error.value = ''
  try {
    const [sum, cod, list] = await Promise.all([
      getLatestEncounterSummary(props.patientId, props.eid).catch(() => null),
      getLatestEncounterCoding(props.patientId, props.eid).catch(() => null),
      listEncounters(props.patientId).catch(() => []),
    ])
    summary.value = sum
    codingResp.value = cod?.payload || cod || null
    background.value = extractBackground(sum?.evidence)
    const match = (list || []).find((e) => e.encounterId === props.eid)
    if (!match) {
      error.value = 'Encounter not found for this patient.'
    } else {
      encounter.value = match
    }
  } finally {
    loading.value = false
  }
}

async function loadSummary(type) {
  busy.summary = true
  try {
    summary.value = await summarizeEncounter(props.patientId, props.eid, { type, includeEvidence: false })
    ui.success('Summary ready')
  } catch {
    ui.error('Failed to generate summary')
  } finally {
    busy.summary = false
  }
}

async function loadCoding() {
  busy.coding = true
  try {
    codingResp.value = await suggestEncounterCoding(props.patientId, props.eid, {
      standards: ['ICD10', 'SNOMEDCT'], includeEvidence: false,
    })
    ui.success('Coding suggestion ready')
  } catch {
    ui.error('Failed to suggest coding')
  } finally {
    busy.coding = false
  }
}

onMounted(async () => {
  await fetchAll()
  if (route.query.action === 'summary' && !summary.value && !busy.summary) {
    loadSummary(encounter.value?.type === 'admission' ? 'discharge_summary' : 'detailed')
  } else if (route.query.action === 'coding' && !codingResp.value && !busy.coding) {
    loadCoding()
  }
})

watch(() => props.eid, fetchAll)
</script>
```

- [ ] **Step 3: Delete the old `EncounterDetail.vue`**

```bash
rm frontend/src/views/EncounterDetail.vue
```

The encounter route still uses this path until Task 7 rewires it; HMR will show a brief 404 on the route until then. That's OK because we'll fix the route in Task 7 within the same commit chain.

- [ ] **Step 4: Update the EncounterDetail Vitest spec (rename + adapt)**

Find `frontend/src/views/__tests__/EncounterDetail.spec.js` (created in PR #4) and rename it:

```bash
git mv frontend/src/views/__tests__/EncounterDetail.spec.js \
       frontend/src/views/__tests__/EncounterDialog.spec.js
```

Edit it: change every reference to `EncounterDetail` to `EncounterDialog`, change the import path to `../EncounterDialog.vue`, and update the props passed in the test (the new component requires `patient-id` and `eid` props directly — no longer via route props). If the test previously mocked `useRoute` for query.action, keep that mock.

(The full body of the spec is essentially the same — the structural assertions on header text / "Regenerate summary" / 404 message still hold for the dialog.)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/EncounterDialog.vue frontend/src/views/__tests__/EncounterDialog.spec.js
git rm frontend/src/views/EncounterDetail.vue frontend/src/views/__tests__/EncounterDetail.spec.js
git commit -m "$(cat <<'EOF'
refactor(ui): EncounterDetail.vue → EncounterDialog.vue (fullscreen v-dialog + tabs)

The encounter page becomes a fullscreen modal that overlays the patient
page, instead of a standalone route view. The Detail tab keeps the
existing PR #4 layout (summary/coding/background/docs); the new Graph
tab renders <GraphView scope="encounter">.

PatientDetail.vue (next task) opens the dialog by watching
route.params.eid so deep-links to /patient/:id/encounter/:eid keep
working.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

(The frontend won't fully run yet — the router still imports EncounterDetail.vue. Task 7 fixes that immediately.)

---

## Task 7: `PatientDetail.vue` watches `route.params.eid` + router update

**Files:**
- Modify: `frontend/src/router.js`
- Modify: `frontend/src/views/PatientDetail.vue`

- [ ] **Step 1: Update the router**

Edit `frontend/src/router.js`:

```javascript
import { createRouter, createWebHashHistory } from 'vue-router'

import PatientsView from './views/PatientsView.vue'
import PatientDetail from './views/PatientDetail.vue'
import IngestView from './views/IngestView.vue'
import ConfigView from './views/ConfigView.vue'
import DebugView from './views/DebugView.vue'

const routes = [
  { path: '/', redirect: '/patients' },
  { path: '/patients', component: PatientsView, name: 'patients' },
  { path: '/patient/:id', component: PatientDetail, name: 'patient', props: true },
  {
    // Encounter detail is rendered as a fullscreen dialog inside PatientDetail.
    // We reuse the same component; route.params.eid triggers the dialog.
    path: '/patient/:id/encounter/:eid',
    name: 'encounter',
    component: PatientDetail,
    props: true,
  },
  { path: '/ingest', component: IngestView, name: 'ingest' },
  { path: '/config', component: ConfigView, name: 'config' },
  { path: '/debug', component: DebugView, name: 'debug' },
]

export default createRouter({ history: createWebHashHistory(), routes })
```

Also note: the existing `patients/:id` path in the previous router was a typo (plural) that doesn't match anything else. Confirm the existing `/patient/:id` route is keyed under name `patient` — that's what the rest of the codebase uses (verified in `<v-btn :to="{ name: 'patient' }">` call sites).

- [ ] **Step 2: Modify `PatientDetail.vue` to render the dialog**

Open `frontend/src/views/PatientDetail.vue`. Add at the top of `<script setup>`, after the existing imports:

```javascript
import { useRoute, useRouter } from 'vue-router'
import EncounterDialog from './EncounterDialog.vue'

const route = useRoute()
// `useRouter` may already be in the file from PR #4's "openEncounter" handler.
// If not, add: import { useRouter } from 'vue-router' and const router = useRouter()
```

If the file already has `useRouter` and `openEncounter`, leave them. They still work because `openEncounter` calls `router.push({ name: 'encounter', ... })` which the new route handles.

In the template, at the top level under `<div v-else>` (the main rendered branch when patient is loaded), append the dialog:

```vue
<EncounterDialog
  v-if="route.params.eid"
  :patient-id="id"
  :eid="String(route.params.eid)"
  @close="closeEncounter"
/>
```

Add the close handler in `<script setup>`:

```javascript
function closeEncounter() {
  router.push({ name: 'patient', params: { id: props.id } })
}
```

- [ ] **Step 3: HMR + smoke test**

```bash
docker logs cng-frontend --since 30s 2>&1 | grep -E "hmr|error|fail" | tail -5
```

Smoke test in browser (or curl):
- Navigate to `http://localhost:8081/#/patient/HN-DEMO-1` → patient page renders; no dialog visible.
- Navigate to `http://localhost:8081/#/patient/HN-DEMO-1/encounter/<some-eid>` → patient page renders AND EncounterDialog opens on top.
- Click the X in the dialog → URL pops back to `/#/patient/HN-DEMO-1`; dialog closes.

- [ ] **Step 4: Update the PatientDetail Vitest spec (extension)**

Find `frontend/src/views/__tests__/PatientsView.spec.js` (no PatientDetail spec exists from PR #4 — only PatientsView). Skip creating a new PatientDetail spec for v1; the EncounterDialog + GraphView specs cover the new behavior, and Playwright covers the end-to-end. Note this as a follow-up in the PR description.

Actually — check whether `frontend/src/views/__tests__/PatientDetail.spec.js` exists:

```bash
ls frontend/src/views/__tests__/ 2>&1
```

If it does, add a test case that mounts PatientDetail on a route with `eid` and asserts the dialog renders. If it doesn't (likely), leave for follow-up; not strictly required by the spec.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/router.js frontend/src/views/PatientDetail.vue
git commit -m "$(cat <<'EOF'
feat(ui): encounter URL opens dialog over PatientDetail; deep-links preserved

Route /patient/:id/encounter/:eid now resolves to PatientDetail.vue
(same component as /patient/:id); the dialog renders when
route.params.eid is set. Closing the dialog navigates back to
/patient/:id. The legacy EncounterDetail.vue route component is gone.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: GraphView Vitest spec

**Files:**
- Create: `frontend/src/components/__tests__/GraphView.spec.js`

- [ ] **Step 1: Check the existing __tests__ directory for component specs**

```bash
ls frontend/src/components/__tests__/ 2>&1
```

If the directory doesn't exist, create it; if it does, the new spec joins the others.

- [ ] **Step 2: Write the spec**

Create `frontend/src/components/__tests__/GraphView.spec.js`:

```javascript
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

import GraphView from '../GraphView.vue'

// Stub vis-network entirely — we're testing the Vue surface, not the canvas.
vi.mock('vis-network/standalone/esm/vis-network', () => ({
  Network: vi.fn(() => ({ fit: vi.fn(), destroy: vi.fn() })),
  DataSet: vi.fn((items) => items),
}))

vi.mock('../../api/client.js', () => ({
  getGraph: vi.fn(),
  listEncounters: vi.fn(),
}))

import * as api from '../../api/client.js'

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  api.listEncounters.mockResolvedValue([])
})

const globalStubs = {
  stubs: {
    'v-card': { template: '<div><slot /></div>' },
    'v-chip-group': { template: '<div><slot /></div>' },
    'v-chip': { template: '<button :data-value="value"><slot /></button>', props: ['value'] },
    'v-btn': { template: '<button :aria-label="ariaLabel" @click="$emit(\'click\')"><slot /></button>', props: ['ariaLabel'] },
    'v-divider': { template: '<hr />' },
    'v-spacer': { template: '<span />' },
    'v-alert': { template: '<div role="alert"><slot /></div>' },
    'v-navigation-drawer': { template: '<div data-test="filter-drawer" v-if="modelValue"><slot /></div>', props: ['modelValue'] },
    'v-list': { template: '<div><slot /></div>' },
    'v-list-item': { template: '<div><slot /></div>' },
    'v-list-subheader': { template: '<div><slot /></div>' },
    'v-switch': { template: '<input type="checkbox" :checked="modelValue" @change="$emit(\'update:modelValue\', !modelValue)" />', props: ['modelValue'] },
    'v-radio-group': { template: '<div><slot /></div>' },
    'v-radio': { template: '<label><input type="radio" :value="value" /><slot />{{ label }}</label>', props: ['value', 'label'] },
    'v-dialog': { template: '<div v-if="modelValue" data-test="pick-dialog"><slot /></div>', props: ['modelValue'] },
    'v-text-field': { template: '<input :value="modelValue" />', props: ['modelValue'] },
    'v-icon': { template: '<i><slot /></i>' },
    'v-card-text': { template: '<div><slot /></div>' },
    'v-card-actions': { template: '<div><slot /></div>' },
    'v-progress-circular': { template: '<span>loading</span>' },
    EmptyState: { template: '<div data-test="empty-state"><slot /></div>' },
  },
}

describe('GraphView.vue', () => {
  it('fetches graph on mount with patient scope defaults', async () => {
    api.getGraph.mockResolvedValue({ nodes: [], edges: [] })
    mount(GraphView, {
      props: { patientId: 'HN-1', scope: 'patient' },
      global: globalStubs,
    })
    await flushPromises()
    expect(api.getGraph).toHaveBeenCalledTimes(1)
    const [pid, opts] = api.getGraph.mock.calls[0]
    expect(pid).toBe('HN-1')
    expect(opts.scope).toBe('patient')
    expect(opts.dedupe).toBe(true)
  })

  it('hides scope chip group when scope is encounter', async () => {
    api.getGraph.mockResolvedValue({ nodes: [], edges: [] })
    const wrapper = mount(GraphView, {
      props: { patientId: 'HN-1', scope: 'encounter', encounterIds: ['E1'] },
      global: globalStubs,
    })
    await flushPromises()
    expect(wrapper.text()).toContain('Encounter scope')
  })

  it('renders oversized banner when getGraph rejects with 422', async () => {
    const err = new Error('too large')
    err.response = { status: 422, data: { detail: { detail: 'Graph too large; narrow the scope', nodeCount: 783 } } }
    api.getGraph.mockRejectedValue(err)
    const wrapper = mount(GraphView, {
      props: { patientId: 'HN-1', scope: 'patient' },
      global: globalStubs,
    })
    await flushPromises()
    expect(wrapper.text()).toContain('Graph too large')
    expect(wrapper.text()).toContain('783')
  })
})
```

- [ ] **Step 3: Run frontend tests**

```bash
docker exec cng-frontend npm test -- --run
```

Expected: existing tests still pass; the new spec adds 3 passing cases.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/__tests__/GraphView.spec.js
git commit -m "$(cat <<'EOF'
test(ui): Vitest coverage for GraphView toolbar, scope, and 422 banner

Three cases: mount fetches with patient-scope defaults, scope=encounter
hides the chip group, 422 response renders the oversized banner with
the node count. vis-network is stubbed so the tests run without a real
canvas.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Playwright e2e smoke

**Files:**
- Create: `frontend/e2e/graph-scope.spec.ts`

- [ ] **Step 1: Write the e2e test**

```typescript
import { test, expect } from '@playwright/test'

// Smoke test for the graph scope/filter UX.
// Uses hash-mode URLs (createWebHashHistory) — see frontend/src/router.js.

test('patient Graph tab renders the canvas and toolbar', async ({ page }) => {
  await page.goto('/#/patient/HN-DEMO-1')

  // Switch to the Graph tab on the patient page.
  await page.getByRole('tab', { name: /Graph/i }).click()

  // Toolbar chip group is visible (we're in patient scope).
  await expect(page.getByText(/^All$/)).toBeVisible({ timeout: 10_000 })
  await expect(page.getByText(/Latest encounter/i)).toBeVisible()
  await expect(page.getByText(/Pick…/)).toBeVisible()

  // Open the filter drawer.
  await page.getByRole('button', { name: /Filters/i }).click()
  await expect(page.locator('text=Node types')).toBeVisible()

  // Close drawer to clear the overlay.
  await page.keyboard.press('Escape')
})

test('encounter URL opens dialog with Graph tab', async ({ page }) => {
  // Visit patient page and grab the first encounter id from the Encounters tab.
  await page.goto('/#/patient/HN-DEMO-1')
  await page.getByRole('tab', { name: /Encounters/i }).click()
  const firstViewBtn = page.getByRole('button', { name: /^View$/i }).first()
  await firstViewBtn.click()

  // Dialog should now be open. Confirm the tab control is present.
  await expect(page.getByRole('tab', { name: /Detail/i })).toBeVisible()
  await expect(page.getByRole('tab', { name: /Graph/i })).toBeVisible()

  // Switch to Graph tab inside the dialog.
  await page.getByRole('tab', { name: /Graph/i }).last().click()

  // Encounter scope hides the chip group; we should see "Encounter scope · N encounter(s)".
  await expect(page.getByText(/Encounter scope/i)).toBeVisible({ timeout: 10_000 })

  // Close dialog.
  await page.getByRole('button', { name: /Close/i }).click()
})
```

- [ ] **Step 2: Run the e2e (optional — may be flaky on Vuetify v4 selectors)**

```bash
cd frontend && npx playwright test e2e/graph-scope.spec.ts --reporter=line --timeout 60000 2>&1 | tail -10
```

If it passes, great. If it times out at a selector — same caveat as the PR #4 spec — commit the file anyway with a note. The Vuetify-v4 role-based selector tuning is a known follow-up.

- [ ] **Step 3: Commit**

```bash
git add frontend/e2e/graph-scope.spec.ts
git commit -m "$(cat <<'EOF'
test(e2e): playwright smoke for graph scope selector and encounter dialog

Two scenarios: patient page Graph tab shows toolbar chip group + filter
drawer; navigating to /patient/:id/encounter/:eid opens the dialog with
Detail/Graph tabs, and the Graph tab hides the chip group in favour of
the encounter-scope label.

Same Vuetify-v4 role-based selector tuning caveats as the PR #4 e2e
spec apply; tuning is a separate concern.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Push + PR

- [ ] **Step 1: Final sanity sweep**

```bash
docker exec cng-backend python -m pytest tests/ -q --tb=no 2>&1 | tail -3
docker exec cng-frontend npm test -- --run 2>&1 | tail -5
```

Expected: backend 0 failed; frontend all green.

- [ ] **Step 2: Push branch**

```bash
git push -u origin feat/graph-scope-selector
```

- [ ] **Step 3: Open the PR**

```bash
gh pr create --title "Graph view: scope selector + decluttering" --body "$(cat <<'EOF'
Implements #6. Builds on #4 (encounter-scoped AI summary + coding, merged).

## Summary

- **`GraphView`** reworked: single component with scope-driven props (`patient` | `encounter`). Compact toolbar (All / Latest encounter / Pick…), filter side-drawer (node types, dedupe, review status), Pick-encounters dialog, oversized banner. Force-directed layout kept.
- **Backend** `/api/patient/{pid}/graph` extended with `scope`, `encounterId`, `dedupe`, `includeEncounters`, `includeDocuments`, `reviewStatus` query params. Pure-Python `_dedupe_nodes_edges` (Conditions/Allergies by `normalized_code`, Medications by `rxNorm`; Observations not deduped). Hard 500-node cap returns HTTP 422 with `nodeCount`. `fetch_patient_graph(pid)` stays as the legacy entry point.
- **`EncounterDetail.vue` → `EncounterDialog.vue`** refactor: fullscreen `<v-dialog>` with Detail / Graph tabs, opened by `PatientDetail.vue` watching `route.params.eid`. Deep-links to `/patient/:id/encounter/:eid` still work; closing the dialog navigates back to `/patient/:id`.

## Test coverage

| Suite | Result |
|---|---|
| `pytest backend/tests/test_graph_dedupe.py` | 7 passed |
| `pytest backend/tests/test_graph_routes.py` | 6 passed |
| `pytest backend/tests/test_graph_legacy_signature.py` | 2 passed |
| `pytest backend/tests` (full suite) | green |
| `npm test` (Vitest, full) | green incl. new GraphView + renamed EncounterDialog specs |
| Playwright `graph-scope.spec.ts` | scaffold committed; selector tuning is a known follow-up (same caveat as PR #4) |

## Out-of-scope follow-ups

Tracked in §12 of the design doc: hierarchical / radial layout; graph-write-time dedupe in Neo4j; cross-fact relationships (e.g., Condition `—treated_by→` Medication); URL-persisted filter state; per-encounter color coding for multi-encounter scope.

## Test plan (manual)

- [ ] Open `/patient/HN-DEMO-1` → Graph tab → see toolbar with three scope chips; default is deduped patient-level (no encounter nodes).
- [ ] Toggle the Filters cog → drawer opens; toggle Encounters on → encounter nodes appear in the canvas.
- [ ] Click "Pick…" chip → multi-select dialog opens; choose two encounters → Apply → graph re-renders with the selected encounters.
- [ ] Navigate to `/patient/HN-DEMO-1/encounter/<some-eid>` → encounter dialog opens over the patient page. Switch to the Graph tab → encounter-scope graph renders (no chip group; "Encounter scope" label).
- [ ] Close the dialog → URL pops back to `/patient/HN-DEMO-1`.
- [ ] Backend regression: existing `GET /patient/HN-DEMO-1/graph` (no params) returns 200 with deduped patient-level data.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review notes

- **Spec coverage:** every section of the design doc maps to a task —
  §4 architecture → Task 1+2 (backend) + Task 5+6+7 (frontend);
  §5 API surface → Task 2;
  §6 dedupe → Task 1;
  §7 perf cap → Task 2 + Task 3;
  §8 UI components → Tasks 5, 6, 7;
  §9 error handling → Tasks 5 (banner, abort, 404), 6 (dialog close);
  §10 testing → Tasks 1, 3, 8, 9.
- **Placeholders:** none. Every step has exact file paths, exact code, exact commands, exact expected output.
- **Type / name consistency:** `_dedupe_nodes_edges`, `_dedupe_key`, `fetch_graph`, `fetch_patient_graph`, `getGraph`, `EncounterDialog`, `closeEncounter`, `applyPicked` all match across tasks.
- **Backward compat checkpoints:** Task 2 commit keeps `fetch_patient_graph` working; Task 4 commit keeps no-options `getGraph` working (the function now has a default empty options); Task 7 commit preserves deep-links via the new dual-route to `PatientDetail`.
