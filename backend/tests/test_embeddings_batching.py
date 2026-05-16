from __future__ import annotations

import asyncio
import pytest


class _Provider:
    """Counts embedding calls so we can assert concurrency."""

    def __init__(self):
        self.in_flight = 0
        self.max_in_flight = 0
        self.calls = 0

    async def embed(self, text, *, job_id=None, patient_id=None, ref_id=None):
        self.calls += 1
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0.01)
            return [0.1] * 8, None
        finally:
            self.in_flight -= 1


def test_embed_many_concurrency_bounded(monkeypatch, fake_store):
    from app.services import embeddings as emb_mod

    provider = _Provider()
    monkeypatch.setattr(emb_mod, "get_ai_provider", lambda *a, **k: provider)

    items = [{"ref_type": "fact", "ref_id": f"r{i}", "content": f"text {i}", "metadata": {}} for i in range(40)]
    n = asyncio.run(emb_mod.embed_and_store_many(patient_id="HN1", items=items))

    assert n == 40
    assert provider.calls == 40
    assert provider.max_in_flight <= 8  # bounded
    assert len(fake_store.embeddings) == 40


def test_embed_many_handles_empty():
    from app.services import embeddings as emb_mod

    n = asyncio.run(emb_mod.embed_and_store_many(patient_id="HN1", items=[]))
    assert n == 0


def test_embed_many_skips_failures(monkeypatch, fake_store):
    from app.services import embeddings as emb_mod

    class _BadProvider:
        async def embed(self, t, *, job_id=None, patient_id=None, ref_id=None):
            if "skipme" in t:
                raise RuntimeError("boom")
            return [0.0] * 4, None

    monkeypatch.setattr(emb_mod, "get_ai_provider", lambda *a, **k: _BadProvider())
    items = [
        {"ref_type": "fact", "ref_id": "a", "content": "good"},
        {"ref_type": "fact", "ref_id": "b", "content": "skipme"},
        {"ref_type": "fact", "ref_id": "c", "content": "good again"},
    ]
    n = asyncio.run(emb_mod.embed_and_store_many(patient_id="HN1", items=items))
    assert n == 2
