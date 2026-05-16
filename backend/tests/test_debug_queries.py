def _seed(fake_store):
    fake_store.ai_outputs.extend([
        {"id": "1", "call_type": "extract", "model": "gpt-4o-mini",
         "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150,
         "latency_ms": 1200, "cost_usd": 0.001, "error": None,
         "created_at": "2026-05-15", "job_id": None, "patient_id": "HN1", "document_id": "d1"},
        {"id": "2", "call_type": "extract", "model": "gpt-4o-mini",
         "prompt_tokens": 200, "completion_tokens": 80, "total_tokens": 280,
         "latency_ms": 1500, "cost_usd": 0.0015, "error": None,
         "created_at": "2026-05-15", "job_id": None, "patient_id": "HN1", "document_id": "d1"},
        {"id": "3", "call_type": "embed", "model": "openai/text-embedding-3-small",
         "prompt_tokens": 30, "completion_tokens": None, "total_tokens": 30,
         "latency_ms": 200, "cost_usd": 0.000001, "error": None,
         "created_at": "2026-05-15", "job_id": None, "patient_id": "HN1", "document_id": None},
    ])


def test_summary_aggregates(fake_store, isolated_vault):
    from app.services.debug_queries import summary
    _seed(fake_store)
    out = summary(start="2026-05-01", end="2026-05-31")
    assert out["total_calls"] == 3
    assert abs(out["total_cost_usd"] - 0.002501) < 1e-9
    assert out["failures"] == 0


def test_by_model_breakdown(fake_store):
    from app.services.debug_queries import by_model
    _seed(fake_store)
    rows = by_model(start=None, end=None)
    models = {r["model"] for r in rows}
    assert {"gpt-4o-mini", "openai/text-embedding-3-small"} <= models


def test_list_calls_filters_by_model(fake_store):
    from app.services.debug_queries import list_calls
    _seed(fake_store)
    rows = list_calls(start=None, end=None, model="gpt-4o-mini",
                      status=None, q=None, limit=10, offset=0)
    assert all(r["model"] == "gpt-4o-mini" for r in rows)
    assert len(rows) == 2


def test_list_calls_filters_failures_only(fake_store):
    from app.services.debug_queries import list_calls
    fake_store.ai_outputs.extend([
        {"id": "9", "model": "x", "call_type": "extract", "prompt_tokens": 1, "completion_tokens": None,
         "total_tokens": 1, "latency_ms": 0, "cost_usd": 0, "error": "boom",
         "created_at": "2026-05-15", "job_id": None, "patient_id": None, "document_id": None},
        {"id": "10", "model": "x", "call_type": "extract", "prompt_tokens": 1, "completion_tokens": None,
         "total_tokens": 1, "latency_ms": 0, "cost_usd": 0, "error": None,
         "created_at": "2026-05-15", "job_id": None, "patient_id": None, "document_id": None},
    ])
    rows = list_calls(start=None, end=None, model=None, status="failed", q=None, limit=10, offset=0)
    assert all(r["error"] is not None for r in rows)
    assert len(rows) == 1
