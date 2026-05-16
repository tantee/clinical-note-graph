from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import text

from app.db.postgres import db_session


def compute_cost(
    rates: dict[str, Decimal | None] | None,
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    embedding_tokens: int = 0,
) -> Decimal | None:
    """Return USD cost (Decimal) or None when rates are unknown.

    None is returned if `rates` itself is None, OR if every rate component
    (prompt/completion/embedding) is None. Otherwise each component with a
    rate contributes (tokens / 1e6) * rate; components with no rate or no
    tokens contribute zero. The result is quantized to 6 decimal places
    (matches NUMERIC(10,6) storage) using banker's rounding.
    """
    if rates is None:
        return None
    p = rates.get("prompt_per_1m")
    c = rates.get("completion_per_1m")
    e = rates.get("embedding_per_1m")
    if p is None and c is None and e is None:
        return None
    total = Decimal("0")
    if p is not None and prompt_tokens:
        total += (Decimal(prompt_tokens) / Decimal(1_000_000)) * p
    if c is not None and completion_tokens:
        total += (Decimal(completion_tokens) / Decimal(1_000_000)) * c
    if e is not None and embedding_tokens:
        total += (Decimal(embedding_tokens) / Decimal(1_000_000)) * e
    return total.quantize(Decimal("0.000001"))


def load_rates(model: str) -> dict[str, Decimal | None] | None:
    """Return rates dict for a model, or None when not priced."""
    with db_session() as s:
        row = s.execute(
            text(
                "SELECT prompt_per_1m, completion_per_1m, embedding_per_1m "
                "FROM model_pricing WHERE model = :m"
            ),
            {"m": model},
        ).mappings().first()
    if not row:
        return None
    return dict(row)


def list_rates() -> list[dict[str, Any]]:
    """All priced models, ordered alphabetically by model name."""
    with db_session() as s:
        rows = s.execute(
            text(
                "SELECT model, prompt_per_1m, completion_per_1m, embedding_per_1m, "
                "source, updated_at FROM model_pricing ORDER BY model"
            )
        ).mappings().all()
    return [dict(r) for r in rows]


def upsert_rate(
    *,
    model: str,
    prompt_per_1m: Decimal | float | None = None,
    completion_per_1m: Decimal | float | None = None,
    embedding_per_1m: Decimal | float | None = None,
    source: str = "manual",
) -> None:
    """Insert or update a single pricing row.

    NULL rate components on the input are preserved (do not overwrite an
    existing value with NULL) via COALESCE on the SET clause.
    """
    with db_session() as s:
        s.execute(
            text(
                """
                INSERT INTO model_pricing
                    (model, prompt_per_1m, completion_per_1m, embedding_per_1m, source, updated_at)
                VALUES (:m, :p, :c, :e, :src, now())
                ON CONFLICT (model) DO UPDATE SET
                    prompt_per_1m     = COALESCE(EXCLUDED.prompt_per_1m,     model_pricing.prompt_per_1m),
                    completion_per_1m = COALESCE(EXCLUDED.completion_per_1m, model_pricing.completion_per_1m),
                    embedding_per_1m  = COALESCE(EXCLUDED.embedding_per_1m,  model_pricing.embedding_per_1m),
                    source = EXCLUDED.source,
                    updated_at = now()
                """
            ),
            {"m": model, "p": prompt_per_1m, "c": completion_per_1m, "e": embedding_per_1m, "src": source},
        )


def delete_rate(model: str) -> None:
    with db_session() as s:
        s.execute(text("DELETE FROM model_pricing WHERE model = :m"), {"m": model})
