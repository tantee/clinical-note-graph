from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from app.db.helpers import audit
from app.db.postgres import db_session
from app.schemas.coding import CodingSuggestRequest, CodingSuggestResponse, SummaryRequest, SummaryResponse
from app.services.coding import suggest_coding
from app.services.embeddings import vector_search
from app.services.graph_updater import fetch_patient_graph  # noqa: F401 (kept for any legacy callers)
from app.services.markdown_generator import collect_backlinks, list_patient_files, read_note
from app.services.patient_facts import gather_patient_facts
from app.services.summary import make_summary
from app.services.summary_store import latest_coding, latest_summary

router = APIRouter(prefix="/api", tags=["patient"])


@router.get("/patients")
def list_patients(q: str | None = None, limit: int = 50):
    with db_session() as s:
        sql = "SELECT patient_id, name, gender, birth_date, metadata, updated_at FROM patients"
        params: dict[str, Any] = {"lim": limit}
        if q:
            sql += " WHERE patient_id ILIKE :q OR name ILIKE :q"
            params["q"] = f"%{q}%"
        sql += " ORDER BY updated_at DESC LIMIT :lim"
        rows = s.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


@router.get("/patient/{patient_id}")
def get_patient(patient_id: str) -> dict[str, Any]:
    facts = gather_patient_facts(patient_id)
    if not facts["patient"]:
        raise HTTPException(status_code=404, detail="Patient not found")
    return facts


@router.get("/patient/{patient_id}/timeline")
def get_timeline(patient_id: str) -> dict[str, Any]:
    with db_session() as s:
        encounters = s.execute(
            text(
                """
                SELECT e.encounter_id, e.type, e.date_time, e.department, e.provider,
                       COUNT(DISTINCT d.document_id) AS document_count,
                       COUNT(DISTINCT f.id) AS fact_count
                FROM encounters e
                LEFT JOIN documents d ON d.encounter_id = e.encounter_id
                LEFT JOIN facts f ON f.encounter_id = e.encounter_id
                WHERE e.patient_id = :pid
                GROUP BY e.encounter_id
                ORDER BY e.date_time ASC
                """
            ),
            {"pid": patient_id},
        ).mappings().all()
    return {"patientId": patient_id, "encounters": [dict(r) for r in encounters]}


@router.get("/patient/{patient_id}/encounter/{encounter_id}/documents")
def get_encounter_documents(patient_id: str, encounter_id: str) -> dict[str, Any]:
    with db_session() as s:
        rows = s.execute(
            text(
                """
                SELECT document_id, source_system, source_document_id, version, format, received_at
                FROM documents
                WHERE patient_id = :pid AND encounter_id = :eid
                ORDER BY received_at ASC
                """
            ),
            {"pid": patient_id, "eid": encounter_id},
        ).mappings().all()
    return {"patientId": patient_id, "encounterId": encounter_id, "documents": [dict(r) for r in rows]}


@router.get("/patient/{patient_id}/encounters")
def list_patient_encounters(patient_id: str) -> list[dict[str, Any]]:
    """List encounters for a patient with doc count + AI-output flags.
    Drives the new Encounters tab and the Patients-list expand row."""
    with db_session() as s:
        rows = s.execute(
            text(
                """
                SELECT e.encounter_id, e.type, e.date_time, e.department, e.provider,
                       (SELECT COUNT(*) FROM documents d WHERE d.encounter_id = e.encounter_id) AS doc_count,
                       EXISTS(SELECT 1 FROM patient_summaries ps
                              WHERE ps.encounter_id = e.encounter_id AND ps.kind = 'summary') AS has_summary,
                       EXISTS(SELECT 1 FROM patient_summaries ps
                              WHERE ps.encounter_id = e.encounter_id AND ps.kind = 'coding') AS has_coding
                FROM encounters e
                WHERE e.patient_id = :pid
                ORDER BY e.date_time DESC
                """
            ),
            {"pid": patient_id},
        ).mappings().all()
    return [
        {
            "encounterId": r["encounter_id"],
            "type": r["type"],
            "dateTime": str(r["date_time"]) if r["date_time"] else None,
            "department": r["department"],
            "provider": r["provider"],
            "docCount": int(r["doc_count"] or 0),
            "hasSummary": bool(r["has_summary"]),
            "hasCoding": bool(r["has_coding"]),
        }
        for r in rows
    ]


_MAX_NODES_PRE_DEDUPE = 500


