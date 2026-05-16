from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy import text

from app.db.helpers import audit, j
from app.db.postgres import db_session
from app.schemas.emr import EMRIngestRequest
from app.schemas.extraction import ClinicalExtractionResult
from app.services.ai_provider import get_ai_provider
from app.services.embeddings import embed_and_store_many
from app.services.fhir_adapter import fhir_bundle_to_text, fhir_extract_patient
from app.services.graph_updater import update_graph_for_document
from app.services.markdown_generator import generate_markdown
from app.services.runtime_config import effective as effective_settings
from app.utils.datetime import iso

logger = logging.getLogger(__name__)


def _normalize_content(req: EMRIngestRequest) -> tuple[str, dict[str, Any] | None, dict[str, Any]]:
    if req.format == "fhir":
        bundle = req.content if isinstance(req.content, dict) else json.loads(str(req.content))
        return fhir_bundle_to_text(bundle), bundle, fhir_extract_patient(bundle)
    if req.format == "json":
        bundle = req.content if isinstance(req.content, dict) else json.loads(str(req.content))
        return json.dumps(bundle, default=str, ensure_ascii=False, indent=2), bundle, {}
    return str(req.content), None, {}


def _upsert_patient(s, patient: dict[str, Any]) -> None:
    s.execute(
        text(
            """
            INSERT INTO patients (patient_id, name, gender, birth_date, metadata, updated_at)
            VALUES (:patient_id, :name, :gender, :birth_date, CAST(:meta AS jsonb), now())
            ON CONFLICT (patient_id) DO UPDATE SET
                name = COALESCE(EXCLUDED.name, patients.name),
                gender = COALESCE(EXCLUDED.gender, patients.gender),
                birth_date = COALESCE(EXCLUDED.birth_date, patients.birth_date),
                metadata = patients.metadata || EXCLUDED.metadata,
                updated_at = now()
            """
        ),
        {
            "patient_id": patient["patientId"],
            "name": patient.get("name"),
            "gender": patient.get("gender"),
            "birth_date": patient.get("birthDate"),
            "meta": j(patient.get("metadata") or {}),
        },
    )


def _upsert_encounter(s, encounter: dict[str, Any], patient_id: str) -> str:
    enc_id = encounter.get("encounterId") or f"enc-{patient_id}-{uuid.uuid4().hex[:10]}"
    s.execute(
        text(
            """
            INSERT INTO encounters (encounter_id, patient_id, type, date_time, department, provider, metadata)
            VALUES (:eid, :pid, :type, :dt, :dept, :prov, CAST(:meta AS jsonb))
            ON CONFLICT (encounter_id) DO UPDATE SET
                type = EXCLUDED.type,
                date_time = EXCLUDED.date_time,
                department = EXCLUDED.department,
                provider = EXCLUDED.provider,
                metadata = encounters.metadata || EXCLUDED.metadata
            """
        ),
        {
            "eid": enc_id, "pid": patient_id,
            "type": encounter.get("type"), "dt": encounter.get("dateTime"),
            "dept": encounter.get("department"), "prov": encounter.get("provider"),
            "meta": j(encounter.get("metadata") or {}),
        },
    )
    return enc_id


def _upsert_document(s, *, patient_id: str, encounter_id: str, source: dict[str, Any], fmt: str, raw_content: str, raw_json: dict[str, Any] | None) -> str:
    src_doc_id = source.get("documentId")
    version = source.get("version") or "1"
    doc_id = src_doc_id or f"doc-{uuid.uuid4().hex[:12]}"
    s.execute(
        text(
            """
            INSERT INTO documents
                (document_id, patient_id, encounter_id, source_system, source_document_id, version, format, raw_content, raw_json)
            VALUES
                (:did, :pid, :eid, :sys, :sdid, :ver, :fmt, :raw, CAST(:rj AS jsonb))
            ON CONFLICT (patient_id, source_document_id, version) DO UPDATE SET
                encounter_id = EXCLUDED.encounter_id,
                raw_content = EXCLUDED.raw_content,
                raw_json = EXCLUDED.raw_json,
                received_at = now()
            """
        ),
        {
            "did": doc_id, "pid": patient_id, "eid": encounter_id,
            "sys": source.get("system"), "sdid": src_doc_id, "ver": version,
            "fmt": fmt, "raw": raw_content,
            "rj": j(raw_json) if raw_json is not None else None,
        },
    )
    return doc_id


