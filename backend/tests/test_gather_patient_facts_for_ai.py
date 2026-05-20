"""Unit tests for gather_patient_facts_for_ai — the hierarchical payload
helper that powers patient-level Summary and Coding AI calls.

Verifies the two representations (summary vs raw_facts), a mixed-mode
patient, the temporality / patient-header invariants, and that the
de-identifier still runs successfully on the new payload shape so the
compliance audit (#11) holds for compressed prompts.
"""
from __future__ import annotations

import json
from datetime import date


def _seed_patient_with_encounters(fake_store, *, patient_id="HN1", n_encounters=3):
    fake_store.patients[patient_id] = {
        "patient_id": patient_id, "name": "Test Patient", "birth_date": date(1965, 4, 12),
    }
    for i in range(1, n_encounters + 1):
        eid = f"E{i}"
        fake_store.encounters[eid] = {
            "encounter_id": eid, "patient_id": patient_id,
            "type": "admission" if i == 1 else "visit",
            "date_time": f"2026-0{i}-01T08:00:00+00:00",
            "department": "IM", "provider": f"Dr {i}",
        }
        # One condition + one observation per encounter
        fake_store.facts.extend([
            {"id": f"f-c-{i}", "patient_id": patient_id, "encounter_id": eid,
             "type": "condition", "value": f"Condition-{i}",
             "normalized_code": f"X{i}", "review_status": "ai_suggested",
             "date_time": f"2026-0{i}-01", "extra": {}, "confidence": 0.8,
             "evidence_text": f"e-c-{i}", "created_at": f"2026-0{i}-01T08:00:00Z"},
            {"id": f"f-o-{i}", "patient_id": patient_id, "encounter_id": eid,
             "type": "observation", "value": "HbA1c", "normalized_code": "4548-4",
             "review_status": "ai_suggested",
             "extra": {"value": f"{6 + i}.0", "unit": "%"}, "confidence": 0.9,
             "date_time": f"2026-0{i}-01T08:00:00Z",
             "created_at": f"2026-0{i}-01T08:00:00Z"},
        ])


def _seed_encounter_summary(fake_store, *, patient_id, encounter_id, markdown="# E summary"):
    fake_store.patient_summaries.append({
        "id": f"ps-{encounter_id}", "patient_id": patient_id,
        "kind": "summary", "encounter_id": encounter_id, "type": "discharge",
        "model": "mock", "markdown": markdown, "payload": None, "evidence": None,
        "cost_usd": 0, "latency_ms": 1, "vault_path": None,
        "created_at": f"2026-{encounter_id[-1]:>02}-02T00:00:00Z",
    })


def test_encounter_with_summary_collapses_to_markdown(fake_store):
    """When an encounter has a persisted summary, only the markdown is shipped —
    the raw facts dict for that encounter is dropped."""
    _seed_patient_with_encounters(fake_store, n_encounters=1)
    _seed_encounter_summary(
        fake_store, patient_id="HN1", encounter_id="E1",
        markdown="# E1 discharge\n- POD0 stable\n- Going home",
    )

    from app.services.patient_facts import gather_patient_facts_for_ai
    out = gather_patient_facts_for_ai("HN1")

    assert len(out["encounters"]) == 1
    enc = out["encounters"][0]
    assert enc["representation"] == "summary"
    assert enc["markdown"] == "# E1 discharge\n- POD0 stable\n- Going home"
    assert enc["encounterId"] == "E1"
    # Encounter metadata is preserved alongside the markdown so the model can
    # still reason about temporal ordering even when facts are collapsed.
    assert enc["dateTime"] is not None
    assert enc["department"] == "IM"
    assert enc["provider"] == "Dr 1"
    # No raw_facts fields when collapsed.
    assert "problems" not in enc
    assert "observations" not in enc


