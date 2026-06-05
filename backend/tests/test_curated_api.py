import pytest


@pytest.fixture()
def patient(fake_store):
    fake_store.patients["HN1"] = {"patient_id": "HN1", "name": "Jane"}
    return "HN1"


def _base_condition(id_, **kw):
    """Return a minimal condition curated_facts row."""
    row = {
        "id": id_, "patient_id": "HN1", "type": "condition",
        "normalized_key": "asthma", "display_value": "Asthma",
        "normalized_code": None, "coding_system": None, "start_date": None,
        "start_qualifier": "unknown", "stop_date": None, "stop_qualifier": "ongoing",
        "start_text": None, "stop_text": None, "schedule_text": None,
        "status": "active", "record_state": "active", "review_status": "ai_suggested",
        "origin": "ai", "human_edited_fields": [], "last_evidence_fact_id": None,
    }
    row.update(kw)
    return row


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


def test_restore_missing_id_404(app_client, patient):
    r = app_client.post("/api/curated/nope/restore")
    assert r.status_code == 404


def test_patch_rename_codeless_updates_normalized_key(app_client, patient, fake_store, stub_neo4j):
    fake_store.curated_facts.append({
        "id": "curR", "patient_id": "HN1", "type": "condition",
        "normalized_key": "high blood pressure", "display_value": "high blood pressure",
        "normalized_code": None, "coding_system": None, "start_date": None,
        "start_qualifier": "unknown", "stop_date": None, "stop_qualifier": "ongoing",
        "start_text": None, "stop_text": None, "schedule_text": None, "status": "active",
        "record_state": "active", "review_status": "ai_suggested", "origin": "ai",
        "human_edited_fields": [], "last_evidence_fact_id": None,
    })
    r = app_client.patch("/api/curated/curR", json={"displayValue": "Hypertension"})
    assert r.status_code == 200, r.text
    row = next(x for x in fake_store.curated_facts if x["id"] == "curR")
    assert row["display_value"] == "Hypertension"
    assert row["normalized_key"] == "hypertension"   # identity followed the rename


# ---- FIX 1 tests: null-clear and precise marking ----------------------------

def test_patch_null_clears_start_date(app_client, patient, fake_store, stub_neo4j):
    """PATCH {startDate: null} on a row with start_date set -> clears start_date
    AND marks start_date in human_edited_fields (bug 002)."""
    fake_store.curated_facts.append(_base_condition(
        "fixA", start_date="2025-01-01", start_qualifier="exact",
    ))
    r = app_client.patch("/api/curated/fixA", json={"startDate": None})
    assert r.status_code == 200, r.text
    row = next(x for x in fake_store.curated_facts if x["id"] == "fixA")
    assert row["start_date"] is None, "startDate null patch must clear the field"
    assert "start_date" in row["human_edited_fields"], "cleared field must be marked human-edited"


def test_patch_only_marks_changed_fields(app_client, patient, fake_store, stub_neo4j):
    """Changing only startDate must NOT mark status/display_value as human-edited (bug 005)."""
    fake_store.curated_facts.append(_base_condition(
        "fixB", display_value="Asthma", start_date=None, status="active",
        start_qualifier="unknown",
    ))
    r = app_client.patch("/api/curated/fixB", json={"startDate": "2025-12-25"})
    assert r.status_code == 200, r.text
    row = next(x for x in fake_store.curated_facts if x["id"] == "fixB")
    assert "start_date" in row["human_edited_fields"]
    assert "status" not in row["human_edited_fields"]
    assert "display_value" not in row["human_edited_fields"]


def test_patch_ui_resend_unchanged_fields_not_marked(app_client, patient, fake_store, stub_neo4j):
    """UI sends all fields; only the one that actually changed should be locked (bug 005)."""
    fake_store.curated_facts.append(_base_condition(
        "fixC",
        display_value="Asthma", start_date=None, start_qualifier="unknown",
        stop_qualifier="ongoing", status="active",
    ))
    # Simulate the frontend resending all fields, with only startDate changed
    r = app_client.patch("/api/curated/fixC", json={
        "displayValue": "Asthma",       # unchanged
        "startDate": "2025-12-25",       # CHANGED
        "startQualifier": "unknown",     # unchanged
        "stopQualifier": "ongoing",      # unchanged
        "status": "active",              # unchanged
    })
    assert r.status_code == 200, r.text
    row = next(x for x in fake_store.curated_facts if x["id"] == "fixC")
    assert row["human_edited_fields"] == ["start_date"], (
        f"only start_date should be locked, got {row['human_edited_fields']}"
    )


