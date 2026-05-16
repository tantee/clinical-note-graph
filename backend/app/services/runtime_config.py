"""Read-time merge of persisted overrides into the runtime Settings.

The `Settings` object is the immutable baseline from env vars. Overrides live
in the `app_config` table and are merged in on every read — never written back
into the cached Settings object.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from sqlalchemy import text

from app.config import Settings, get_settings
from app.db.postgres import db_session

_TTL_SECONDS = 5.0
_OVERRIDES: dict[str, Any] = {}
_LOADED_AT: float = 0.0
_LOCK = threading.Lock()


def _load_overrides_from_db() -> dict[str, Any]:
    try:
        with db_session() as s:
            rows = s.execute(text("SELECT key, value FROM app_config")).mappings().all()
        return {r["key"]: r["value"] for r in rows}
    except Exception:
        return {}


def _maybe_refresh() -> None:
    global _LOADED_AT, _OVERRIDES
    if time.monotonic() - _LOADED_AT < _TTL_SECONDS:
        return
    with _LOCK:
        if time.monotonic() - _LOADED_AT < _TTL_SECONDS:
            return
        _OVERRIDES = _load_overrides_from_db()
        _LOADED_AT = time.monotonic()


def invalidate() -> None:
    global _LOADED_AT
    with _LOCK:
        _LOADED_AT = 0.0


def effective() -> Settings:
    """Return a shallow copy of Settings with DB overrides applied (never mutates the cache)."""
    _maybe_refresh()
    base = get_settings()
    if not _OVERRIDES:
        return base
    data = base.model_dump()
    for k, v in _OVERRIDES.items():
        if k in data:
            data[k] = v
    return Settings(**data)


def overrides_snapshot() -> dict[str, Any]:
    _maybe_refresh()
    return dict(_OVERRIDES)
