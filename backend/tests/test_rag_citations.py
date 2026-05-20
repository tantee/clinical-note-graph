"""Unit tests for the citation helpers — pure functions on top of LLM
markdown output. No DB or HTTP."""
from __future__ import annotations


def test_parses_simple_marker():
    from app.services.rag import parse_cited_indices
    assert parse_cited_indices("Patient has diabetes [1].") == {1}


def test_parses_multiple_markers():
    from app.services.rag import parse_cited_indices
    assert parse_cited_indices("[1][2] are both relevant; see also [3].") == {1, 2, 3}


def test_ignores_non_index_brackets():
    from app.services.rag import parse_cited_indices
    # 'abc' is not a digit run; '12.5' contains '.' so the digit run is '12'.
    # The regex \[(\d+)\] captures contiguous digit runs only, so '[12.5]'
    # is NOT matched as a whole; we expect the empty set.
    assert parse_cited_indices("Note [abc] and [12.5] not indices.") == set()


def test_handles_no_markers():
    from app.services.rag import parse_cited_indices
    assert parse_cited_indices("No citations here.") == set()


def test_build_citations_flags_cited_chunks():
    from app.services.rag import build_citations
    chunks = [
        {"content": "Patient has hypertension.",
         "ref_type": "note", "ref_id": "patients/HN1/visits/2026-05-17.md",
         "similarity": 0.88},
        {"content": "BP 152/92 on admission.",
         "ref_type": "note", "ref_id": "patients/HN1/visits/2026-05-17.md",
         "similarity": 0.72},
    ]
    answer = "Patient has hypertension [1]."
    cits = build_citations(chunks, answer)
    assert len(cits) == 2
    assert cits[0].n == 1 and cits[0].cited is True
    assert cits[0].score == 0.88
    assert cits[0].refType == "note"
    assert cits[1].n == 2 and cits[1].cited is False
