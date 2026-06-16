"""Idempotent startup SQL migration runner.

The Postgres image only runs `/docker-entrypoint-initdb.d` scripts the first
time a data volume is initialised. Any numbered migration added after a database
was created (e.g. 006_curated_facts, 007_curated_aliases) therefore never lands
on an existing volume — surfacing as `relation "..." does not exist` at runtime.

This runner replays every `backend/db/init/*.sql` file on application startup, in
numbered order. The scripts are written to be idempotent (CREATE ... IF NOT
EXISTS, ADD COLUMN IF NOT EXISTS, INSERT ... ON CONFLICT DO NOTHING), so a
fresh DB (already initialised by initdb) re-runs them as cheap no-ops while a
stale DB catches up. Best-effort and isolated per file: one failure is logged
and never aborts the others or app startup.
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.db.postgres import get_engine

logger = logging.getLogger(__name__)

# backend/app/db/migrations.py -> parents[2] == backend/
_INIT_DIR = Path(__file__).resolve().parents[2] / "db" / "init"


def _migration_files() -> list[Path]:
    """Every *.sql migration, in lexical (numbered) order."""
    if not _INIT_DIR.is_dir():
        return []
    return sorted(_INIT_DIR.glob("*.sql"))


def run_migrations() -> list[str]:
    """Apply each migration file idempotently. Returns the names applied OK.

    psycopg3 runs multi-statement scripts (no bound parameters) in a single
    exec_driver_sql call; each file gets its own transaction so a failure rolls
    back only that file."""
    eng = get_engine()
    applied: list[str] = []
    for f in _migration_files():
        sql = f.read_text()
        if not sql.strip():
            continue
        try:
            with eng.begin() as conn:
                conn.exec_driver_sql(sql)
            applied.append(f.name)
            logger.info("migration applied: %s", f.name)
        except Exception:  # noqa: BLE001 — resilient startup; log and continue
            logger.exception("migration failed: %s", f.name)
    return applied
