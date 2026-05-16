from __future__ import annotations

import base64
import io
import zipfile
from typing import Any

from sqlalchemy import text

from app.config import get_settings
from app.db.postgres import db_session
from app.schemas.coding import CodingSuggestRequest, ExportRequest, SummaryRequest
from app.services.coding import suggest_coding
from app.services.graph_updater import fetch_patient_graph
from app.services.patient_facts import gather_patient_facts
from app.services.summary import make_summary
from app.utils.vault import patient_root


async def run_export(req: ExportRequest) -> dict[str, Any]:
    if req.exportType == "summary":
        s = await make_summary(req.patientId, SummaryRequest(type="detailed"))
        return {
            "format": "json+markdown",
            "patientId": req.patientId,
            "markdown": s.markdown,
            "data": s.model_dump(by_alias=True)["json"],
        }
    if req.exportType == "coding":
        c = await suggest_coding(req.patientId, CodingSuggestRequest())
        return {"format": "json", "patientId": req.patientId, "data": c.model_dump()}
    if req.exportType == "graph":
        return {"format": "json", "patientId": req.patientId, "data": fetch_patient_graph(req.patientId)}
    if req.exportType == "fhir_bundle":
        return {"format": "fhir", "patientId": req.patientId, "data": _to_fhir_bundle(req.patientId)}
    if req.exportType == "markdown_vault":
        return {"format": "zip-base64", "patientId": req.patientId, "data": _vault_zip_base64(req.patientId)}
    if req.exportType == "custom":
        profile = _get_profile(req.profileId or "default-summary")
        return await _run_custom_export(req.patientId, profile)
    return {"format": "json", "data": {}}


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
