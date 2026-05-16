from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from app.schemas.emr import EMRIngestRequest, EMRIngestResponse
from app.services.ingest import run_ingest
from app.services.jobs import schedule_ingest

router = APIRouter(prefix="/api/emr", tags=["emr"])


@router.post("/ingest", response_model=EMRIngestResponse)
async def ingest(
    req: EMRIngestRequest,
    async_processing: bool = Query(False, alias="async", description="If true, run in background and return jobId"),
):
    if async_processing:
        job_id = schedule_ingest(req)
        return EMRIngestResponse(
            jobId=job_id,
            status="queued",
            patientId=req.patient.patientId,
            encounterId=req.encounter.encounterId or "",
            documentId=req.source.documentId or "",
            summary=None,
        )

    try:
        result = await run_ingest(req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return EMRIngestResponse(
        jobId="sync",
        status="completed",
        patientId=result["patientId"],
        encounterId=result["encounterId"],
        documentId=result["documentId"],
        summary=result.get("summary"),
    )
