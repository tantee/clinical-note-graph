from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.db.postgres import db_session
from app.services.runtime_config import effective as effective_settings


def _vault_summary_path(
    patient_id: str, kind: str, summary_type: str | None,
    encounter_id: str | None, created_at: datetime,
) -> Path:
    """Encounter-scoped: patients/<HN>/encounters/<eid>/<kind>-<type>.md (overwritten).
       Patient-level summary: patients/<HN>/summaries/<ts>-summary[-<type>].md
       Patient-level coding:  patients/<HN>/coding/patient-level-<ts>.md
                              (per #27 Part A — colocates with encounter-level
                               coding/<ts>.md under the same `coding/` folder)."""
    settings = effective_settings()
    safe_pid = re.sub(r"[^A-Za-z0-9._-]+", "-", patient_id)
    suffix = f"-{summary_type}" if summary_type else ""
    if encounter_id:
        safe_eid = re.sub(r"[^A-Za-z0-9._-]+", "-", encounter_id)
        return (Path(settings.VAULT_PATH) / "patients" / safe_pid /
                "encounters" / safe_eid / f"{kind}{suffix}.md")
    date_str = created_at.strftime("%Y-%m-%d-%H%M%S")
    if kind == "coding":
        return (Path(settings.VAULT_PATH) / "patients" / safe_pid /
                "coding" / f"patient-level-{date_str}.md")
    return (Path(settings.VAULT_PATH) / "patients" / safe_pid /
            "summaries" / f"{date_str}-{kind}{suffix}.md")


def _write_summary_markdown(path: Path, *, patient_id: str, kind: str, summary_type: str | None,
                            model: str | None, cost_usd, latency_ms: int | None, body_md: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        f"patientId: {patient_id}",
        f"kind: {kind}",
    ]
    if summary_type:
        lines.append(f"type: {summary_type}")
    if model:
        lines.append(f"model: {model}")
    if cost_usd is not None:
        lines.append(f"costUsd: {cost_usd}")
    if latency_ms is not None:
        lines.append(f"latencyMs: {latency_ms}")
    lines.append(f"createdAt: {datetime.now(timezone.utc).isoformat()}")
    lines.append("aiAssisted: true")
    lines.append("---")
    lines.append("")
    lines.append(body_md)
    path.write_text("\n".join(lines), encoding="utf-8")


def _render_coding_markdown(payload: dict[str, Any]) -> str:
    """Render the coding response as a small markdown summary for the vault."""
    lines: list[str] = ["# Suggested coding", ""]
    primary = payload.get("primaryDiagnosis")
    if primary:
        codes = []
        if primary.get("icd10"):
            codes.append(f"ICD-10 {primary['icd10']}")
        if primary.get("snomed"):
            codes.append(f"SNOMED {primary['snomed']}")
        suffix = f" ({', '.join(codes)})" if codes else ""
        lines.append(f"**Primary:** {primary.get('condition', '?')}{suffix}")
        lines.append("")
    if payload.get("secondaryDiagnoses"):
        lines.append("## Secondary")
        for d in payload["secondaryDiagnoses"]:
            codes = []
            if d.get("icd10"):
                codes.append(f"ICD-10 {d['icd10']}")
            if d.get("snomed"):
                codes.append(f"SNOMED {d['snomed']}")
            suffix = f" ({', '.join(codes)})" if codes else ""
            lines.append(f"- {d.get('condition', '?')}{suffix}")
    if payload.get("warnings"):
        lines.append("")
        lines.append("## Warnings")
        for w in payload["warnings"]:
            lines.append(f"- {w}")
    return "\n".join(lines)


def save_summary(
    *, patient_id: str, summary_type: str, markdown: str,
    evidence: dict[str, Any] | None, model: str | None, cost_usd,
    latency_ms: int | None, encounter_id: str | None = None,
) -> dict[str, Any]:
    created = datetime.now(timezone.utc)
    vault_path = _vault_summary_path(patient_id, "summary", summary_type, encounter_id, created)
    try:
        _write_summary_markdown(
            vault_path,
            patient_id=patient_id, kind="summary", summary_type=summary_type,
            model=model, cost_usd=cost_usd, latency_ms=latency_ms, body_md=markdown,
        )
        settings = effective_settings()
        rel_path = str(vault_path.relative_to(settings.VAULT_PATH))
    except Exception:
        rel_path = None
    with db_session() as s:
        row = s.execute(
            text(
                """
                INSERT INTO patient_summaries
                    (patient_id, kind, type, encounter_id, model, markdown,
                     evidence, cost_usd, latency_ms, vault_path)
                VALUES
                    (:pid, 'summary', :tp, :eid, :mdl, :md,
                     CAST(:ev AS jsonb), :cost, :lat, :vp)
                RETURNING id, created_at
                """
            ),
            {
                "pid": patient_id, "tp": summary_type, "eid": encounter_id,
                "mdl": model, "md": markdown,
                "ev": json.dumps(evidence, default=str) if evidence is not None else None,
                "cost": cost_usd, "lat": latency_ms, "vp": rel_path,
            },
        ).mappings().first()
    return {"id": str(row["id"]), "createdAt": row["created_at"].isoformat(), "vaultPath": rel_path}


