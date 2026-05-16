from decimal import Decimal


def test_list_pricing_returns_seeds(app_client, fake_store):
    fake_store.pricing = {
        "gpt-4o-mini": {
            "model": "gpt-4o-mini", "prompt_per_1m": 0.15, "completion_per_1m": 0.6,
            "embedding_per_1m": None, "source": "seed", "updated_at": "now",
        }
    }
    r = app_client.get("/api/config/pricing")
    assert r.status_code == 200
    body = r.json()
    assert any(p["model"] == "gpt-4o-mini" for p in body)


def test_upsert_pricing_via_put(app_client, fake_store):
    r = app_client.put("/api/config/pricing/acme", json={"prompt_per_1m": 1.0, "completion_per_1m": 2.0})
    assert r.status_code == 200
    assert fake_store.pricing["acme"]["prompt_per_1m"] == 1.0
    assert fake_store.pricing["acme"]["completion_per_1m"] == 2.0


def test_delete_pricing(app_client, fake_store):
    fake_store.pricing["foo"] = {"model": "foo", "source": "manual"}
    r = app_client.delete("/api/config/pricing/foo")
    assert r.status_code == 200
    assert "foo" not in fake_store.pricing


def test_openrouter_refresh_upserts(monkeypatch, app_client, fake_store):
    import httpx

    async def fake_get(self, url, headers=None):
        return httpx.Response(
            200,
            json={"data": [
                {"id": "anthropic/claude-3.5-sonnet", "pricing": {"prompt": "0.000003", "completion": "0.000015"}},
                {"id": "openai/gpt-4o-mini", "pricing": {"prompt": "0.00000015", "completion": "0.0000006"}},
                {"id": "openai/text-embedding-3-small", "pricing": {"prompt": "0.00000002"}},
            ]},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    r = app_client.post("/api/config/pricing/refresh-openrouter")
    assert r.status_code == 200
    body = r.json()
    assert body["upserted"] >= 3
    assert body["source"] == "openrouter"
    # 0.000003 USD/token -> 3.0 USD per 1M
    assert float(fake_store.pricing["anthropic/claude-3.5-sonnet"]["prompt_per_1m"]) == 3.0
    # Embedding model: prompt becomes embedding rate, prompt/completion stay NULL
    e = fake_store.pricing["openai/text-embedding-3-small"]
    assert float(e["embedding_per_1m"]) == 0.02
    assert e["prompt_per_1m"] is None or float(e.get("prompt_per_1m") or 0) == 0.0
