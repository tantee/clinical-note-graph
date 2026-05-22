from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy import text

from app.config import get_settings
from app.db.helpers import j
from app.db.postgres import db_session

logger = logging.getLogger(__name__)

ProgressCallback = Callable[..., None]
Handler = Callable[[dict, ProgressCallback], Awaitable[Any]]

JOB_HANDLERS: dict[str, Handler] = {}


def register_handler(job_type: str, handler: Handler) -> None:
    JOB_HANDLERS[job_type] = handler


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _isoformat(dt: datetime) -> str:
    return dt.isoformat()


class QueueWorker:
    def __init__(self, worker_id: str | None = None):
        self.worker_id = worker_id or f"w-{uuid.uuid4().hex[:8]}"
        self.settings = get_settings()
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def run_forever(self) -> None:
        backoff = 0.2
        while not self._stop.is_set():
            try:
                job = await self._claim_one()
            except Exception as exc:  # noqa: BLE001
                logger.exception("claim failed: %s", exc)
                job = None
            if not job:
                stop_task = asyncio.create_task(self._stop.wait())
                done, pending = await asyncio.wait({stop_task}, timeout=backoff)
                for p in pending:
                    p.cancel()
                    with suppress(asyncio.CancelledError):
                        await p
                backoff = min(backoff * 1.5, 2.0)
                continue
            backoff = 0.2
            try:
                await self._run(job)
            except Exception as exc:  # noqa: BLE001
                logger.exception("worker %s run loop error: %s", self.worker_id, exc)

    def start(self) -> None:
        self._task = asyncio.create_task(
            self.run_forever(), name=f"queue-{self.worker_id}"
        )

    async def stop(self, grace_seconds: int | None = None) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(
                    self._task,
                    timeout=grace_seconds or self.settings.JOB_GRACE_SECONDS,
                )
            except asyncio.TimeoutError:
                self._task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._task

    async def _claim_one(self) -> dict | None:
        lock_until = _now() + timedelta(seconds=self.settings.JOB_LOCK_SECONDS)
        return await asyncio.to_thread(self._claim_one_sync, lock_until)

    def _claim_one_sync(self, lock_until: datetime) -> dict | None:
        with db_session() as s:
            row = (
                s.execute(
                    text(
                        """
                        WITH claimed AS (
                            SELECT job_id FROM jobs
                            WHERE (status = 'pending' AND next_run_at <= now())
                               OR (status = 'running' AND locked_until < now())
                            ORDER BY priority DESC, next_run_at ASC
                            FOR UPDATE SKIP LOCKED
                            LIMIT 1
                        )
                        UPDATE jobs
                           SET status = 'running',
                               locked_by = :wid,
                               locked_until = :lock,
                               started_at = COALESCE(started_at, now()),
                               attempts = attempts + 1
                         WHERE job_id IN (SELECT job_id FROM claimed)
                         RETURNING job_id::text, type, status, patient_id, document_id, payload,
                                   attempts, max_attempts, progress
                        """
                    ),
                    {"wid": self.worker_id, "lock": _isoformat(lock_until)},
                )
                .mappings()
                .first()
            )
        return dict(row) if row else None

    async def _run(self, job: dict) -> None:
        handler = JOB_HANDLERS.get(job["type"])
        if handler is None:
            await self._finalize_failure(job, f"no handler for job type {job['type']!r}")
            return

        progress: dict[str, Any] = dict(job.get("progress") or {})

        def on_progress(stage: str, **payload: Any) -> None:
            progress[stage] = {"at": _isoformat(_now()), **payload}
            self._write_progress(job["job_id"], progress)

        try:
            result = await handler(job, on_progress=on_progress)
        except Exception as exc:  # noqa: BLE001
            # Log the full traceback to the backend log AND persist a
            # one-line "ClassName: message" summary to jobs.error so the
            # UI can render something useful without us shipping multi-
            # line tracebacks across the wire. The full traceback stays
            # in the container logs for ops to grep.
            logger.exception(
                "job %s (type=%s) failed: %s", job["job_id"], job["type"], exc,
            )
            await self._finalize_failure(job, f"{type(exc).__name__}: {exc}")
            return

        await self._finalize_success(job, result, progress)

    def _write_progress(self, job_id: str, progress: dict) -> None:
        with db_session() as s:
            s.execute(
                text(
                    "UPDATE jobs SET progress = CAST(:p AS jsonb), "
                    "locked_until = now() + interval '120 seconds' "
                    "WHERE job_id = CAST(:j AS uuid)"
                ),
                {"p": j(progress), "j": job_id},
            )

    async def _finalize_success(self, job: dict, result: Any, progress: dict) -> None:
        with db_session() as s:
            s.execute(
                text(
                    "UPDATE jobs SET status='completed', result=CAST(:r AS jsonb), "
                    "progress=CAST(:p AS jsonb), finished_at=now(), "
                    "locked_by=NULL, locked_until=NULL "
                    "WHERE job_id=CAST(:j AS uuid)"
                ),
                {"r": j(result), "p": j(progress), "j": job["job_id"]},
            )

    async def _finalize_failure(self, job: dict, error: str) -> None:
        attempts = int(job.get("attempts") or 1)
        max_attempts = int(job.get("max_attempts") or 3)
        if attempts >= max_attempts:
            new_status = "failed"
            next_run_at: datetime | None = None
        else:
            new_status = "pending"
            backoff_seconds = min(5 * (2 ** (attempts - 1)), 300)
            next_run_at = _now() + timedelta(seconds=backoff_seconds)
        params = {
            "st": new_status,
            "err": error,
            "j": job["job_id"],
            "nxt": _isoformat(next_run_at) if next_run_at else None,
        }
        sql = (
            "UPDATE jobs SET status=:st, error=:err, finished_at=now(), "
            "locked_by=NULL, locked_until=NULL"
        )
        if next_run_at:
            sql += ", next_run_at=:nxt"
        sql += " WHERE job_id=CAST(:j AS uuid)"
        with db_session() as s:
            s.execute(text(sql), params)


def start_workers(n: int | None = None) -> list[QueueWorker]:
    settings = get_settings()
    workers = [QueueWorker() for _ in range(n or settings.QUEUE_WORKERS)]
    for w in workers:
        w.start()
    return workers


async def stop_workers(workers: list[QueueWorker]) -> None:
    await asyncio.gather(*[w.stop() for w in workers], return_exceptions=True)