def save_coding(
    *, patient_id: str, payload: dict[str, Any], model: str | None,
    cost_usd, latency_ms: int | None, encounter_id: str | None = None,
) -> dict[str, Any]:
    # Coding gets a vault file in BOTH the encounter-scoped and the
    # patient-level case — clinicians want every coding run reflected in
    # Obsidian (issue #27 Part A: the patient's notes folder must be a
    # complete audit trail). _vault_summary_path picks the right destination.
    vault_path = None
    created = datetime.now(timezone.utc)
    path = _vault_summary_path(patient_id, "coding", None, encounter_id, created)
    try:
        md = _render_coding_markdown(payload)
        _write_summary_markdown(
            path, patient_id=patient_id, kind="coding", summary_type=None,
            model=model, cost_usd=cost_usd, latency_ms=latency_ms, body_md=md,
        )
        settings = effective_settings()
        vault_path = str(path.relative_to(settings.VAULT_PATH))
    except Exception:
        vault_path = None
    with db_session() as s:
        row = s.execute(
            text(
                """
                INSERT INTO patient_summaries
                    (patient_id, kind, encounter_id, model, payload,
                     cost_usd, latency_ms, vault_path)
                VALUES
                    (:pid, 'coding', :eid, :mdl, CAST(:p AS jsonb),
                     :cost, :lat, :vp)
                RETURNING id, created_at
                """
            ),
            {
                "pid": patient_id, "eid": encounter_id, "mdl": model,
                "p": json.dumps(payload, default=str),
                "cost": cost_usd, "lat": latency_ms, "vp": vault_path,
            },
        ).mappings().first()
    return {"id": str(row["id"]), "createdAt": row["created_at"].isoformat(), "vaultPath": vault_path}


def latest_summary(patient_id: str, encounter_id: str | None = None) -> dict[str, Any] | None:
    with db_session() as s:
        if encounter_id is None:
            row = s.execute(
                text(
                    "SELECT id, type, model, markdown, evidence, cost_usd, latency_ms, "
                    "vault_path, created_at FROM patient_summaries "
                    "WHERE patient_id = :pid AND kind = 'summary' "
                    "AND encounter_id IS NULL ORDER BY created_at DESC LIMIT 1"
                ),
                {"pid": patient_id},
            ).mappings().first()
        else:
            row = s.execute(
                text(
                    "SELECT id, type, model, markdown, evidence, cost_usd, latency_ms, "
                    "vault_path, created_at FROM patient_summaries "
                    "WHERE patient_id = :pid AND kind = 'summary' "
                    "AND encounter_id = :eid ORDER BY created_at DESC LIMIT 1"
                ),
                {"pid": patient_id, "eid": encounter_id},
            ).mappings().first()
    if not row:
        return None
    return {
        "id": str(row["id"]),
        "type": row["type"],
        "model": row["model"],
        "markdown": row["markdown"],
        "evidence": row["evidence"],
        "costUsd": float(row["cost_usd"]) if row["cost_usd"] is not None else None,
        "latencyMs": row["latency_ms"],
        "vaultPath": row["vault_path"],
        "createdAt": row["created_at"].isoformat(),
    }


def latest_coding(patient_id: str, encounter_id: str | None = None) -> dict[str, Any] | None:
    with db_session() as s:
        if encounter_id is None:
            row = s.execute(
                text(
                    "SELECT id, model, payload, cost_usd, latency_ms, created_at "
                    "FROM patient_summaries "
                    "WHERE patient_id = :pid AND kind = 'coding' "
                    "AND encounter_id IS NULL ORDER BY created_at DESC LIMIT 1"
                ),
                {"pid": patient_id},
            ).mappings().first()
        else:
            row = s.execute(
                text(
                    "SELECT id, model, payload, cost_usd, latency_ms, created_at "
                    "FROM patient_summaries "
                    "WHERE patient_id = :pid AND kind = 'coding' "
                    "AND encounter_id = :eid ORDER BY created_at DESC LIMIT 1"
                ),
                {"pid": patient_id, "eid": encounter_id},
            ).mappings().first()
    if not row:
        return None
    return {
        "id": str(row["id"]),
        "model": row["model"],
        "payload": row["payload"],
        "costUsd": float(row["cost_usd"]) if row["cost_usd"] is not None else None,
        "latencyMs": row["latency_ms"],
        "createdAt": row["created_at"].isoformat(),
    }