_FACT_INSERT = """
INSERT INTO facts (patient_id, encounter_id, document_id, type, value, normalized_code,
                   coding_system, date_time, evidence_text, confidence, review_status, extra)
VALUES (:patient_id, :encounter_id, :document_id, :type, :value, :normalized_code,
        :coding_system, :date_time, :evidence_text, :confidence, :review_status, CAST(:extra AS jsonb))
"""


def _facts_rows(*, patient_id: str, encounter_id: str, document_id: str, ex: ClinicalExtractionResult) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def row(t: str, value: str, code: str | None, system: str | None, dt, evidence: str | None, conf: float, extra: dict[str, Any]):
        rows.append({
            "patient_id": patient_id, "encounter_id": encounter_id, "document_id": document_id,
            "type": t, "value": value, "normalized_code": code, "coding_system": system,
            "date_time": dt, "evidence_text": evidence, "confidence": conf,
            "review_status": "ai_suggested", "extra": j(extra),
        })

    for p in ex.problems:
        row("condition", p.value, p.normalizedCode, p.codingSystem, p.dateTime, p.evidenceText, p.confidence, p.extra)
    for m in ex.medications:
        row("medication", m.name, m.rxNorm, "RxNorm" if m.rxNorm else None, None, m.evidenceText, m.confidence,
            {"action": m.action, "dose": m.dose, "route": m.route, "frequency": m.frequency, "indication": m.indication})
    for o in ex.observations:
        row("observation", f"{o.name}={o.value}{(' '+o.unit) if o.unit else ''}",
            o.loinc, "LOINC" if o.loinc else None, o.dateTime, o.evidenceText, o.confidence,
            {"name": o.name, "value": o.value, "unit": o.unit, "abnormalFlag": o.abnormalFlag})
    for pr in ex.procedures:
        row("procedure", pr.value, pr.normalizedCode, pr.codingSystem, pr.dateTime, pr.evidenceText, pr.confidence, pr.extra)
    for a in ex.allergies:
        row("allergy", a.value, a.normalizedCode, a.codingSystem, a.dateTime, a.evidenceText, a.confidence, a.extra)
    for pl in ex.plans:
        row("plan", pl.description, None, None, None, pl.evidenceText, pl.confidence,
            {"category": pl.category, "addressesCondition": pl.addressesCondition})
    for d in ex.diagnoses:
        row("diagnosis_candidate", d.condition, d.icd10 or d.snomed,
            "ICD10" if d.icd10 else ("SNOMEDCT" if d.snomed else None),
            None, d.evidenceText, d.confidence, {"role": d.role, "rationale": d.rationale})
    for c in ex.codingCandidates:
        row("coding_candidate", c.display, c.code, c.system, None, None, c.confidence,
            {"forCondition": c.forCondition, "rationale": c.rationale})
    return rows


def _store_ai_output(s, *, document_id: str, patient_id: str, model: str, raw: dict[str, Any], valid: bool, errors: list[Any]) -> None:
    s.execute(
        text(
            "INSERT INTO ai_outputs (document_id, patient_id, prompt_template, model, raw_output, valid, validation_errors) "
            "VALUES (:d, :p, :pt, :m, CAST(:r AS jsonb), :v, CAST(:e AS jsonb))"
        ),
        {
            "d": document_id, "p": patient_id, "pt": "EMR_EXTRACTION", "m": model,
            "r": j(raw), "v": valid, "e": j(errors),
        },
    )


def _persist_pre_extraction(req: EMRIngestRequest) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str, dict[str, Any] | None]:
    """Stage 1: persist patient + encounter + raw document. Returns the canonical structures."""
    text_for_ai, raw_json, fhir_overrides = _normalize_content(req)
    patient = req.patient.model_dump()
    for k, v in fhir_overrides.items():
        if v and not patient.get(k):
            patient[k] = v
    if not patient.get("patientId"):
        raise ValueError("patientId required")

    encounter = req.encounter.model_dump()
    encounter["dateTime"] = iso(encounter["dateTime"])
    source = req.source.model_dump()

    with db_session() as s:
        _upsert_patient(s, patient)
        encounter_id = _upsert_encounter(s, encounter, patient["patientId"])
        encounter["encounterId"] = encounter_id
        document_id = _upsert_document(
            s,
            patient_id=patient["patientId"], encounter_id=encounter_id, source=source, fmt=req.format,
            raw_content=text_for_ai if not raw_json else json.dumps(raw_json, default=str),
            raw_json=raw_json,
        )
        audit(s, action="DOCUMENT_INGESTED", target_type="document", target_id=document_id,
              payload={"patientId": patient["patientId"], "encounterId": encounter_id})

    document = {
        "documentId": document_id,
        "sourceSystem": source.get("system"),
        "version": source.get("version") or "1",
        "format": req.format,
    }
    return patient, encounter, document, text_for_ai, raw_json


