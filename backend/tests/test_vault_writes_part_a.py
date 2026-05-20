"""Tests for issue #27 Part A — vault writes for patient-level coding and
export bundle mirroring.

Acceptance criteria (from #27 Part A):
- Patient-level coding produces `patients/<HN>/coding/patient-level-<ts>.md`
  with the same YAML frontmatter shape as the encounter-scoped variant.
- Export bundles are mirrored into `patients/<HN>/exports/<name>-<ts>.<ext>`
  so the patient's notes folder is a complete audit trail.
- The DB row's `vault_path` column points at the new file (already wired by
  save_coding) and the export response carries `vaultPath` so the UI can
  deep-link.
"""

from __future__ import annotations

import base64
import json
import zipfile
from pathlib import Path


def _seed_patient(fake_store, pid="HN1"):
    fake_store.patients[pid] = {"patient_id": pid, "name": "Test Patient"}
    fake_store.encounters["E1"] = {
        "encounter_id": "E1", "patient_id": pid, "type": "admission",
        "date_time": "2026-04-01T08:00:00+00:00",
        "department": "IM", "provider": "Dr A",
    }
    fake_store.facts.append({
        "id": "f-1", "patient_id": pid, "encounter_id": "E1",
        "type": "condition", "value": "Hypertension",
        "normalized_code": "I10", "review_status": "ai_suggested",
        "extra": {}, "confidence": 0.9, "created_at": "2026-04-01T08:00:00Z",
    })


# ---------------------------------------------------------------------------
# Patient-level coding vault write
# ---------------------------------------------------------------------------


def test_patient_level_coding_writes_vault_md(app_client, fake_store, isolated_vault):
    """POSTing patient-level coding writes a markdown file under
    patients/<HN>/coding/patient-level-<ts>.md and stores the vault path
    on the patient_summaries row so the UI can link to it."""
    _seed_patient(fake_store)
    r = app_client.post(
        "/api/patient/HN1/coding/suggest",
        json={"standards": ["ICD10"], "includeEvidence": False},
    )
    assert r.status_code == 200, r.text

    coding_dir = isolated_vault / "patients" / "HN1" / "coding"
    files = list(coding_dir.glob("patient-level-*.md"))
    assert len(files) == 1, f"expected exactly one patient-level coding file, got {files}"

    body = files[0].read_text(encoding="utf-8")
    # YAML frontmatter shape — same as encounter-scoped (see _write_summary_markdown).
    assert body.startswith("---\n")
    assert "patientId: HN1" in body
    assert "kind: coding" in body
    assert "aiAssisted: true" in body
    # Body should be the rendered coding markdown (not raw JSON).
    assert "# Suggested coding" in body

    # The patient_summaries row picks up the vault_path so the GET /latest
    # endpoint can hand the UI a deep-link.
    coding_rows = [r for r in fake_store.patient_summaries if r["kind"] == "coding"]
    assert len(coding_rows) == 1
    assert coding_rows[0]["vault_path"] is not None
    assert coding_rows[0]["vault_path"].startswith("patients/HN1/coding/patient-level-")


def test_encounter_level_coding_still_writes_under_encounter_folder(app_client, fake_store, isolated_vault):
    """Regression: the existing encounter-scoped path
    (patients/<HN>/encounters/<eid>/coding.md) must not have moved."""
    _seed_patient(fake_store)
    r = app_client.post(
        "/api/patient/HN1/encounter/E1/coding/suggest",
        json={"standards": ["ICD10"], "includeEvidence": False},
    )
    assert r.status_code == 200, r.text

    expected = isolated_vault / "patients" / "HN1" / "encounters" / "E1" / "coding.md"
    assert expected.exists(), f"encounter-scoped coding file missing at {expected}"

    # Patient-level path should NOT exist for an encounter-scoped run.
    patient_dir = isolated_vault / "patients" / "HN1" / "coding"
    assert not patient_dir.exists() or not list(patient_dir.glob("patient-level-*.md"))


# ---------------------------------------------------------------------------
# Export bundle mirroring
# ---------------------------------------------------------------------------


def _exports_dir(isolated_vault: Path, pid: str) -> Path:
    return isolated_vault / "patients" / pid / "exports"


def test_export_summary_mirrors_md(app_client, fake_store, isolated_vault):
    _seed_patient(fake_store)
    r = app_client.post(
        "/api/export",
        json={"patientId": "HN1", "exportType": "summary"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("vaultPath", "").startswith("patients/HN1/exports/summary-")
    assert body["vaultPath"].endswith(".md")

    files = list(_exports_dir(isolated_vault, "HN1").glob("summary-*.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    # Hybrid format: rendered markdown body + fenced JSON for structured data.
    assert "## Structured data" in text
    assert "```json" in text


def test_export_coding_mirrors_json(app_client, fake_store, isolated_vault):
    _seed_patient(fake_store)
    r = app_client.post(
        "/api/export",
        json={"patientId": "HN1", "exportType": "coding"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("vaultPath", "").startswith("patients/HN1/exports/coding-")
    assert body["vaultPath"].endswith(".json")

    files = list(_exports_dir(isolated_vault, "HN1").glob("coding-*.json"))
    assert len(files) == 1
    parsed = json.loads(files[0].read_text(encoding="utf-8"))
    # Same payload the caller got — primaryDiagnosis / codingCandidates etc.
    assert isinstance(parsed, dict)


def test_export_fhir_bundle_mirrors_json(app_client, fake_store, isolated_vault):
    _seed_patient(fake_store)
    r = app_client.post(
        "/api/export",
        json={"patientId": "HN1", "exportType": "fhir_bundle"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("vaultPath", "").startswith("patients/HN1/exports/fhir_bundle-")
    files = list(_exports_dir(isolated_vault, "HN1").glob("fhir_bundle-*.json"))
    assert len(files) == 1
    bundle = json.loads(files[0].read_text(encoding="utf-8"))
    assert bundle.get("resourceType") == "Bundle"


def test_export_markdown_vault_mirrors_zip(app_client, fake_store, isolated_vault):
    """The vault-snapshot export round-trips through base64 → real .zip on disk
    so the audit trail is openable in Finder without an extra decode step."""
    _seed_patient(fake_store)
    # Plant a file inside the vault so the snapshot has something to zip.
    note = isolated_vault / "patients" / "HN1" / "summaries" / "preexisting.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("# preexisting\n", encoding="utf-8")

    r = app_client.post(
        "/api/export",
        json={"patientId": "HN1", "exportType": "markdown_vault"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("vaultPath", "").startswith("patients/HN1/exports/markdown_vault-")
    assert body["vaultPath"].endswith(".zip")

    files = list(_exports_dir(isolated_vault, "HN1").glob("markdown_vault-*.zip"))
    assert len(files) == 1
    # Verify it's a real .zip (not the base64 string written as text).
    with zipfile.ZipFile(files[0], "r") as zf:
        names = zf.namelist()
    assert any(n.endswith("preexisting.md") for n in names)


def test_export_custom_uses_profile_id_in_filename(app_client, fake_store, isolated_vault):
    """Custom exports name the file after the profile so the user can scan
    `exports/` and see which profile produced what (vs an opaque timestamp)."""
    _seed_patient(fake_store)
    # default-summary profile is auto-seeded in FakeStore.
    r = app_client.post(
        "/api/export",
        json={"patientId": "HN1", "exportType": "custom", "profileId": "default-summary"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("vaultPath", "").startswith(
        "patients/HN1/exports/default-summary-"
    )
