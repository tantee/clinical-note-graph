import asyncio
import pytest


@pytest.fixture
def queue(fake_store, monkeypatch):
    from app.services import queue as q_mod
    # Reset module state
    q_mod.JOB_HANDLERS.clear()
    return q_mod


def _enqueue(fake_store, jid="j1", status="pending", attempts=0, lock_until=None, run_at="now"):
    fake_store.jobs[jid] = {
        "job_id": jid, "type": "test", "status": status, "patient_id": None, "document_id": None,
        "payload": {}, "attempts": attempts, "max_attempts": 3,
        "locked_by": None, "locked_until": lock_until,
        "priority": 0, "next_run_at": run_at, "progress": {},
    }


def test_claim_returns_pending_row(queue, fake_store):
    _enqueue(fake_store)
    w = queue.QueueWorker(worker_id="w1")
    job = asyncio.run(w._claim_one())
    assert job is not None
    assert job["job_id"] == "j1"
    assert fake_store.jobs["j1"]["status"] == "running"
    assert fake_store.jobs["j1"]["locked_by"] == "w1"


def test_claim_skips_locked_row(queue, fake_store):
    from datetime import datetime, timezone, timedelta
    future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    _enqueue(fake_store, jid="j1", status="running", lock_until=future)
    w = queue.QueueWorker(worker_id="w2")
    job = asyncio.run(w._claim_one())
    assert job is None


def test_stale_running_lock_reclaimed(queue, fake_store):
    from datetime import datetime, timezone, timedelta
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    _enqueue(fake_store, jid="j1", status="running", lock_until=past)
    w = queue.QueueWorker(worker_id="w3")
    job = asyncio.run(w._claim_one())
    assert job is not None
    assert fake_store.jobs["j1"]["locked_by"] == "w3"


@pytest.mark.asyncio
async def test_run_invokes_handler_and_finalizes(queue, fake_store):
    handled = []

    async def my_handler(job, *, on_progress):
        handled.append(job["job_id"])
        on_progress("only", count=1)
        return {"ok": True}

    queue.register_handler("test", my_handler)
    _enqueue(fake_store, jid="j1")

    w = queue.QueueWorker(worker_id="w1")
    job = await w._claim_one()
    await w._run(job)

    assert handled == ["j1"]
    row = fake_store.jobs["j1"]
    assert row["status"] == "completed"
    assert row["result"] == {"ok": True}
    assert "only" in row["progress"]


@pytest.mark.asyncio
async def test_handler_failure_reschedules_with_backoff(queue, fake_store):
    async def boom(job, *, on_progress):
        raise RuntimeError("nope")

    queue.register_handler("test", boom)
    _enqueue(fake_store, jid="j1")

    w = queue.QueueWorker(worker_id="w1")
    job = await w._claim_one()
    await w._run(job)
    row = fake_store.jobs["j1"]
    assert row["status"] == "pending"            # rescheduled (still has attempts left)
    assert row["attempts"] == 1
    assert row["error"] and "nope" in row["error"]


@pytest.mark.asyncio
async def test_handler_max_attempts_marks_failed(queue, fake_store):
    async def boom(job, *, on_progress):
        raise RuntimeError("dead")

    queue.register_handler("test", boom)
    _enqueue(fake_store, jid="j1", attempts=2)  # one attempt remaining
    fake_store.jobs["j1"]["max_attempts"] = 3

    w = queue.QueueWorker(worker_id="w1")
    job = await w._claim_one()
    await w._run(job)
    assert fake_store.jobs["j1"]["status"] == "failed"
    assert fake_store.jobs["j1"]["attempts"] == 3
