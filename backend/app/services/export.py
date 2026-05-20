from __future__ import annotations

import base64
import io
import json
import logging
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.config import get_settings
from app.db.postgres import db_session
from app.schemas.coding import CodingSuggestRequest, ExportRequest, SummaryRequest
from app.services.coding import suggest_coding
from app.services.graph_updater import fetch_patient_graph
from app.services.patient_facts import gather_patient_facts
from app.services.summary import make_summary
from app.utils.vault import patient_root, safe_seg

_log = logging.getLogger(__name__)


async def run_export(req: ExportRequest) -> dict[str, Any]:
    if req.exportType == "summary":
        s = await make_summary(req.patientId, SummaryRequest(type="detailed"))
        result = {
            "format": "json+markdown",
            "patientId": req.patientId,
            "markdown": s.markdown,
            "data": s.model_dump(by_alias=True)["json"],
        }
    elif req.exportType == "coding":
        c = await suggest_coding(req.patientId, CodingSuggestRequest())
        result = {"format": "json", "patientId": req.patientId, "data": c.model_dump()}
    elif req.exportType == "graph":
        result = {"format": "json", "patientId": req.patientId, "data": fetch_patient_graph(req.patientId)}
    elif req.exportType == "fhir_bundle":
        result = {"format": "fhir", "patientId": req.patientId, "data": _to_fhir_bundle(req.patientId)}
    elif req.exportType == "markdown_vault":
        result = {"format": "zip-base64", "patientId": req.patientId, "data": _vault_zip_base64(req.patientId)}
    elif req.exportType == "custom":
        profile = _get_profile(req.profileId or "default-summary")
        result = await _run_custom_export(req.patientId, profile)
        result.setdefault("patientId", req.patientId)
    else:
        return {"format": "json", "data": {}}

    # Mirror every export bundle into the patient's vault as an audit trail
    # (issue #27 Part A). The on-disk path is included in the response so the
    # UI can deep-link to it (Obsidian / file picker).
    vault_path = _mirror_export_to_vault(
        patient_id=req.patientId,
        export_type=req.exportType,
        profile_id=req.profileId if req.exportType == "custom" else None,
        result=result,
    )
    if vault_path is not None:
        result["vaultPath"] = vault_path
    return result


def _mirror_export_to_vault(
    *, patient_id: str, export_type: str, profile_id: str | None,
    result: dict[str, Any],
) -> str | None:
    """Write the export bundle into patients/<HN>/exports/<name>-<ts>.<ext>.

    Returns the vault-relative path on success, None if writing failed (vault
    misconfigured, disk full, etc) — never raises, because the export itself
    succeeded and the caller still needs to receive the bundle.
    """
    try:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
        name = safe_seg(profile_id) if (export_type == "custom" and profile_id) else export_type
        ext, body = _serialise_for_vault(result)
        if body is None:
            return None
        vault = get_settings().vault_dir
        path = vault / "patients" / safe_seg(patient_id) / "exports" / f"{name}-{ts}.{ext}"
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(body, bytes):
            path.write_bytes(body)
        else:
            path.write_text(body, encoding="utf-8")
        return str(path.relative_to(vault))
    except Exception as exc:  # pragma: no cover - defensive
        _log.warning("export vault mirror failed for %s/%s: %s", patient_id, export_type, exc)
        return None


def _serialise_for_vault(result: dict[str, Any]) -> tuple[str, bytes | str | None]:
    """Pick the right on-disk encoding for each export `format`.

    Returns (extension, body) or (extension, None) if there's nothing useful
    to persist (e.g. an empty default branch).
    """
    fmt = (result.get("format") or "").lower()
    if fmt == "zip-base64":
        # Round-trip the base64 back to bytes so the file is a real .zip
        # openable in Finder / Obsidian without an extra decode step.
        encoded = result.get("data") or ""
        try:
            return "zip", base64.b64decode(encoded)
        except Exception:
            return "zip", None
    if fmt == "markdown":
        return "md", result.get("data") or ""
    if fmt == "json+markdown":
        # The summary export is a hybrid — markdown is the human-readable
        # primary; serialise the structured `data` alongside as `.json` would
        # split the artefact. Embed the data after the markdown body in a
        # fenced block so it's one file the reader can scroll through.
        md = result.get("markdown") or ""
        data = result.get("data") or {}
        return "md", (
            md.rstrip() + "\n\n## Structured data\n\n```json\n"
            + json.dumps(data, indent=2, default=str) + "\n```\n"
        )
    if fmt == "fhir":
        return "json", json.dumps(result.get("data") or {}, indent=2, default=str)
    if fmt == "json":
        return "json", json.dumps(result.get("data") or {}, indent=2, default=str)
    return "json", None


