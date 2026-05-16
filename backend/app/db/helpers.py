from __future__ import annotations

import json
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.orm import Session


def j(value: Any) -> str:
    """JSON-serialize for `CAST(:x AS jsonb)` parameters."""
    return json.dumps(value, default=str)


def audit(s: Session, *, actor: str = "system", action: str, target_type: str, target_id: str, payload: dict[str, Any] | None = None) -> None:
    s.execute(
        text(
            "INSERT INTO audit_log (actor, action, target_type, target_id, payload) "
            "VALUES (:a, :ac, :tt, :ti, CAST(:p AS jsonb))"
        ),
        {"a": actor, "ac": action, "tt": target_type, "ti": target_id, "p": j(payload or {})},
    )


def executemany(s: Session, sql: str, rows: Iterable[dict[str, Any]]) -> None:
    rows = list(rows)
    if not rows:
        return
    s.execute(text(sql), rows)
