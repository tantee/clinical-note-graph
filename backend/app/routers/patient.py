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


@router.post("/patient/{patient_id}/graph/rebuild")
def rebuild_graph(patient_id: str) -> dict[str, Any]:
    """Reconstruct this patient's Neo4j subgraph from Postgres facts.

    Recovery for the silent-graph-upsert-failure mode: when Neo4j was
    unhealthy or transiently unreachable during ingest, the facts still
    landed in Postgres but the graph stayed empty. This endpoint replays
    `graph_updater` against the rows that are already there, with no
    additional AI calls.

    The graph is wiped first (Patient node retained, everything else
    cleared) so duplicate nodes accumulated from prior incremental writes
    are removed. Observation MERGEs key on `dateTime`, which falls back
    to `datetime()` (NOW) for un-timestamped readings — every rebuild
    would otherwise add new copies. The facts pushed back in are run
    through the same dedup helpers the Overview tab uses, so identical
    EMR mentions don't produce parallel Neo4j nodes.

    Returns wipe counts + per-document write counts so the caller can
    confirm something was actually replaced.
    """
    from app.services.graph_updater import (
        backfill_graph_for_document, wipe_patient_subgraph,
    )
    from app.services.patient_facts import (
        _PATIENT_DEDUPE_TYPES, _dedupe_patient_facts, _dedupe_observations,
    )

    with db_session() as s:
        patient_row = s.execute(
            text("SELECT * FROM patients WHERE patient_id = :pid"),
            {"pid": patient_id},
        ).mappings().first()
        if not patient_row:
            raise HTTPException(status_code=404, detail=f"Patient {patient_id} not found")
        encounters = s.execute(
            text("SELECT * FROM encounters WHERE patient_id = :pid"),
            {"pid": patient_id},
        ).mappings().all()
        documents = s.execute(
            text("SELECT * FROM documents WHERE patient_id = :pid"),
            {"pid": patient_id},
        ).mappings().all()
        all_facts = s.execute(
            text(
                "SELECT * FROM facts WHERE patient_id = :pid "
                "AND review_status <> 'rejected' "
                "ORDER BY created_at ASC"
            ),
            {"pid": patient_id},
        ).mappings().all()

    if not documents:
        # No documents = nothing the AI ever extracted. Encounters alone
        # don't produce a useful graph; tell the caller to ingest first.
        return {"documents": 0, "perDocument": [], "wiped": {"labels": []}}

    # Wipe first so the next write doesn't pile on top of whatever was
    # there (duplicate Observation nodes are the common case).
    wiped = wipe_patient_subgraph(patient_id)

    encounters_by_id = {e["encounter_id"]: dict(e) for e in encounters}
    facts_by_doc: dict[str, list[dict[str, Any]]] = {}
    for f in all_facts:
        d = dict(f)
        d["id"] = str(d["id"])
        if d.get("confidence") is not None:
            d["confidence"] = float(d["confidence"])
        facts_by_doc.setdefault(d["document_id"] or "", []).append(d)

    # Dedup per document before pushing into Neo4j so the same finding
    # mentioned in IMP / Intraop / Discharge sections doesn't become three
    # graph nodes. The helpers preserve evidence text via `·` join.
    def _dedup_doc_facts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_type: dict[str, list[dict[str, Any]]] = {}
        for f in rows:
            by_type.setdefault(f["type"], []).append(f)
        for t in list(by_type):
            if t in _PATIENT_DEDUPE_TYPES:
                by_type[t] = _dedupe_patient_facts(by_type[t])
            elif t == "observation":
                by_type[t] = _dedupe_observations(by_type[t])
        return [f for rows in by_type.values() for f in rows]

    facts_by_doc = {k: _dedup_doc_facts(v) for k, v in facts_by_doc.items()}

    p_dict = {
        "patientId": patient_id,
        "name": patient_row.get("name"),
        "gender": patient_row.get("gender"),
        "birthDate": patient_row.get("birth_date"),
    }
    per_doc = []
    for d in documents:
        enc = encounters_by_id.get(d.get("encounter_id"))
        if not enc:
            continue
        enc_dict = {
            "encounterId": enc["encounter_id"],
            "type": enc.get("type"),
            "dateTime": enc.get("date_time"),
            "department": enc.get("department"),
            "provider": enc.get("provider"),
        }
        doc_dict = {
            "documentId": d["document_id"],
            "sourceSystem": d.get("source_system"),
            "version": d.get("version") or "1",
            "format": d.get("format"),
        }
        rows = facts_by_doc.get(d["document_id"], [])
        try:
            counts = backfill_graph_for_document(p_dict, enc_dict, doc_dict, rows)
        except Exception as exc:
            per_doc.append({"documentId": d["document_id"], "error": str(exc)})
            continue
        per_doc.append({"documentId": d["document_id"], "counts": counts})

    return {"documents": len(documents), "perDocument": per_doc, "wiped": wiped}


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
    # Normalise the rows the way gather_patient_facts does, then collapse same-
    # condition / same-med mentions inside this document so the EMR-vs-facts
    # tab matches the Overview tab's deduped lists. The doc may mention the
    # same finding in IMP / Intraop / Discharge sections; the LLM faithfully
    # produces one fact per mention. Reviewers want one row with evidence
    # accumulated, not three separate rows.
    from app.services.patient_facts import (
        _dedupe_patient_facts, _dedupe_observations, _PATIENT_DEDUPE_TYPES,
    )

    normalised = [
        {**dict(f), "id": str(f["id"]),
         "confidence": float(f["confidence"]) if f["confidence"] is not None else None}
        for f in facts
    ]
    by_type: dict[str, list[dict[str, Any]]] = {}
    for f in normalised:
        by_type.setdefault(f["type"], []).append(f)
    for t in list(by_type):
        if t in _PATIENT_DEDUPE_TYPES:
            by_type[t] = _dedupe_patient_facts(by_type[t])
        elif t == "observation":
            by_type[t] = _dedupe_observations(by_type[t])
    deduped = [f for rows in by_type.values() for f in rows]
    # Preserve the (type, created_at) ordering the SQL produced.
    deduped.sort(key=lambda f: (f.get("type") or "", str(f.get("created_at") or "")))

    return {
        "document": dict(doc),
        "facts": deduped,
        "aiOutput": dict(ai) if ai else None,
    }


