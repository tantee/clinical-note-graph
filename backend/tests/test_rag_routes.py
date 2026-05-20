"""Integration tests for POST /api/rag/ask and GET /api/search/patients."""
from __future__ import annotations


def _seed_embeddings(fake_store, patient_id: str = "HN-1", n: int = 3):
    """Seed the FakeStore with n embeddings for a patient + the patient row."""
    fake_store.patients[patient_id] = {"patient_id": patient_id, "name": "Test Patient"}
    for i in range(n):
        fake_store.embeddings.append({
            "patient_id": patient_id,
            "ref_type": "note",
            "ref_id": f"patients/{patient_id}/visits/2026-05-{17 + i:02d}.md",
            "content": f"Synthetic note {i}: hypertension and diabetes on metformin.",
        })


def test_rag_ask_happy_path(app_client, fake_store):
    _seed_embeddings(fake_store, n=3)
    r = app_client.post(
        "/api/rag/ask",
        json={"patientId": "HN-1", "question": "What conditions does this patient have?", "topK": 3},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["patientId"] == "HN-1"
    assert body["question"] == "What conditions does this patient have?"
    assert "answer" in body and len(body["answer"]) > 0
    assert len(body["citations"]) == 3
    # MockProvider deterministically cites [1].
    assert body["citations"][0]["cited"] is True
    assert body["citations"][1]["cited"] is False
    assert body["modelUsed"] == "mock-rag"


def test_rag_ask_404_when_patient_not_found(app_client, fake_store):
    r = app_client.post(
        "/api/rag/ask",
        json={"patientId": "HN-DOES-NOT-EXIST", "question": "?"},
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "Patient not found"


def test_rag_ask_422_when_no_embeddings(app_client, fake_store):
    fake_store.patients["HN-1"] = {"patient_id": "HN-1", "name": "No Embeddings"}
    # NOTE: no fake_store.embeddings entries — vector_search returns []
    r = app_client.post(
        "/api/rag/ask",
        json={"patientId": "HN-1", "question": "anything"},
    )
    assert r.status_code == 422
    assert "No embeddings" in r.json()["detail"]


def test_rag_ask_chat_mode_includes_history(app_client, fake_store):
    _seed_embeddings(fake_store, n=2)
    r = app_client.post(
        "/api/rag/ask",
        json={
            "patientId": "HN-1",
            "question": "And what dose?",
            "mode": "chat",
            "history": [
                {"role": "user", "content": "What medications?"},
                {"role": "assistant", "content": "Metformin and lisinopril."},
            ],
            "topK": 2,
        },
    )
    assert r.status_code == 200
    # The mock provider doesn't echo history, but the request validates and the
    # citation count matches topK (smoke check that history did not block).
    assert len(r.json()["citations"]) == 2


def test_search_patients_returns_ranked_results(app_client, fake_store):
    fake_store.patients["HN-1"] = {"patient_id": "HN-1", "name": "Alpha"}
    fake_store.patients["HN-2"] = {"patient_id": "HN-2", "name": "Beta"}
    fake_store.prime_patient_search_results([
        {"patient_id": "HN-1", "name": "Alpha", "score": 0.91,
         "top_snippets": [
             {"refType": "note", "refId": "patients/HN-1/visits/x.md",
              "content": "snippet", "score": 0.91}
         ]},
        {"patient_id": "HN-2", "name": "Beta", "score": 0.74,
         "top_snippets": [
             {"refType": "note", "refId": "patients/HN-2/visits/y.md",
              "content": "snippet", "score": 0.74}
         ]},
    ])
    r = app_client.get("/api/search/patients", params={"q": "diabetes"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["results"]) == 2
    assert body["results"][0]["patientId"] == "HN-1"
    assert body["results"][0]["score"] > body["results"][1]["score"]
    assert body["query"] == "diabetes"


def test_search_patients_empty_query_422(app_client, fake_store):
    r = app_client.get("/api/search/patients", params={"q": ""})
    assert r.status_code == 422  # Pydantic min_length=1


def test_search_patients_no_results(app_client, fake_store):
    fake_store.prime_patient_search_results([])
    r = app_client.get("/api/search/patients", params={"q": "nonexistent"})
    assert r.status_code == 200
    assert r.json()["results"] == []