# ---- FIX 2 tests: insert_curated only locks supplied fields -----------------

def test_insert_with_only_display_value_locks_only_that_field(app_client, patient, stub_neo4j):
    """POST with only type+displayValue -> humanEditedFields == ['display_value']."""
    r = app_client.post(f"/api/patient/{patient}/curated", json={
        "type": "condition", "displayValue": "Hypertension",
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["humanEditedFields"] == ["display_value"], (
        f"expected only display_value locked, got {data['humanEditedFields']}"
    )


def test_insert_with_multiple_fields_locks_only_supplied(app_client, patient, stub_neo4j):
    """POST with type+displayValue+startDate+status -> only those 3 locked."""
    r = app_client.post(f"/api/patient/{patient}/curated", json={
        "type": "condition", "displayValue": "Diabetes", "startDate": "2024-01-01",
        "status": "active",
    })
    assert r.status_code == 200, r.text
    data = r.json()
    locked = set(data["humanEditedFields"])
    assert "display_value" in locked
    assert "start_date" in locked
    assert "status" in locked
    # Fields NOT supplied must NOT be locked
    assert "schedule_text" not in locked
    assert "stop_date" not in locked


# ---- FIX 4 tests: dismiss/restore propagates to graph ----------------------

def test_dismiss_propagates_record_state_to_graph(app_client, patient, fake_store, stub_neo4j):
    """DELETE /curated/<id> must push curatedRecordState='dismissed' to Neo4j."""
    fake_store.curated_facts.append(_base_condition(
        "gphA", display_value="Asthma", type="condition",
    ))
    r = app_client.delete("/api/curated/gphA")
    assert r.status_code == 200
    # Find any Condition Cypher call and verify recordState param
    cond_calls = [(q, p) for q, p in stub_neo4j if "Condition" in q]
    assert cond_calls, "expected a Condition graph propagation after dismiss"
    _, params = cond_calls[-1]
    assert params.get("recordState") == "dismissed", (
        f"expected recordState='dismissed' in graph params, got {params}"
    )


# ---- FIX 5 tests: rename collision -> 409 -----------------------------------

def test_patch_rename_collision_returns_409(app_client, patient, fake_store, stub_neo4j):
    """PATCH rename to a key that another row already occupies must return 409."""
    fake_store.curated_facts.append(_base_condition(
        "col1", display_value="high blood pressure",
        normalized_key="high blood pressure", status="active",
    ))
    fake_store.curated_facts.append(_base_condition(
        "col2", display_value="Hypertension",
        normalized_key="hypertension", status="active",
    ))
    # Renaming col1 -> "Hypertension" would collide with col2
    r = app_client.patch("/api/curated/col1", json={"displayValue": "Hypertension"})
    assert r.status_code == 409, (
        f"expected 409 on rename collision, got {r.status_code}: {r.text}"
    )


# ---- Hardening: real-PG date objects coerced to ISO strings -----------------

def test_get_curated_coerces_date_objects_to_iso_strings(fake_store):
    """Real Postgres SELECT * returns datetime.date for date columns; _to_dict
    must coerce them to ISO strings so change-detection (FIX 1) and the
    str|None schema stay correct."""
    import datetime as _dt
    fake_store.patients["HN1"] = {"patient_id": "HN1"}
    fake_store.curated_facts.append({
        "id": "curD", "patient_id": "HN1", "type": "medication",
        "normalized_key": "warfarin", "display_value": "Warfarin",
        "normalized_code": None, "coding_system": None,
        "start_date": _dt.date(2026, 1, 10), "start_qualifier": "exact",
        "stop_date": _dt.date(2026, 3, 1), "stop_qualifier": "exact",
        "start_text": None, "stop_text": None, "schedule_text": None, "status": "start",
        "record_state": "active", "review_status": "ai_suggested", "origin": "ai",
        "human_edited_fields": [], "last_evidence_fact_id": None,
    })
    from app.services.curated import get_curated
    row = get_curated("curD")
    assert row["start_date"] == "2026-01-10"
    assert row["stop_date"] == "2026-03-01"
