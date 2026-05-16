from __future__ import annotations


def _payload():
    return {
        "patient": {"patientId": "HN-A1", "name": "Async"},
        "encounter": {"type": "admission", "dateTime": "2026-05-15T10:00:00+07:00"},
        "format": "text",
        "content": "Patient has Type 2 diabetes mellitus. BP 152/95.",
        "source": {"system": "T", "documentId": "doc-a1", "version": "1"},
    }


def test_default_post_returns_queued(app_client, fake_store):
    r = app_client.post("/api/emr/ingest", json=_payload())
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "queued"
    assert body["jobId"]
    # Row exists in pending state.
    assert fake_store.jobs[body["jobId"]]["status"] == "pending"


def test_sync_query_param_still_runs_inline(app_client, fake_store):
    r = app_client.post("/api/emr/ingest?async=false", json=_payload())
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "completed"
    assert body["summary"] is not None


def test_job_status_endpoint_returns_progress(app_client, fake_store):
    fake_store.jobs["abc"] = {
        "job_id": "abc", "type": "emr_ingest", "status": "running",
        "patient_id": "HN1", "document_id": "d1",
        "payload": {}, "attempts": 1, "max_attempts": 3,
        "progress": {"stage_persisted": {"at": "now"}},
        "locked_by": "w1", "locked_until": "soon", "priority": 0, "next_run_at": "now",
    }
    r = app_client.get("/api/jobs/abc")
    assert r.status_code == 200
    assert "stage_persisted" in (r.json().get("progress") or {})


def test_requeue_endpoint(app_client, fake_store):
    fake_store.jobs["fail1"] = {
        "job_id": "fail1", "type": "emr_ingest", "status": "failed",
        "attempts": 3, "max_attempts": 3, "error": "boom",
        "payload": {}, "patient_id": None, "document_id": None,
        "progress": {}, "locked_by": None, "locked_until": None,
        "priority": 0, "next_run_at": "now",
    }
    r = app_client.post("/api/jobs/fail1/requeue")
    assert r.status_code == 200
    body = r.json()
    assert body.get("requeued") == "fail1"
    assert fake_store.jobs["fail1"]["status"] == "pending"
    assert fake_store.jobs["fail1"]["attempts"] == 0


def test_list_jobs_filters_by_status(app_client, fake_store):
    fake_store.jobs.update({
        "a": {"job_id": "a", "type": "emr_ingest", "status": "completed", "patient_id": None, "document_id": None,
              "payload": {}, "attempts": 1, "max_attempts": 3, "progress": {},
              "locked_by": None, "locked_until": None, "priority": 0, "next_run_at": "now"},
        "b": {"job_id": "b", "type": "emr_ingest", "status": "failed", "patient_id": None, "document_id": None,
              "payload": {}, "attempts": 3, "max_attempts": 3, "progress": {},
              "locked_by": None, "locked_until": None, "priority": 0, "next_run_at": "now"},
    })
    r = app_client.get("/api/jobs?status=failed&limit=10")
    assert r.status_code == 200
    rows = r.json()
    assert all(row["status"] == "failed" for row in rows)
    assert any(row["job_id"] == "b" for row in rows)
