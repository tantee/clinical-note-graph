"""End-to-end smoke against a live stack started by `docker compose up`.

Set CNG_E2E=1 to enable. Set CNG_BASE_URL to override the default
(http://localhost:8000) if the stack isn't on the local machine.
"""

from __future__ import annotations

import os
import time

import httpx
import pytest


pytestmark = pytest.mark.e2e


BASE = os.environ.get("CNG_BASE_URL", "http://localhost:8000")
API_KEY = os.environ.get("CNG_API_KEY", "")


def _headers() -> dict[str, str]:
    return {"X-API-Key": API_KEY} if API_KEY else {}


@pytest.fixture(scope="module")
def wait_for_backend():
    deadline = time.time() + 90
    last = None
    while time.time() < deadline:
        try:
            r = httpx.get(f"{BASE}/health", timeout=5)
            if r.status_code == 200:
                return
            last = r.text
        except Exception as exc:
            last = str(exc)
        time.sleep(2)
    pytest.fail(f"Backend never came up at {BASE}: {last}")


def test_full_ingest_to_summary_to_coding(wait_for_backend):
    payload = {
        "patient": {"patientId": "E2E-001", "name": "E2E Test", "gender": "male", "birthDate": "1965-04-12"},
        "encounter": {"type": "admission", "dateTime": "2026-05-15T10:00:00+07:00", "department": "IM", "provider": "Dr"},
        "format": "text",
        "content": (
            "Admission note. Patient with Type 2 diabetes mellitus and hypertension. "
            "HbA1c 8.4 % on admission. BP 152/95. Start metformin 500 mg bid, lisinopril 10 mg daily. "
            "Plan: cardiology consult."
        ),
        "source": {"system": "E2E-HIS", "documentId": "e2e-doc-1", "version": "1"},
    }

    r = httpx.post(f"{BASE}/api/emr/ingest?async=false", json=payload, headers=_headers(), timeout=120)
    assert r.status_code == 200, r.text
    assert r.json()["patientId"] == "E2E-001"

    # Reading back the structured patient
    r = httpx.get(f"{BASE}/api/patient/E2E-001", headers=_headers(), timeout=20)
    assert r.status_code == 200
    facts = r.json()
    assert any("diabetes" in p["value"].lower() for p in facts["problems"])

    # Graph
    r = httpx.get(f"{BASE}/api/patient/E2E-001/graph", headers=_headers(), timeout=20)
    assert r.status_code == 200
    g = r.json()
    assert g["nodes"], "expected at least one node in the patient graph"

    # Notes/markdown
    r = httpx.get(f"{BASE}/api/patient/E2E-001/notes", headers=_headers(), timeout=20)
    assert r.status_code == 200
    notes = r.json()["files"]
    assert any(f["path"].endswith("index.md") for f in notes)

    # Summary
    r = httpx.post(f"{BASE}/api/patient/E2E-001/summary", headers=_headers(), json={"type": "brief"}, timeout=60)
    assert r.status_code == 200
    assert "markdown" in r.json()

    # Coding
    r = httpx.post(f"{BASE}/api/patient/E2E-001/coding/suggest", headers=_headers(),
                   json={"standards": ["ICD10", "SNOMEDCT"]}, timeout=60)
    assert r.status_code == 200
    assert r.json()["disclaimer"].startswith("AI-assisted")

    # Export FHIR bundle
    r = httpx.post(f"{BASE}/api/export", headers=_headers(),
                   json={"patientId": "E2E-001", "exportType": "fhir_bundle"}, timeout=60)
    assert r.status_code == 200
    assert r.json()["data"]["resourceType"] == "Bundle"


def test_e2e_idempotent(wait_for_backend):
    payload = {
        "patient": {"patientId": "E2E-002"},
        "encounter": {"type": "lab", "dateTime": "2026-05-15T10:00:00+07:00"},
        "format": "text",
        "content": "HbA1c 8.0 %. Glucose 180 mg/dL.",
        "source": {"system": "E2E", "documentId": "e2e-doc-lab", "version": "1"},
    }
    r1 = httpx.post(f"{BASE}/api/emr/ingest?async=false", json=payload, headers=_headers(), timeout=120)
    r2 = httpx.post(f"{BASE}/api/emr/ingest?async=false", json=payload, headers=_headers(), timeout=120)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["documentId"] == r2.json()["documentId"]


def test_async_ingest_polls_to_completion(wait_for_backend):
    payload = {
        "patient": {"patientId": "E2E-Q1"},
        "encounter": {"type": "admission", "dateTime": "2026-05-15T10:00:00+07:00"},
        "format": "text",
        "content": "Patient with Type 2 diabetes mellitus.",
        "source": {"system": "E2E", "documentId": "e2e-q1", "version": "1"},
    }
    r = httpx.post(f"{BASE}/api/emr/ingest", json=payload, headers=_headers(), timeout=60)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "queued"
    job_id = body["jobId"]

    deadline = time.time() + 90
    final = None
    while time.time() < deadline:
        j = httpx.get(f"{BASE}/api/jobs/{job_id}", headers=_headers(), timeout=10).json()
        if j["status"] in ("completed", "failed"):
            final = j
            break
        time.sleep(1)

    assert final is not None, "job did not finish within 90 seconds"
    assert final["status"] == "completed", f"job failed: {final.get('error')}"

    s = httpx.get(f"{BASE}/api/debug/summary", headers=_headers(), timeout=10).json()
    assert s["total_calls"] >= 1
