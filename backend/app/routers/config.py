from __future__ import annotations

import json
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.db.helpers import audit, j
from app.db.postgres import db_session
from app.schemas.coding import ExportProfilePayload
from app.services import runtime_config
from app.services.pricing import delete_rate, list_rates, upsert_rate

router = APIRouter(prefix="/api/config", tags=["config"])


_PATCHABLE = {
    "AI_PROVIDER", "AI_BASE_URL", "AI_API_KEY",
    "AI_MODEL", "AI_MODEL_EXTRACT", "AI_MODEL_SUMMARY", "AI_MODEL_CODING",
    "AI_EMBEDDING_MODEL",
    "VAULT_PATH",
    "CODING_ICD10", "CODING_SNOMEDCT", "CODING_LOINC", "CODING_RXNORM",
}


class ConfigPatch(BaseModel):
    AI_PROVIDER: str | None = None
    AI_BASE_URL: str | None = None
    AI_API_KEY: str | None = Field(default=None, description="Set to null to clear; omit to leave unchanged")
    AI_MODEL: str | None = None
    AI_MODEL_EXTRACT: str | None = Field(default=None, description="Override for EMR extract; blank → AI_MODEL")
    AI_MODEL_SUMMARY: str | None = Field(default=None, description="Override for summary; blank → AI_MODEL")
    AI_MODEL_CODING: str | None = Field(default=None, description="Override for coding suggest; blank → AI_MODEL")
    AI_EMBEDDING_MODEL: str | None = None
    VAULT_PATH: str | None = None
    CODING_ICD10: bool | None = None
    CODING_SNOMEDCT: bool | None = None
    CODING_LOINC: bool | None = None
    CODING_RXNORM: bool | None = None


def _mask_secret(value: str | None) -> str | None:
    if not value:
        return value
    if len(value) <= 4:
        return "***"
    return "***" + value[-4:]


def _serialise(settings) -> dict[str, Any]:
    data = settings.model_dump()
    data["AI_API_KEY"] = _mask_secret(data.get("AI_API_KEY"))
    return data


@router.get("")
def get_config() -> dict[str, Any]:
    eff = runtime_config.effective()
    return {
        "settings": _serialise(eff),
        "overrides": runtime_config.overrides_snapshot(),
    }


@router.patch("")
def patch_config(patch: ConfigPatch) -> dict[str, Any]:
    sent = patch.model_dump(exclude_unset=True)
    updated: list[str] = []
    with db_session() as sess:
        for key, value in sent.items():
            if key not in _PATCHABLE:
                continue
            sess.execute(
                text(
                    """
                    INSERT INTO app_config (key, value, updated_at)
                    VALUES (:k, CAST(:v AS jsonb), now())
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
                    """
                ),
                {"k": key, "v": j(value)},
            )
            updated.append(key)
        audit(sess, action="CONFIG_UPDATED", target_type="config", target_id="*", payload={"keys": updated})
    runtime_config.invalidate()
    return {"updated": updated, "current": get_config()}


@router.get("/export-profiles")
def list_export_profiles() -> list[dict[str, Any]]:
    with db_session() as s:
        rows = s.execute(text("SELECT profile_id, name, config FROM export_profiles ORDER BY name")).mappings().all()
    return [dict(r) for r in rows]


@router.put("/export-profiles/{profile_id}")
def upsert_export_profile(profile_id: str, payload: ExportProfilePayload) -> dict[str, Any]:
    if payload.profileId != profile_id:
        raise HTTPException(status_code=400, detail="profile_id mismatch")
    with db_session() as s:
        s.execute(
            text(
                """
                INSERT INTO export_profiles (profile_id, name, config, updated_at)
                VALUES (:p, :n, CAST(:c AS jsonb), now())
                ON CONFLICT (profile_id) DO UPDATE SET name = EXCLUDED.name, config = EXCLUDED.config, updated_at = now()
                """
            ),
            {"p": payload.profileId, "n": payload.name, "c": j(payload.config)},
        )
        audit(s, action="EXPORT_PROFILE_UPSERT", target_type="export_profile", target_id=payload.profileId, payload=None)
    return {"profileId": payload.profileId}


@router.delete("/export-profiles/{profile_id}")
def delete_export_profile(profile_id: str) -> dict[str, Any]:
    with db_session() as s:
        s.execute(text("DELETE FROM export_profiles WHERE profile_id = :p"), {"p": profile_id})
        audit(s, action="EXPORT_PROFILE_DELETE", target_type="export_profile", target_id=profile_id, payload=None)
    return {"deleted": profile_id}


class PricingPatch(BaseModel):
    prompt_per_1m: float | None = None
    completion_per_1m: float | None = None
    embedding_per_1m: float | None = None
    source: str | None = "manual"


def _serialise_rate(row: dict) -> dict:
    def _f(v):
        return float(v) if v is not None else None
    return {
        "model": row["model"],
        "prompt_per_1m": _f(row.get("prompt_per_1m")),
        "completion_per_1m": _f(row.get("completion_per_1m")),
        "embedding_per_1m": _f(row.get("embedding_per_1m")),
        "source": row.get("source"),
        "updated_at": str(row["updated_at"]) if row.get("updated_at") else None,
    }


@router.get("/pricing")
def get_pricing() -> list[dict]:
    return [_serialise_rate(r) for r in list_rates()]


@router.put("/pricing/{model:path}")
def put_pricing(model: str, body: PricingPatch) -> dict:
    upsert_rate(
        model=model,
        prompt_per_1m=body.prompt_per_1m,
        completion_per_1m=body.completion_per_1m,
        embedding_per_1m=body.embedding_per_1m,
        source=body.source or "manual",
    )
    return {"model": model}


@router.delete("/pricing/{model:path}")
def del_pricing(model: str) -> dict:
    delete_rate(model)
    return {"deleted": model}


@router.post("/pricing/refresh-openrouter")
async def refresh_openrouter() -> dict:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get("https://openrouter.ai/api/v1/models")
        r.raise_for_status()
        data = r.json()
    upserted = 0
    for entry in data.get("data", []) or []:
        model = entry.get("id")
        if not model:
            continue
        pricing = entry.get("pricing") or {}
        is_embedding = "embedding" in model.lower()
        try:
            prompt = float(pricing.get("prompt")) if pricing.get("prompt") is not None else None
            completion = float(pricing.get("completion")) if pricing.get("completion") is not None else None
        except (TypeError, ValueError):
            continue
        if is_embedding:
            upsert_rate(
                model=model,
                prompt_per_1m=None,
                completion_per_1m=None,
                embedding_per_1m=(prompt * 1e6) if prompt is not None else None,
                source="openrouter",
            )
        else:
            upsert_rate(
                model=model,
                prompt_per_1m=(prompt * 1e6) if prompt is not None else None,
                completion_per_1m=(completion * 1e6) if completion is not None else None,
                embedding_per_1m=None,
                source="openrouter",
            )
        upserted += 1
    return {"upserted": upserted, "source": "openrouter"}