def _persist_post_extraction(*, patient: dict[str, Any], encounter: dict[str, Any], document: dict[str, Any],
                             extraction: ClinicalExtractionResult, raw_output: dict[str, Any],
                             valid: bool, errors: list[Any], model: str) -> None:
    """Stage 3: persist AI output + facts in one transaction."""
    with db_session() as s:
        _store_ai_output(
            s, document_id=document["documentId"], patient_id=patient["patientId"],
            model=model, raw=raw_output, valid=valid, errors=errors,
        )
        if not valid:
            audit(s, action="EXTRACTION_INVALID", target_type="document", target_id=document["documentId"],
                  payload={"errorCount": len(errors)})
            return
        rows = _facts_rows(
            patient_id=patient["patientId"], encounter_id=encounter["encounterId"],
            document_id=document["documentId"], ex=extraction,
        )
        if rows:
            s.execute(text(_FACT_INSERT), rows)
        audit(s, action="FACTS_PERSISTED", target_type="document", target_id=document["documentId"],
              payload={"count": len(rows)})


async def run_ingest(req: EMRIngestRequest, *, job_id: str | None = None) -> dict[str, Any]:
    settings = effective_settings()
    patient, encounter, document, text_for_ai, _raw_json = await asyncio.to_thread(_persist_pre_extraction, req)

    provider = get_ai_provider(settings)
    raw_output = await provider.extract(
        patient_id=patient["patientId"],
        encounter_type=encounter["type"],
        encounter_dt=str(encounter["dateTime"]),
        document_id=document["documentId"],
        content=text_for_ai,
    )
    raw_output.setdefault("patientId", patient["patientId"])
    raw_output["documentId"] = document["documentId"]
    raw_output["encounterId"] = encounter["encounterId"]

    valid = True
    errors: list[Any] = []
    try:
        extraction = ClinicalExtractionResult.model_validate(raw_output)
    except ValidationError as e:
        valid = False
        errors = e.errors()
        extraction = ClinicalExtractionResult(
            patientId=patient["patientId"], encounterId=encounter["encounterId"], documentId=document["documentId"],
            warnings=["AI output failed schema validation; downstream writes skipped."],
        )

    await asyncio.to_thread(
        _persist_post_extraction,
        patient=patient, encounter=encounter, document=document,
        extraction=extraction, raw_output=raw_output, valid=valid, errors=errors,
        model=settings.AI_MODEL,
    )

    graph_counts: dict[str, Any] = {}
    md_written: dict[str, str] = {}
    if valid:
        graph_task = asyncio.to_thread(update_graph_for_document, patient, encounter, document, extraction)
        md_task = asyncio.to_thread(generate_markdown,
                                    patient=patient, encounter=encounter, document=document,
                                    raw_content=text_for_ai, extraction=extraction)
        try:
            graph_counts, md_written = await asyncio.gather(graph_task, md_task)
        except Exception as exc:
            logger.exception("Post-extraction side-effect failed: %s", exc)
            graph_counts = {"error": str(exc)}

        # Embeddings — best-effort, bounded concurrency, batched insert
        try:
            await embed_and_store_many(
                patient_id=patient["patientId"],
                items=[
                    {"ref_type": "fact", "ref_id": f"{document['documentId']}-cond-{f.value}",
                     "content": f"{f.value}\n{f.evidenceText or ''}", "metadata": {"type": "condition"}}
                    for f in extraction.problems[:50]
                ] + [
                    {"ref_type": "note", "ref_id": path, "content": content[:4000], "metadata": {}}
                    for path, content in md_written.items()
                ],
            )
        except Exception as exc:
            logger.warning("Embedding step failed: %s", exc)

    return {
        "patientId": patient["patientId"],
        "encounterId": encounter["encounterId"],
        "documentId": document["documentId"],
        "valid": valid,
        "summary": {
            "headline": extraction.summary,
            "counts": {
                "problems": len(extraction.problems),
                "medications": len(extraction.medications),
                "observations": len(extraction.observations),
                "plans": len(extraction.plans),
                "warnings": len(extraction.warnings),
            },
            "graph": graph_counts,
            "markdownFiles": sorted(md_written.keys()),
        },
    }
