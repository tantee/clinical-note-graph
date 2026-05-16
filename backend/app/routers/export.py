from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.schemas.coding import ExportRequest
from app.services.export import run_export

router = APIRouter(prefix="/api", tags=["export"])


@router.post("/export")
async def post_export(req: ExportRequest) -> dict[str, Any]:
    return await run_export(req)