def _get_profile(profile_id: str) -> dict[str, Any]:
    with db_session() as s:
        row = s.execute(
            text("SELECT profile_id, name, config FROM export_profiles WHERE profile_id = :p"),
            {"p": profile_id},
        ).mappings().first()
    if not row:
        return {
            "profileId": "default-summary",
            "name": "Default Summary",
            "config": {"fields": ["problems", "medications"], "format": "json", "includeEvidence": True},
        }
    return {"profileId": row["profile_id"], "name": row["name"], "config": row["config"]}


async def _run_custom_export(patient_id: str, profile: dict[str, Any]) -> dict[str, Any]:
    cfg = profile["config"]
    facts = gather_patient_facts(patient_id)
    fields = cfg.get("fields", ["problems", "medications"])
    data = {f: facts.get(f, []) for f in fields}
    if cfg.get("format") == "markdown":
        lines = [f"# Patient {patient_id} export"]
        for f in fields:
            lines.append(f"\n## {f}")
            for item in data[f]:
                label = item.get("value") or item.get("name")
                lines.append(f"- {label or '(unnamed)'}")
        return {"format": "markdown", "data": "\n".join(lines), "profile": profile["profileId"]}
    return {"format": "json", "data": data, "profile": profile["profileId"]}


def _vault_zip_base64(patient_id: str) -> str:
    vault = get_settings().vault_dir
    root = patient_root(patient_id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if root.exists():
            for p in root.rglob("*"):
                if p.is_file():
                    zf.write(p, p.relative_to(vault))
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _to_fhir_bundle(patient_id: str) -> dict[str, Any]:
    facts = gather_patient_facts(patient_id)
    patient = facts.get("patient") or {"patient_id": patient_id}
    entries: list[dict[str, Any]] = [{
        "resource": {
            "resourceType": "Patient",
            "id": patient.get("patient_id"),
            "name": [{"text": patient.get("name") or patient.get("patient_id")}],
            "gender": patient.get("gender"),
            "birthDate": str(patient.get("birth_date")) if patient.get("birth_date") else None,
        },
    }]
    for c in facts["problems"]:
        coding = []
        if c.get("normalized_code"):
            coding = [{"system": _system_url(c.get("coding_system")), "code": c.get("normalized_code")}]
        entries.append({"resource": {
            "resourceType": "Condition",
            "subject": {"reference": f"Patient/{patient_id}"},
            "code": {"text": c["value"], "coding": coding},
            "evidence": [{"detail": [{"display": c.get("evidence_text")}]}] if c.get("evidence_text") else [],
        }})
    for m in facts["medications"]:
        extra = m.get("extra") or {}
        entries.append({"resource": {
            "resourceType": "MedicationStatement",
            "subject": {"reference": f"Patient/{patient_id}"},
            "medicationCodeableConcept": {"text": m["value"]},
            "status": extra.get("action", "active"),
        }})
    for o in facts["observations"]:
        extra = o.get("extra") or {}
        entries.append({"resource": {
            "resourceType": "Observation",
            "subject": {"reference": f"Patient/{patient_id}"},
            "code": {"text": extra.get("name") or o["value"]},
            "valueString": str(extra.get("value", o["value"])),
        }})
    return {"resourceType": "Bundle", "type": "collection", "entry": entries}


def _system_url(system: str | None) -> str:
    return {
        "ICD10": "http://hl7.org/fhir/sid/icd-10",
        "SNOMEDCT": "http://snomed.info/sct",
        "LOINC": "http://loinc.org",
        "RxNorm": "http://www.nlm.nih.gov/research/umls/rxnorm",
    }.get(system or "", "")
