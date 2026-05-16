from decimal import Decimal


def test_compute_cost_chat_model():
    from app.services.pricing import compute_cost
    rates = {"prompt_per_1m": Decimal("3.00"), "completion_per_1m": Decimal("15.00"), "embedding_per_1m": None}
    c = compute_cost(rates, prompt_tokens=1_000_000, completion_tokens=500_000)
    # 1.0 * 3 + 0.5 * 15 = 10.5
    assert c == Decimal("10.500000")


def test_compute_cost_embedding_model():
    from app.services.pricing import compute_cost
    rates = {"prompt_per_1m": None, "completion_per_1m": None, "embedding_per_1m": Decimal("0.02")}
    c = compute_cost(rates, embedding_tokens=1_000_000)
    assert c == Decimal("0.020000")


def test_compute_cost_returns_none_when_rates_missing():
    from app.services.pricing import compute_cost
    assert compute_cost(None, prompt_tokens=10) is None


def test_compute_cost_returns_none_when_all_rate_components_missing():
    from app.services.pricing import compute_cost
    rates = {"prompt_per_1m": None, "completion_per_1m": None, "embedding_per_1m": None}
    assert compute_cost(rates, prompt_tokens=100) is None


def test_compute_cost_handles_partial_rates():
    from app.services.pricing import compute_cost
    rates = {"prompt_per_1m": Decimal("1.0"), "completion_per_1m": None, "embedding_per_1m": None}
    # completion has no rate so its component is 0
    c = compute_cost(rates, prompt_tokens=2_000_000, completion_tokens=1_000_000)
    assert c == Decimal("2.000000")


def test_compute_cost_quantizes_to_six_dp():
    from app.services.pricing import compute_cost
    rates = {"prompt_per_1m": Decimal("0.15"), "completion_per_1m": Decimal("0.6"), "embedding_per_1m": None}
    c = compute_cost(rates, prompt_tokens=1234, completion_tokens=567)
    # exact: (1234/1e6)*0.15 + (567/1e6)*0.6 = 0.0001851 + 0.0003402 = 0.0005253
    assert c == Decimal("0.000525")  # round-half-even at 6dp


def test_upsert_load_and_list_via_fake_store(fake_store):
    from app.services.pricing import list_rates, load_rates, upsert_rate, delete_rate

    upsert_rate(model="acme/super-llm", prompt_per_1m=1.23, completion_per_1m=4.56, source="manual")
    rows = list_rates()
    assert any(r["model"] == "acme/super-llm" for r in rows)

    loaded = load_rates("acme/super-llm")
    assert loaded is not None
    # rates come back; types are whatever the fake stored, which is fine — values match.
    assert float(loaded["prompt_per_1m"]) == 1.23
    assert float(loaded["completion_per_1m"]) == 4.56

    # NULL component preserves existing value (COALESCE-style)
    upsert_rate(model="acme/super-llm", prompt_per_1m=9.99)  # completion left None
    after = load_rates("acme/super-llm")
    assert float(after["prompt_per_1m"]) == 9.99
    assert float(after["completion_per_1m"]) == 4.56

    delete_rate("acme/super-llm")
    assert load_rates("acme/super-llm") is None
