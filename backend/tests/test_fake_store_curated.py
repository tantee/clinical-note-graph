import json

from sqlalchemy import text

import app.db.postgres as pg

_INSERT = (
    "INSERT INTO curated_facts (patient_id, type, normalized_key, display_value, "
    "normalized_code, coding_system, start_date, start_qualifier, stop_date, "
    "stop_qualifier, start_text, stop_text, schedule_text, status, record_state, "
    "review_status, origin, human_edited_fields, last_evidence_fact_id, updated_by) "
    "VALUES (:patient_id, :type, :normalized_key, :display_value, :normalized_code, "
    ":coding_system, :start_date, :start_qualifier, :stop_date, :stop_qualifier, "
    ":start_text, :stop_text, :schedule_text, :status, :record_state, :review_status, "
    ":origin, CAST(:human_edited_fields AS jsonb), :last_evidence_fact_id, :updated_by) "
    "RETURNING id"
)

_SELECT_ACTIVE = (
    "SELECT * FROM curated_facts WHERE patient_id = :pid AND type = :type "
    "AND record_state = 'active' ORDER BY display_value ASC"
)
_SELECT_BY_ID = "SELECT * FROM curated_facts WHERE id = CAST(:cid AS uuid)"
_UPDATE_STATE = (
    "UPDATE curated_facts SET record_state = :state, updated_at = now() "
    "WHERE id = CAST(:cid AS uuid)"
)
_UPDATE_FULL = (
    "UPDATE curated_facts SET display_value = :display_value, "
    "normalized_code = :normalized_code, coding_system = :coding_system, "
    "start_date = :start_date, start_qualifier = :start_qualifier, "
    "stop_date = :stop_date, stop_qualifier = :stop_qualifier, "
    "start_text = :start_text, stop_text = :stop_text, "
    "schedule_text = :schedule_text, status = :status, "
    "record_state = :record_state, review_status = :review_status, "
    "human_edited_fields = CAST(:human_edited_fields AS jsonb), "
    "last_evidence_fact_id = :last_evidence_fact_id, updated_by = :updated_by, "
    "updated_at = now() WHERE id = CAST(:cid AS uuid)"
)


def _payload(**over):
    base = {k: None for k in (
        "normalized_code", "coding_system", "start_date", "stop_date",
        "start_text", "stop_text", "schedule_text", "status",
        "last_evidence_fact_id", "updated_by",
    )}
    base.update(
        patient_id="HN1", type="medication", normalized_key="warfarin",
        display_value="Warfarin", start_qualifier="unknown", stop_qualifier="unknown",
        record_state="active", review_status="ai_suggested", origin="ai",
        human_edited_fields=json.dumps([]),
    )
    base.update(over)
    return base


def test_fake_store_curated_round_trip(fake_store):
    fake_store.patients["HN1"] = {"patient_id": "HN1"}

    with pg.db_session() as s:
        res = s.execute(text(_INSERT), _payload()).mappings().first()
    assert res and res["id"]
    cid = res["id"]

    with pg.db_session() as s:
        rows = s.execute(text(_SELECT_ACTIVE), {"pid": "HN1", "type": "medication"}).mappings().all()
    assert len(rows) == 1
    assert rows[0]["display_value"] == "Warfarin"
    assert rows[0]["human_edited_fields"] == []   # stored as a list, not a JSON string

    with pg.db_session() as s:
        row = s.execute(text(_SELECT_BY_ID), {"cid": cid}).mappings().first()
    assert row["normalized_key"] == "warfarin"

    with pg.db_session() as s:
        s.execute(text(_UPDATE_STATE), {"cid": cid, "state": "dismissed"})
    with pg.db_session() as s:
        rows = s.execute(text(_SELECT_ACTIVE), {"pid": "HN1", "type": "medication"}).mappings().all()
    assert rows == []   # dismissed row no longer in the active list


def test_fake_store_curated_full_update(fake_store):
    fake_store.patients["HN1"] = {"patient_id": "HN1"}

    with pg.db_session() as s:
        res = s.execute(text(_INSERT), _payload()).mappings().first()
    cid = res["id"]

    full = {
        "cid": cid,
        "display_value": "Warfarin sodium",
        "normalized_code": "11289",
        "coding_system": "RxNorm",
        "start_date": "2026-01-01",
        "start_qualifier": "exact",
        "stop_date": None,
        "stop_qualifier": "unknown",
        "start_text": "started Jan",
        "stop_text": None,
        "schedule_text": "5mg daily",
        "status": "active",
        "record_state": "active",
        "review_status": "human_confirmed",
        "human_edited_fields": json.dumps(["start_date"]),
        "last_evidence_fact_id": "fact-7",
        "updated_by": "dr.smith",
    }
    with pg.db_session() as s:
        s.execute(text(_UPDATE_FULL), full)

    with pg.db_session() as s:
        row = s.execute(text(_SELECT_BY_ID), {"cid": cid}).mappings().first()
    assert row["display_value"] == "Warfarin sodium"
    assert row["normalized_code"] == "11289"
    assert row["coding_system"] == "RxNorm"
    assert row["start_date"] == "2026-01-01"
    assert row["review_status"] == "human_confirmed"
    assert row["schedule_text"] == "5mg daily"
    assert row["human_edited_fields"] == ["start_date"]   # JSON string decoded to list

    # Mutating the returned list must NOT corrupt FakeStore state.
    row["human_edited_fields"].append("MUTATED")
    with pg.db_session() as s:
        fresh = s.execute(text(_SELECT_BY_ID), {"cid": cid}).mappings().first()
    assert fresh["human_edited_fields"] == ["start_date"]