@router.get("/patient/{patient_id}/graph")
def get_graph(
    patient_id: str,
    scope: str = Query("patient", pattern="^(patient|encounter|encounters)$"),
    encounterId: list[str] = Query(default=[]),
    dedupe: bool | None = Query(None),
    includeEncounters: bool | None = Query(None, alias="includeEncounters"),
    includeDocuments: bool = Query(False, alias="includeDocuments"),
    reviewStatus: str = Query("hide_rejected", pattern="^(all|confirmed|hide_rejected)$"),
) -> dict[str, Any]:
    from app.services.graph_updater import fetch_graph
    try:
        graph = fetch_graph(
            patient_id,
            scope=scope,
            encounter_ids=encounterId,
            dedupe=dedupe,
            include_encounters=includeEncounters,
            include_documents=includeDocuments,
            review_status=reviewStatus,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if len(graph["nodes"]) > _MAX_NODES_PRE_DEDUPE:
        raise HTTPException(
            status_code=422,
            detail={
                "detail": "Graph too large; narrow the scope",
                "nodeCount": len(graph["nodes"]),
            },
        )
    return graph


@router.get("/patient/{patient_id}/notes")
def get_notes(patient_id: str) -> dict[str, Any]:
    return {"files": list_patient_files(patient_id)}


@router.get("/patient/{patient_id}/note")
def get_note(patient_id: str, path: str = Query(..., description="Vault-relative path")) -> dict[str, Any]:
    content = read_note(path)
    if content is None:
        raise HTTPException(status_code=404, detail="Note not found")
    backlinks = collect_backlinks(patient_id, path)
    return {"path": path, "content": content, "backlinks": backlinks}


@router.get("/patient/{patient_id}/document/{document_id}")
def get_document(patient_id: str, document_id: str, include_raw: bool = Query(True, alias="includeRaw")) -> dict[str, Any]:
    raw_select = ", raw_content, raw_json" if include_raw else ""
    with db_session() as s:
        doc = s.execute(
            text(
                f"SELECT document_id, patient_id, encounter_id, source_system, source_document_id, "
                f"version, format, received_at{raw_select} FROM documents "
                f"WHERE patient_id = :pid AND document_id = :did"
            ),
            {"pid": patient_id, "did": document_id},
        ).mappings().first()
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        facts = s.execute(
            text("SELECT * FROM facts WHERE patient_id = :pid AND document_id = :did ORDER BY type, created_at"),
            {"pid": patient_id, "did": document_id},
        ).mappings().all()
        ai = s.execute(
            text("SELECT * FROM ai_outputs WHERE document_id = :did ORDER BY created_at DESC LIMIT 1"),
            {"did": document_id},
        ).mappings().first()
    return {
        "document": dict(doc),
        "facts": [
            {**dict(f), "id": str(f["id"]),
             "confidence": float(f["confidence"]) if f["confidence"] is not None else None}
            for f in facts
        ],
        "aiOutput": dict(ai) if ai else None,
    }


@router.post("/patient/{patient_id}/summary", response_model=SummaryResponse)
async def patient_summary(patient_id: str, req: SummaryRequest):
    return await make_summary(patient_id, req)


@router.get("/patient/{patient_id}/summary/latest")
def patient_summary_latest(patient_id: str) -> dict[str, Any] | None:
    """Return the most recent persisted summary, or null if none has been generated."""
    return latest_summary(patient_id)


@router.post("/patient/{patient_id}/coding/suggest", response_model=CodingSuggestResponse)
async def patient_coding(patient_id: str, req: CodingSuggestRequest):
    return await suggest_coding(patient_id, req)


@router.get("/patient/{patient_id}/coding/latest")
def patient_coding_latest(patient_id: str) -> dict[str, Any] | None:
    return latest_coding(patient_id)


@router.patch("/facts/{fact_id}/review")
def review_fact(fact_id: str, status: str = Query(..., pattern="^(human_confirmed|rejected|ai_suggested)$")) -> dict[str, Any]:
    with db_session() as s:
        s.execute(
            text("UPDATE facts SET review_status = :st WHERE id = CAST(:fid AS uuid)"),
            {"st": status, "fid": fact_id},
        )
        audit(s, actor="human", action="REVIEW_FACT", target_type="fact", target_id=fact_id, payload={"status": status})
    return {"factId": fact_id, "reviewStatus": status}


@router.get("/search")
async def search(q: str, patientId: str | None = None, limit: int = 10) -> dict[str, Any]:
    return {"query": q, "results": await vector_search(q, patient_id=patientId, limit=limit)}