def test_encounter_without_summary_keeps_raw_facts(fake_store):
    """When no summary exists, the encounter ships its raw facts grouped by
    the same bucket names gather_patient_facts uses (problems, observations…)."""
    _seed_patient_with_encounters(fake_store, n_encounters=1)

    from app.services.patient_facts import gather_patient_facts_for_ai
    out = gather_patient_facts_for_ai("HN1")

    assert len(out["encounters"]) == 1
    enc = out["encounters"][0]
    assert enc["representation"] == "raw_facts"
    assert len(enc["problems"]) == 1
    assert enc["problems"][0]["value"] == "Condition-1"
    assert len(enc["observations"]) == 1
    assert enc["observations"][0]["value"] == "HbA1c"
    # markdown only appears on summary-represented encounters.
    assert "markdown" not in enc


def test_mixed_mode_5_encounters_3_have_summaries(fake_store):
    """Acceptance scenario: 5 encounters, 3 with summaries → 3 collapsed,
    2 raw. Order from gather_patient_facts is preserved (date ASC)."""
    _seed_patient_with_encounters(fake_store, n_encounters=5)
    _seed_encounter_summary(fake_store, patient_id="HN1", encounter_id="E1", markdown="# E1")
    _seed_encounter_summary(fake_store, patient_id="HN1", encounter_id="E3", markdown="# E3")
    _seed_encounter_summary(fake_store, patient_id="HN1", encounter_id="E5", markdown="# E5")

    from app.services.patient_facts import gather_patient_facts_for_ai
    out = gather_patient_facts_for_ai("HN1")

    assert [e["encounterId"] for e in out["encounters"]] == ["E1", "E2", "E3", "E4", "E5"]
    reps = [e["representation"] for e in out["encounters"]]
    assert reps == ["summary", "raw_facts", "summary", "raw_facts", "summary"]

    # Patient header is always sent verbatim.
    assert out["patient"]["patient_id"] == "HN1"
    # chronicProblems is the dedup'd cross-encounter list (one row per condition).
    assert len(out["chronicProblems"]) == 5  # 5 distinct conditions, one per encounter


def test_prefer_summaries_false_returns_only_raw_facts(fake_store):
    """The flag lets callers force a full-raw view (e.g. for debug / audit).
    Even encounters that DO have summaries should ship raw facts."""
    _seed_patient_with_encounters(fake_store, n_encounters=2)
    _seed_encounter_summary(fake_store, patient_id="HN1", encounter_id="E1", markdown="# E1")

    from app.services.patient_facts import gather_patient_facts_for_ai
    out = gather_patient_facts_for_ai("HN1", prefer_summaries=False)

    assert {e["representation"] for e in out["encounters"]} == {"raw_facts"}


def test_only_latest_summary_per_encounter_is_used(fake_store):
    """If the same encounter has been summarised twice (re-ran), the most
    recent row wins. ORDER BY created_at DESC + first-wins."""
    _seed_patient_with_encounters(fake_store, n_encounters=1)
    # Two summaries for the same encounter. Newer should win.
    fake_store.patient_summaries.extend([
        {"id": "ps-old", "patient_id": "HN1", "kind": "summary", "encounter_id": "E1",
         "type": "brief", "model": "mock", "markdown": "# OLD", "payload": None,
         "evidence": None, "cost_usd": 0, "latency_ms": 1, "vault_path": None,
         "created_at": "2026-01-01T00:00:00Z"},
        {"id": "ps-new", "patient_id": "HN1", "kind": "summary", "encounter_id": "E1",
         "type": "brief", "model": "mock", "markdown": "# NEW", "payload": None,
         "evidence": None, "cost_usd": 0, "latency_ms": 1, "vault_path": None,
         "created_at": "2026-06-01T00:00:00Z"},
    ])

    from app.services.patient_facts import gather_patient_facts_for_ai
    out = gather_patient_facts_for_ai("HN1")
    assert out["encounters"][0]["markdown"] == "# NEW"


