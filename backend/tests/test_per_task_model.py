"""Per-task model override falls back to AI_MODEL when blank."""

from __future__ import annotations

import asyncio
import json

import httpx


def _stub_post(captured: dict):
    async def fake_post(self, url, json=None, headers=None):
        captured["model"] = json["model"]
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.get("response_format")
                    and '{"patientId":"HN1","summary":"","problems":[],"medications":[],"observations":[],"procedures":[],"allergies":[],"plans":[],"diagnoses":[],"codingCandidates":[],"graphUpdates":[],"markdownUpdates":[],"warnings":[]}'
                    or "ok"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
            request=httpx.Request("POST", url),
        )
    return fake_post


def test_extract_uses_override_when_set(monkeypatch, fake_store):
    from app.config import Settings
    from app.services.ai_provider import OpenAICompatibleProvider

    captured: dict = {}
    monkeypatch.setattr(httpx.AsyncClient, "post", _stub_post(captured))

    settings = Settings(
        AI_PROVIDER="openai", AI_BASE_URL="https://x/v1", AI_API_KEY="k",
        AI_MODEL="default-model",
        AI_MODEL_EXTRACT="extract-only-model",
    )
    p = OpenAICompatibleProvider(settings)
    asyncio.run(p.extract(
        patient_id="HN1", encounter_type="admission",
        encounter_dt="2026-05-15T10:00:00+07:00", document_id="D1", content="x",
    ))
    assert captured["model"] == "extract-only-model"


def test_extract_falls_back_to_default_when_override_blank(monkeypatch, fake_store):
    from app.config import Settings
    from app.services.ai_provider import OpenAICompatibleProvider

    captured: dict = {}
    monkeypatch.setattr(httpx.AsyncClient, "post", _stub_post(captured))

    settings = Settings(
        AI_PROVIDER="openai", AI_BASE_URL="https://x/v1", AI_API_KEY="k",
        AI_MODEL="default-model",
        AI_MODEL_EXTRACT="",  # blank
    )
    p = OpenAICompatibleProvider(settings)
    asyncio.run(p.extract(
        patient_id="HN1", encounter_type="admission",
        encounter_dt="2026-05-15T10:00:00+07:00", document_id="D1", content="x",
    ))
    assert captured["model"] == "default-model"


def test_summary_and_coding_use_their_own_override(monkeypatch, fake_store):
    from app.config import Settings
    from app.services.ai_provider import OpenAICompatibleProvider

    captured: dict = {}
    monkeypatch.setattr(httpx.AsyncClient, "post", _stub_post(captured))

    settings = Settings(
        AI_PROVIDER="openai", AI_BASE_URL="https://x/v1", AI_API_KEY="k",
        AI_MODEL="default-model",
        AI_MODEL_SUMMARY="cheap-summary",
        AI_MODEL_CODING="strong-coding",
    )
    p = OpenAICompatibleProvider(settings)

    asyncio.run(p.summarize(patient_facts={}, summary_type="brief"))
    assert captured["model"] == "cheap-summary"

    asyncio.run(p.suggest_coding(patient_facts={"problems": []}, standards=["ICD10"]))
    assert captured["model"] == "strong-coding"


def test_config_patch_persists_per_task_override(app_client, fake_store):
    r = app_client.patch("/api/config", json={
        "AI_MODEL": "default",
        "AI_MODEL_EXTRACT": "claude-sonnet",
        "AI_MODEL_SUMMARY": "",
    })
    assert r.status_code == 200
    keys = r.json()["updated"]
    assert "AI_MODEL_EXTRACT" in keys
    assert fake_store.config["AI_MODEL_EXTRACT"] == "claude-sonnet"