@router.post("/patient/{patient_id}/summary")
async def patient_summary(
    patient_id: str,
    req: SummaryRequest,
    async_processing: bool = Query(
        False, alias="async",
        description="If true, enqueue the call and return a jobId immediately. "
                    "Default false so existing callers keep getting the sync "
                    "SummaryResponse shape; the UI sets ?async=true so long "
                    "reasoning-model calls don't block the page.",
    ),
):
    if async_processing:
        from app.services.jobs import schedule_patient_summary
        job_id = schedule_patient_summary(patient_id, req)
        return {"jobId": job_id, "status": "queued", "type": "patient_summary",
                "patientId": patient_id}
    return await make_summary(patient_id, req)


@router.get("/patient/{patient_id}/summary/latest")
def patient_summary_latest(patient_id: str) -> dict[str, Any] | None:
    """Return the most recent persisted summary, or null if none has been generated."""
    return latest_summary(patient_id)


@router.post("/patient/{patient_id}/coding/suggest")
async def patient_coding(
    patient_id: str,
    req: CodingSuggestRequest,
    async_processing: bool = Query(
        False, alias="async",
        description="If true, enqueue the call and return a jobId immediately. "
                    "Default false so existing callers keep getting the sync "
                    "CodingSuggestResponse shape.",
    ),
):
    if async_processing:
        from app.services.jobs import schedule_patient_coding
        job_id = schedule_patient_coding(patient_id, req)
        return {"jobId": job_id, "status": "queued", "type": "patient_coding",
                "patientId": patient_id}
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
