"""?async=true on summary / coding / encounter-summary / encounter-coding.

Returns 200 with {jobId, status: "queued", type, patientId[, encounterId]}
immediately instead of waiting for the AI call. The queue handler invokes
the same service function the sync path uses, so the test asserts both
the response shape and that the corresponding job row was created with
the right `type` and `patient_id`.
"""

from __future__ import annotations


def _seed_patient_with_encounter(fake_store):
    fake_store.patients["HN1"] = {"patient_id": "HN1", "name": "Test"}
    fake_store.encounters["E1"] = {
        "encounter_id": "E1", "patient_id": "HN1", "type": "admission",
        "date_time": "2026-04-01T08:00:00+00:00",
        "department": "IM", "provider": "Dr A",
    }
    fake_store.facts.append({
        "id": "f-1", "patient_id": "HN1", "encounter_id": "E1",
        "type": "condition", "value": "Hypertension",
        "normalized_code": "I10", "review_status": "ai_suggested",
        "extra": {}, "confidence": 0.9, "date_time": "2026-04-01",
    })


# ---------------------------------------------------------------------------
# Patient-level summary
# ---------------------------------------------------------------------------


def test_summary_async_returns_job_id_not_response(app_client, fake_store):
    _seed_patient_with_encounter(fake_store)
    r = app_client.post(
        "/api/patient/HN1/summary?async=true",
        json={"type": "brief", "includeEvidence": False},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "queued"
    assert body["type"] == "patient_summary"
    assert body["patientId"] == "HN1"
    assert body["jobId"]
    # And a job row should have been created with that type.
    types = [job.get("type") for job in fake_store.jobs.values()]
    assert "patient_summary" in types


def test_summary_sync_still_returns_inline_response(app_client, fake_store):
    """No ?async= → existing sync behaviour: returns the SummaryResponse
    body directly, no jobs created."""
    _seed_patient_with_encounter(fake_store)
    before = len(fake_store.jobs)
    r = app_client.post(
        "/api/patient/HN1/summary",
        json={"type": "brief", "includeEvidence": False},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # Sync shape — has the SummaryResponse fields, not the queued shape.
    assert "jobId" not in body or body.get("status") != "queued"
    assert "markdown" in body
    assert len(fake_store.jobs) == before, "sync call must not create a job"


# ---------------------------------------------------------------------------
# Patient-level coding
# ---------------------------------------------------------------------------


def test_coding_async_returns_job_id(app_client, fake_store):
    _seed_patient_with_encounter(fake_store)
    r = app_client.post(
        "/api/patient/HN1/coding/suggest?async=true",
        json={"standards": ["ICD10"], "includeEvidence": False},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "queued"
    assert body["type"] == "patient_coding"
    assert body["patientId"] == "HN1"
    assert body["jobId"]
    assert "patient_coding" in [j.get("type") for j in fake_store.jobs.values()]


def test_coding_sync_still_returns_inline(app_client, fake_store):
    _seed_patient_with_encounter(fake_store)
    before = len(fake_store.jobs)
    r = app_client.post(
        "/api/patient/HN1/coding/suggest",
        json={"standards": ["ICD10"], "includeEvidence": False},
    )
    assert r.status_code == 200
    assert "patientId" in r.json()
    assert len(fake_store.jobs) == before


# ---------------------------------------------------------------------------
# Encounter-scoped variants
# ---------------------------------------------------------------------------


def test_encounter_summary_async_carries_encounter_id_in_response(app_client, fake_store):
    _seed_patient_with_encounter(fake_store)
    r = app_client.post(
        "/api/patient/HN1/encounter/E1/summary?async=true",
        json={"type": "brief", "includeEvidence": False},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "queued"
    assert body["type"] == "encounter_summary"
    assert body["patientId"] == "HN1"
    assert body["encounterId"] == "E1"
    # And the encounter_id is stashed in the payload for the handler to
    # pop back out at run time.
    job = next(j for j in fake_store.jobs.values() if j.get("type") == "encounter_summary")
    assert job["payload"]["__encounter_id"] == "E1"


def test_encounter_coding_async_carries_encounter_id_in_response(app_client, fake_store):
    _seed_patient_with_encounter(fake_store)
    r = app_client.post(
        "/api/patient/HN1/encounter/E1/coding/suggest?async=true",
        json={"standards": ["ICD10"], "includeEvidence": False},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "queued"
    assert body["type"] == "encounter_coding"
    assert body["encounterId"] == "E1"


# ---------------------------------------------------------------------------
# Handlers are registered
# ---------------------------------------------------------------------------


def test_all_four_handlers_registered():
    """A simple wiring smoke — the queue module's handler registry must
    contain the four new entries so a worker will actually pick them up."""
    from app.services.queue import JOB_HANDLERS
    # Importing this module triggers `register_handler(...)` calls.
    import app.services.jobs  # noqa: F401
    for t in ("patient_summary", "patient_coding",
              "encounter_summary", "encounter_coding"):
        assert t in JOB_HANDLERS, f"handler for {t!r} not registered"