def test_patient_header_is_always_present(fake_store):
    """patient row, allergies, chronicProblems must always be at the top level
    so the model has cross-encounter context regardless of compression."""
    _seed_patient_with_encounters(fake_store, n_encounters=1)
    fake_store.facts.append({
        "id": "a-1", "patient_id": "HN1", "encounter_id": "E1",
        "type": "allergy", "value": "Penicillin", "normalized_code": None,
        "review_status": "ai_suggested", "extra": {}, "confidence": 0.95,
        "evidence_text": "drug allergy", "created_at": "2026-01-01T08:00:00Z",
    })
    _seed_encounter_summary(fake_store, patient_id="HN1", encounter_id="E1")

    from app.services.patient_facts import gather_patient_facts_for_ai
    out = gather_patient_facts_for_ai("HN1")

    assert out["patient"]["patient_id"] == "HN1"
    assert len(out["allergies"]) == 1
    assert out["allergies"][0]["value"] == "Penicillin"
    assert len(out["chronicProblems"]) == 1
    # Even though E1 collapsed to summary, the patient header still has the
    # cross-encounter problems / allergies.
    assert out["encounters"][0]["representation"] == "summary"


def test_token_budget_drops_when_most_encounters_have_summaries(fake_store):
    """Acceptance: ≥60% reduction on a 50-encounter patient when most have summaries.

    We approximate "token budget" via the JSON-serialised length of the
    payload — that's what the provider actually ships, and it's a stable proxy
    for tokens (constant ratio for the same character set).
    """
    fake_store.patients["HN1"] = {"patient_id": "HN1", "name": "Long History"}
    for i in range(1, 51):
        eid = f"E{i}"
        fake_store.encounters[eid] = {
            "encounter_id": eid, "patient_id": "HN1", "type": "visit",
            "date_time": f"2024-{(i % 12) + 1:02d}-01T08:00:00+00:00",
            "department": "IM", "provider": "Dr A",
        }
        # Realistic-ish payload: 12 facts per encounter (mix of types)
        # with evidence text averaging ~120 chars — matches what the extractor
        # actually emits on real EMRs (see test_dedupe_* fixtures).
        for j in range(12):
            fact_type = ("condition", "medication", "observation", "procedure",
                         "plan", "diagnosis_candidate")[j % 6]
            fake_store.facts.append({
                "id": f"f-{i}-{j}", "patient_id": "HN1", "encounter_id": eid,
                "type": fact_type,
                "value": f"Fact about a clinical finding mentioned in {eid}",
                "normalized_code": f"CODE-{i}-{j}",
                "review_status": "ai_suggested", "extra": {"detail": "y" * 30},
                "confidence": 0.8,
                "evidence_text": ("clinical evidence snippet referenced in "
                                  "encounter " + eid + " " + "x" * 60),
                "created_at": f"2024-01-01T0{j % 10}:00:00Z",
            })
    # Summarise 40 / 50 encounters.
    for i in range(1, 41):
        fake_store.patient_summaries.append({
            "id": f"ps-{i}", "patient_id": "HN1", "kind": "summary",
            "encounter_id": f"E{i}", "type": "discharge", "model": "mock",
            # ~200 chars — typical brief summary.
            "markdown": "# Discharge summary\n- Stable\n- Continue meds\n- Follow up 2 wks" + " " * 60,
            "payload": None, "evidence": None, "cost_usd": 0, "latency_ms": 1,
            "vault_path": None, "created_at": "2026-01-01T00:00:00Z",
        })

    from app.services.patient_facts import gather_patient_facts_for_ai
    compressed = gather_patient_facts_for_ai("HN1")
    raw = gather_patient_facts_for_ai("HN1", prefer_summaries=False)

    compressed_size = len(json.dumps(compressed, default=str))
    raw_size = len(json.dumps(raw, default=str))
    reduction = 1 - (compressed_size / raw_size)
    assert reduction >= 0.6, (
        f"expected ≥60% reduction with 40/50 summaries; "
        f"got raw={raw_size} compressed={compressed_size} reduction={reduction:.2%}"
    )


# Compliance test for the hierarchical payload — verifying the de-identifier
# still runs cleanly on the new shape (collapsed-summary + raw_facts mix) —
# lives in test_deidentify_e2e.py where the fake Presidio fixture is wired up.
