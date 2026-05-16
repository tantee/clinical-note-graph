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
