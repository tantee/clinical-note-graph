import asyncio
from decimal import Decimal


def _seed_pricing(fake_store):
    """Mirror the migration seeds the tests rely on.

    The migration 002 seeds 'mock' at $0 and 'gpt-4o-mini' at (0.15, 0.60).
    The FakeStore starts empty, so we seed it here for the metering tests.
    """
    fake_store.pricing["mock"] = {
        "model": "mock",
        "prompt_per_1m": Decimal("0"),
        "completion_per_1m": Decimal("0"),
        "embedding_per_1m": Decimal("0"),
        "source": "seed",
        "updated_at": "now",
    }
    fake_store.pricing["gpt-4o-mini"] = {
        "model": "gpt-4o-mini",
        "prompt_per_1m": Decimal("0.15"),
        "completion_per_1m": Decimal("0.60"),
        "embedding_per_1m": None,
        "source": "seed",
        "updated_at": "now",
    }


def test_mock_provider_extract_returns_record(isolated_vault, fake_store):
    from app.services.ai_provider import MockProvider

    _seed_pricing(fake_store)
    p = MockProvider()
    out, rec = asyncio.run(p.extract(
        patient_id="HN1", encounter_type="admission", encounter_dt="2026-05-15T10:00:00+07:00",
        document_id="D1", content="Type 2 diabetes mellitus. BP 152/95.",
        job_id=None,
    ))
    assert out["patientId"] == "HN1"
    assert rec.call_type == "extract"
    assert rec.model == "mock"
    assert rec.prompt_tokens is not None and rec.prompt_tokens > 0
    assert rec.latency_ms >= 0
    # Mock pricing seeded at $0 → cost is Decimal('0.000000') (not None)
    assert rec.cost_usd == Decimal("0.000000")
    # Was an ai_outputs row written?
    assert any(r["call_type"] == "extract" and r["model"] == "mock" for r in fake_store.ai_outputs)


def test_mock_provider_embed_returns_record(isolated_vault, fake_store):
    from app.services.ai_provider import MockProvider

    _seed_pricing(fake_store)
    p = MockProvider()
    vec, rec = asyncio.run(p.embed("hello world some text", job_id=None, patient_id="HN1", ref_id="r1"))
    assert vec == []  # mock returns no embedding
    assert rec.call_type == "embed"
    assert rec.prompt_tokens >= 1


def test_openai_provider_captures_usage(monkeypatch, fake_store):
    import httpx
    from app.config import Settings
    from app.services.ai_provider import OpenAICompatibleProvider

    _seed_pricing(fake_store)
    captured = {}

    async def fake_post(self, url, json=None, headers=None):
        captured["url"] = url
        captured["model"] = json["model"]
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"patientId":"HN1","summary":"ok","problems":[],"medications":[],"observations":[],"procedures":[],"allergies":[],"plans":[],"diagnoses":[],"codingCandidates":[],"graphUpdates":[],"markdownUpdates":[],"warnings":[]}'}}],
                "usage": {"prompt_tokens": 1200, "completion_tokens": 300, "total_tokens": 1500},
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    settings = Settings(AI_PROVIDER="openai", AI_BASE_URL="https://test/v1", AI_API_KEY="k", AI_MODEL="gpt-4o-mini")
    p = OpenAICompatibleProvider(settings)
    out, rec = asyncio.run(p.extract(
        patient_id="HN1", encounter_type="admission", encounter_dt="2026-05-15T10:00:00+07:00",
        document_id="D1", content="anything", job_id=None,
    ))
    assert captured["model"] == "gpt-4o-mini"
    assert rec.prompt_tokens == 1200
    assert rec.completion_tokens == 300
    assert rec.total_tokens == 1500
    # gpt-4o-mini is seeded: (1200/1e6)*0.15 + (300/1e6)*0.60 = 0.000180 + 0.000180 = 0.000360
    assert rec.cost_usd == Decimal("0.000360")
