from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.db.neo4j_client import close_driver
from app.middleware import ApiKeyMiddleware, RequestContextMiddleware
from app.routers import config as config_router
from app.routers import emr, export, jobs, patient
from app.services.graph_updater import ensure_constraints

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=settings.LOG_LEVEL.upper())
    last_err: Exception | None = None
    for attempt in range(12):
        try:
            await asyncio.to_thread(ensure_constraints, True)
            last_err = None
            break
        except Exception as exc:
            last_err = exc
            await asyncio.sleep(2.5)
    if last_err is not None:
        logger.warning("Neo4j constraints not initialised at startup: %s", last_err)
    yield
    await asyncio.to_thread(close_driver)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Clinical Graph Notes",
        version="0.1.0",
        description=(
            "Receives EMR documents, extracts structured clinical facts via AI, "
            "maintains a longitudinal patient knowledge graph, and generates Obsidian-style notes.\n\n"
            "**All AI-assisted output requires clinical review.**"
        ),
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=settings.cors_origin_list != ["*"],
    )
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(ApiKeyMiddleware)

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "clinical-graph-notes", "aiProvider": get_settings().AI_PROVIDER}

    @app.get("/ready")
    async def ready():
        from app.db.postgres import get_engine
        try:
            engine = get_engine()
            with engine.connect() as conn:
                conn.exec_driver_sql("SELECT 1")
            return {"status": "ready"}
        except Exception as exc:
            return JSONResponse(status_code=503, content={"status": "degraded", "detail": str(exc)})

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={"detail": exc.errors(), "requestId": getattr(request.state, "request_id", None)},
        )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        logger.exception("Unhandled exception (rid=%s)", getattr(request.state, "request_id", None))
        return JSONResponse(
            status_code=500,
            content={"detail": str(exc), "requestId": getattr(request.state, "request_id", None)},
        )

    app.include_router(emr.router)
    app.include_router(patient.router)
    app.include_router(config_router.router)
    app.include_router(export.router)
    app.include_router(jobs.router)
    return app


app = create_app()
