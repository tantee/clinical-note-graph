def _seed(fake_store):
    fake_store.ai_outputs.extend([
        {"id": "1", "call_type": "extract", "model": "gpt-4o-mini",
         "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150,
         "latency_ms": 1200, "cost_usd": 0.001, "error": None,
         "created_at": "2026-05-15", "job_id": None, "patient_id": "HN1", "document_id": "d1"},
        {"id": "2", "call_type": "embed", "model": "openai/text-embedding-3-small",
         "prompt_tokens": 30, "completion_tokens": None, "total_tokens": 30,
         "latency_ms": 200, "cost_usd": 0.000001, "error": None,
         "created_at": "2026-05-15", "job_id": None, "patient_id": "HN1", "document_id": None},
    ])


def test_summary_endpoint(app_client, fake_store):
    _seed(fake_store)
    r = app_client.get("/api/debug/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["total_calls"] == 2


def test_by_model_endpoint(app_client, fake_store):
    _seed(fake_store)
    r = app_client.get("/api/debug/by-model")
    assert r.status_code == 200
    rows = r.json()
    assert {row["model"] for row in rows} >= {"gpt-4o-mini"}


def test_ai_calls_list_and_filter(app_client, fake_store):
    _seed(fake_store)
    r = app_client.get("/api/debug/ai-calls?model=gpt-4o-mini")
    assert r.status_code == 200
    rows = r.json()
    assert all(row["model"] == "gpt-4o-mini" for row in rows)


def test_ai_calls_csv_streams(app_client, fake_store):
    _seed(fake_store)
    r = app_client.get("/api/debug/ai-calls.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert b"id,created_at" in r.content[:200]


def test_protected_when_key_set(monkeypatch, fake_store):
    from app.config import get_settings
    get_settings.cache_clear()
    monkeypatch.setenv("API_KEY", "secret")
    monkeypatch.setenv("QUEUE_WORKERS", "0")
    from fastapi.testclient import TestClient
    from app.main import create_app
    with TestClient(create_app()) as c:
        assert c.get("/api/debug/summary").status_code == 401
        assert c.get("/api/debug/summary", headers={"X-API-Key": "secret"}).status_code == 200
