from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.db.postgres import db_session
from app.services.runtime_config import effective as effective_settings


def _vault_summary_path(patient_id: str, kind: str, summary_type: str | None, created_at: datetime) -> Path:
    """`patients/<HN>/summaries/<YYYY-MM-DD-HHMMSS>-<kind>[-<type>].md` under VAULT_PATH."""
    settings = effective_settings()
    safe_pid = re.sub(r"[^A-Za-z0-9._-]+", "-", patient_id)
    date_str = created_at.strftime("%Y-%m-%d-%H%M%S")
    suffix = f"-{summary_type}" if summary_type else ""
    return Path(settings.VAULT_PATH) / "patients" / safe_pid / "summaries" / f"{date_str}-{kind}{suffix}.md"


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


def save_summary(
    *, patient_id: str, summary_type: str, markdown: str, evidence: dict[str, Any] | None,
    model: str | None, cost_usd, latency_ms: int | None,
) -> dict[str, Any]:
    created = datetime.now(timezone.utc)
    vault_path = _vault_summary_path(patient_id, "summary", summary_type, created)
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
                    (patient_id, kind, type, model, markdown, evidence, cost_usd, latency_ms, vault_path)
                VALUES
                    (:pid, 'summary', :tp, :mdl, :md, CAST(:ev AS jsonb), :cost, :lat, :vp)
                RETURNING id, created_at
                """
            ),
            {
                "pid": patient_id, "tp": summary_type, "mdl": model, "md": markdown,
                "ev": json.dumps(evidence) if evidence is not None else None,
                "cost": cost_usd, "lat": latency_ms, "vp": rel_path,
            },
        ).mappings().first()
    return {"id": str(row["id"]), "createdAt": row["created_at"].isoformat(), "vaultPath": rel_path}


def save_coding(
    *, patient_id: str, payload: dict[str, Any], model: str | None,
    cost_usd, latency_ms: int | None,
) -> dict[str, Any]:
    with db_session() as s:
        row = s.execute(
            text(
                """
                INSERT INTO patient_summaries
                    (patient_id, kind, model, payload, cost_usd, latency_ms)
                VALUES
                    (:pid, 'coding', :mdl, CAST(:p AS jsonb), :cost, :lat)
                RETURNING id, created_at
                """
            ),
            {
                "pid": patient_id, "mdl": model,
                "p": json.dumps(payload, default=str),
                "cost": cost_usd, "lat": latency_ms,
            },
        ).mappings().first()
    return {"id": str(row["id"]), "createdAt": row["created_at"].isoformat()}


def latest_summary(patient_id: str) -> dict[str, Any] | None:
    with db_session() as s:
        row = s.execute(
            text(
                """
                SELECT id, type, model, markdown, evidence, cost_usd, latency_ms, vault_path, created_at
                FROM patient_summaries
                WHERE patient_id = :pid AND kind = 'summary'
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"pid": patient_id},
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


def latest_coding(patient_id: str) -> dict[str, Any] | None:
    with db_session() as s:
        row = s.execute(
            text(
                """
                SELECT id, model, payload, cost_usd, latency_ms, created_at
                FROM patient_summaries
                WHERE patient_id = :pid AND kind = 'coding'
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"pid": patient_id},
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
