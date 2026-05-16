from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


def test_markdown_generator_creates_vault(monkeypatch, tmp_path):
    # Point vault at tmpdir BEFORE importing the module-level vault setup
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))

    # Clear cached settings
    from app.config import get_settings
    get_settings.cache_clear()

    from app.schemas.extraction import ClinicalExtractionResult, MedicationChange, PatientFact, ObservationResult, PlanItem
    from app.services.markdown_generator import generate_markdown

    extraction = ClinicalExtractionResult(
        patientId="HN1",
        encounterId="E1",
        documentId="D1",
        summary="Type 2 DM admission",
        problems=[PatientFact(type="condition", value="Type 2 diabetes mellitus", normalizedCode="E11.9", codingSystem="ICD10", confidence=0.8, evidenceText="dx of T2DM")],
        medications=[MedicationChange(name="Metformin", action="start", rxNorm="6809", indication="diabetes", confidence=0.9, evidenceText="start metformin 500 mg")],
        observations=[ObservationResult(name="HbA1c", loinc="4548-4", value="8.4", unit="%", confidence=0.95)],
        plans=[PlanItem(description="Diabetes education", category="education")],
    )

    written = generate_markdown(
        patient={"patientId": "HN1", "name": "Sample"},
        encounter={"encounterId": "E1", "type": "admission", "dateTime": "2026-05-15T10:00:00+07:00"},
        document={"documentId": "D1", "sourceSystem": "Test", "version": "1", "format": "text"},
        raw_content="Patient has T2DM. Start metformin 500mg.",
        extraction=extraction,
    )

    # Index file exists
    idx = tmp_path / "patients" / "HN1" / "index.md"
    assert idx.exists()
    text = idx.read_text()
    assert "[[problems/type-2-diabetes-mellitus|Type 2 diabetes mellitus]]" in text
    assert "[[medications/metformin|Metformin]]" in text
    # Problem file exists with evidence
    prob = tmp_path / "patients" / "HN1" / "problems" / "type-2-diabetes-mellitus.md"
    assert prob.exists()
    assert "Evidence" in prob.read_text()
    # Visit file
    visits = list((tmp_path / "patients" / "HN1" / "visits").glob("*.md"))
    assert len(visits) == 1
    # Source file
    src = tmp_path / "patients" / "HN1" / "sources" / "D1.md"
    assert src.exists()


def test_markdown_appends_longitudinally(monkeypatch, tmp_path):
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    from app.config import get_settings
    get_settings.cache_clear()

    from app.schemas.extraction import ClinicalExtractionResult, PatientFact
    from app.services.markdown_generator import generate_markdown

    base_kwargs = dict(
        patient={"patientId": "HN2"},
        document={"documentId": "DOCA", "sourceSystem": "Test", "version": "1", "format": "text"},
        raw_content="hello",
    )

    extraction1 = ClinicalExtractionResult(
        patientId="HN2",
        encounterId="E1",
        documentId="DOCA",
        problems=[PatientFact(type="condition", value="Hypertension", confidence=0.7, evidenceText="HTN noted")],
    )
    generate_markdown(
        encounter={"encounterId": "E1", "type": "admission", "dateTime": "2026-01-01T09:00:00+07:00"},
        extraction=extraction1, **base_kwargs,
    )

    base_kwargs["document"] = {"documentId": "DOCB", "sourceSystem": "Test", "version": "1", "format": "text"}
    extraction2 = ClinicalExtractionResult(
        patientId="HN2",
        encounterId="E2",
        documentId="DOCB",
        problems=[PatientFact(type="condition", value="Hypertension", confidence=0.85, evidenceText="HTN persistent")],
    )
    generate_markdown(
        encounter={"encounterId": "E2", "type": "progress_note", "dateTime": "2026-01-05T09:00:00+07:00"},
        extraction=extraction2, **base_kwargs,
    )

    prob = (tmp_path / "patients" / "HN2" / "problems" / "hypertension.md").read_text()
    assert prob.count("HTN noted") == 1
    assert prob.count("HTN persistent") == 1
    assert prob.count("[[visits/2026-01-01-admission") >= 1
    assert prob.count("[[visits/2026-01-05-progress_note") >= 1
