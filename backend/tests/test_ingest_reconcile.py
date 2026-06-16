from app.schemas.extraction import ClinicalExtractionResult, MedicationChange, PatientFact


def _extraction(**kw):
    return ClinicalExtractionResult(patientId="HN1", encounterId="E1", documentId="D1", **kw)


def test_reconcile_called_after_facts_persisted(fake_store, stub_neo4j, monkeypatch):
    """The ingest flow runs reconcile_curated once per successful extraction."""
    import app.services.ingest as ingest

    calls = []
    monkeypatch.setattr(
        ingest, "reconcile_curated",
        lambda pid, extraction: calls.append((pid, extraction)),
    )
    ex = _extraction(
        problems=[PatientFact(type="condition", value="Asthma")],
        medications=[MedicationChange(name="albuterol")],
    )
    ingest._persist_post_extraction(
        patient={"patientId": "HN1"},
        encounter={"encounterId": "E1"},
        document={"documentId": "D1"},
        extraction=ex, valid=True, errors=[],
    )
    assert len(calls) == 1
    assert calls[0][0] == "HN1"


def test_reconcile_not_called_when_invalid(fake_store, stub_neo4j, monkeypatch):
    import app.services.ingest as ingest

    calls = []
    monkeypatch.setattr(
        ingest, "reconcile_curated",
        lambda pid, extraction: calls.append((pid, extraction)),
    )
    ex = _extraction(problems=[PatientFact(type="condition", value="Asthma")])
    ingest._persist_post_extraction(
        patient={"patientId": "HN1"},
        encounter={"encounterId": "E1"},
        document={"documentId": "D1"},
        extraction=ex, valid=False, errors=["bad"],
    )
    assert calls == []
