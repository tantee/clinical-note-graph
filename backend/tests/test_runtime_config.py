from __future__ import annotations

import time


def test_overrides_merged_without_mutating_cache(fake_store, monkeypatch):
    from app.config import get_settings
    from app.services import runtime_config

    base = get_settings()
    base_model = base.AI_MODEL
    fake_store.config["AI_MODEL"] = "gpt-test-override"
    runtime_config.invalidate()

    eff = runtime_config.effective()
    assert eff.AI_MODEL == "gpt-test-override"

    # The cached singleton is untouched.
    assert get_settings().AI_MODEL == base_model


def test_overrides_ttl_refreshes(fake_store):
    from app.services import runtime_config
    runtime_config.invalidate()
    assert runtime_config.effective().AI_MODEL  # primes the cache
    fake_store.config["AI_MODEL"] = "model-A"
    runtime_config.invalidate()
    assert runtime_config.effective().AI_MODEL == "model-A"
    fake_store.config["AI_MODEL"] = "model-B"
    runtime_config.invalidate()
    assert runtime_config.effective().AI_MODEL == "model-B"
