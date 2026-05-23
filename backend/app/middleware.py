from __future__ import annotations

import logging
import time
import uuid

from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings

logger = logging.getLogger("cng.request")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        request.state.request_id = rid
        start = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            dur_ms = (time.perf_counter() - start) * 1000
            logger.info(
                "request",
                extra={
                    "rid": rid,
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": round(dur_ms, 1),
                },
            )
        response.headers["X-Request-ID"] = rid
        return response


# Gate the entire /api/* surface. The previous allow-list was a leaky
# subset that missed /api/patients, /api/patient/{id}, /api/jobs,
# /api/rag/ask, /api/search/* etc. — i.e. patient PHI and LLM-cost
# endpoints were reachable with no key in prod when API_KEY was set.
# Anonymous-safe routes (/health, /ready, /docs, /openapi.json) live
# outside /api/ and are unaffected.
_PROTECTED_PREFIX = "/api/"


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Optional API key gate. If API_KEY env is set, /api/* requires X-API-Key."""

    async def dispatch(self, request: Request, call_next):
        api_key = get_settings().API_KEY
        if api_key and request.url.path.startswith(_PROTECTED_PREFIX):
            supplied = request.headers.get("X-API-Key")
            if supplied != api_key:
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={"detail": "Invalid or missing X-API-Key"},
                )
        return await call_next(request)
