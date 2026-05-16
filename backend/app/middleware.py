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


_PROTECTED_PREFIXES = ("/api/emr", "/api/config", "/api/export", "/api/facts")


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Optional API key gate. If API_KEY env is set, protected paths require X-API-Key."""

    async def dispatch(self, request: Request, call_next):
        api_key = get_settings().API_KEY
        if api_key and any(request.url.path.startswith(p) for p in _PROTECTED_PREFIXES):
            supplied = request.headers.get("X-API-Key")
            if supplied != api_key:
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={"detail": "Invalid or missing X-API-Key"},
                )
        return await call_next(request)
