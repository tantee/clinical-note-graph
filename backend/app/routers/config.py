from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.db.helpers import audit, j
from app.db.postgres import db_session
from app.schemas.coding import ExportProfilePayload
from app.services import runtime_config

router = APIRouter(prefix="/api/config", tags=["config"])


_PATCHABLE = {
    "AI_PROVIDER", "AI_BASE_URL", "AI_API_KEY", "AI_MODEL", "AI_EMBEDDING_MODEL",
    "VAULT_PATH",
    "CODING_ICD10", "CODING_SNOMEDCT", "CODING_LOINC", "CODING_RXNORM",
}


class ConfigPatch(BaseModel):
    AI_PROVIDER: str | None = None
    AI_BASE_URL: str | None = None
    AI_API_KEY: str | None = Field(default=None, description="Set to null to clear; omit to leave unchanged")
    AI_MODEL: str | None = None
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
