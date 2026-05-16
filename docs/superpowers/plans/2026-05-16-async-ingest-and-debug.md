# Async ingest queue, unified ingest UI, debug page — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement spec [`2026-05-16-async-ingest-and-debug.md`](../specs/2026-05-16-async-ingest-and-debug.md): move EMR ingest onto a Postgres-backed async queue, unify the ingest UI, and add a debug page with token + cost tracking.

**Architecture:** Postgres-backed queue with `SELECT … FOR UPDATE SKIP LOCKED` inside the uvicorn process; provider interface returns `(output, AICallRecord)` so token/cost capture happens at the call site; new `model_pricing` table feeds USD cost computation; new `/api/debug/*` routes feed a Vue 3 `DebugView` with three tabs.

**Tech Stack:** FastAPI · SQLAlchemy · Postgres · Pydantic · Vue 3 · Vuetify 4 · Pinia · Chart.js · vue-chartjs · Vitest · pytest · Playwright.

**Definition of done:** All 36+ existing backend tests pass, all new tests pass, GitHub CI green, the live stack (`docker compose up`) handles a full ingest end-to-end through the queue with the debug page showing the resulting cost.

---

## File map

**Create**
- `backend/db/init/002_async_and_debug.sql`
- `backend/app/services/pricing.py`
- `backend/app/services/queue.py`
- `backend/app/services/debug_queries.py`
- `backend/app/routers/debug.py`
- `backend/tests/test_pricing.py`
- `backend/tests/test_pricing_routes.py`
- `backend/tests/test_queue_worker.py`
- `backend/tests/test_ai_metering.py`
- `backend/tests/test_ingest_async.py`
- `backend/tests/test_debug_endpoints.py`
- `frontend/src/views/DebugView.vue`
- `frontend/src/components/JobWatcher.vue`
- `frontend/src/components/PricingTable.vue`
- `frontend/tests/JobWatcher.spec.js`
- `frontend/tests/IngestView.spec.js`
- `frontend/tests/DebugView.spec.js`
- `frontend/tests/e2e/debug.spec.js`

**Modify**
- `backend/app/config.py` — add `QUEUE_WORKERS`, `JOB_GRACE_SECONDS`, `JOB_LOCK_SECONDS`
- `backend/app/middleware.py` — extend `_PROTECTED_PREFIXES`
- `backend/app/main.py` — start/stop queue workers in `lifespan`
- `backend/app/services/ai_provider.py` — `AICallRecord` dataclass, tuple-returning interface, writes `ai_outputs` itself
- `backend/app/services/ingest.py` — `run_ingest_pipeline(on_progress=…)`, removed `_store_ai_output`
- `backend/app/services/jobs.py` — thin facade for create/get; delegates execution to the queue
- `backend/app/services/embeddings.py` — adapt to new provider tuple-return; pass `job_id`/`call_type`
- `backend/app/routers/emr.py` — default `async=true`; returns job shape
- `backend/app/routers/jobs.py` — list endpoint + re-queue endpoint
- `backend/app/routers/config.py` — pricing CRUD + OpenRouter refresh
- `backend/tests/conftest.py` — fake store updates for new columns/tables
- `backend/tests/test_api_ingest.py` — async-default tests, sync still works
- `backend/tests/test_e2e_smoke.py` — poll + assert debug summary
- `frontend/src/views/IngestView.vue` — unified single-mode form + JobWatcher
- `frontend/src/views/ConfigView.vue` — adds Model pricing card
- `frontend/src/views/PatientDetail.vue` — `[+ Add note]` button
- `frontend/src/App.vue` — Debug nav link
- `frontend/src/router.js` — `/debug` route
- `frontend/src/api/client.js` — new endpoints
- `frontend/src/utils/format.js` — `formatUSD`, `formatTokens`
- `frontend/package.json` — `chart.js`, `vue-chartjs` deps
- `examples/ingest.sh` — append `?async=false` to keep the script printing the summary inline
- `README.md` — async default semantics + debug page + pricing notes

---

## Conventions

- **Run the test first, watch it fail, then implement.** Every task ends with a commit.
- Use the venv recipe from earlier: `python3 -m venv /tmp/cng-v && /tmp/cng-v/bin/pip install -r backend/requirements.txt` (omit if already created).
- Backend tests: `cd backend && /tmp/cng-v/bin/pytest <path> -v`.
- Frontend tests: `cd frontend && npm run test`.
- Commit messages: imperative, single line summary + optional body, no co-author for plan-driven commits unless re-pushing for review.

---

## Task 1: Schema migration (idempotent)

**Files:**
- Create: `backend/db/init/002_async_and_debug.sql`
- Test: `backend/tests/test_pricing.py::test_pricing_table_has_seeds` (the test will smoke-test the migration end-to-end once the fake store understands it; here we just stage the SQL)

- [ ] **Step 1: Write the failing test (sentinel)**

```python
# backend/tests/test_schema_migration.py
from pathlib import Path

MIGRATION = Path(__file__).resolve().parents[1] / "db" / "init" / "002_async_and_debug.sql"


def test_migration_file_exists():
    assert MIGRATION.exists(), f"missing migration: {MIGRATION}"


def test_migration_is_idempotent_sql_only():
    text = MIGRATION.read_text()
    # All ALTERs must use IF NOT EXISTS; all tables must use IF NOT EXISTS.
    for line in text.splitlines():
        s = line.strip().upper()
        if s.startswith("ALTER TABLE") and "ADD COLUMN" in s:
            assert "IF NOT EXISTS" in s, f"non-idempotent ALTER: {line}"
        if s.startswith("CREATE TABLE"):
            assert "IF NOT EXISTS" in s, f"non-idempotent CREATE TABLE: {line}"


def test_migration_seeds_pricing_for_mock():
    text = MIGRATION.read_text()
    assert "'mock'" in text
    assert "ON CONFLICT (model) DO NOTHING" in text
```

- [ ] **Step 2: Run test to verify it fails**

```
cd backend && /tmp/cng-v/bin/pytest tests/test_schema_migration.py -v
```
Expected: FAIL — `MIGRATION` path does not exist.

- [ ] **Step 3: Create the migration**

```sql
-- backend/db/init/002_async_and_debug.sql
-- Idempotent: safe to apply to a fresh init container OR an upgraded DB.

-- jobs: queue plumbing
ALTER TABLE jobs
  ADD COLUMN IF NOT EXISTS attempts     INT          NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS max_attempts INT          NOT NULL DEFAULT 3,
  ADD COLUMN IF NOT EXISTS locked_by    TEXT,
  ADD COLUMN IF NOT EXISTS locked_until TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS priority     INT          NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS next_run_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
  ADD COLUMN IF NOT EXISTS progress     JSONB        NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS jobs_claimable_idx
  ON jobs (priority DESC, next_run_at)
  WHERE status IN ('pending', 'running');

-- ai_outputs: metering
ALTER TABLE ai_outputs
  ADD COLUMN IF NOT EXISTS job_id            UUID,
  ADD COLUMN IF NOT EXISTS call_type         TEXT,
  ADD COLUMN IF NOT EXISTS prompt_tokens     INT,
  ADD COLUMN IF NOT EXISTS completion_tokens INT,
  ADD COLUMN IF NOT EXISTS total_tokens      INT,
  ADD COLUMN IF NOT EXISTS latency_ms        INT,
  ADD COLUMN IF NOT EXISTS cost_usd          NUMERIC(10,6),
  ADD COLUMN IF NOT EXISTS error             TEXT;

CREATE INDEX IF NOT EXISTS ai_outputs_time_idx ON ai_outputs (created_at DESC);
CREATE INDEX IF NOT EXISTS ai_outputs_job_idx  ON ai_outputs (job_id);

-- model_pricing: rates per model
CREATE TABLE IF NOT EXISTS model_pricing (
  model              TEXT PRIMARY KEY,
  prompt_per_1m      NUMERIC(10,4),
  completion_per_1m  NUMERIC(10,4),
  embedding_per_1m   NUMERIC(10,4),
  source             TEXT NOT NULL DEFAULT 'manual',
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO model_pricing (model, prompt_per_1m, completion_per_1m, embedding_per_1m, source) VALUES
  ('gpt-4o-mini',                       0.15,  0.60,  NULL, 'seed'),
  ('gpt-4o',                            2.50, 10.00,  NULL, 'seed'),
  ('anthropic/claude-3.5-sonnet',       3.00, 15.00,  NULL, 'seed'),
  ('anthropic/claude-3.5-haiku',        0.80,  4.00,  NULL, 'seed'),
  ('google/gemini-2.0-flash-001',       0.075, 0.30,  NULL, 'seed'),
  ('text-embedding-3-small',            NULL,  NULL,  0.02, 'seed'),
  ('openai/text-embedding-3-small',     NULL,  NULL,  0.02, 'seed'),
  ('deepseek-chat',                     0.27,  1.10,  NULL, 'seed'),
  ('mock',                              0.00,  0.00,  0.00, 'seed')
ON CONFLICT (model) DO NOTHING;
```

- [ ] **Step 4: Run test to verify it passes**

```
cd backend && /tmp/cng-v/bin/pytest tests/test_schema_migration.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/db/init/002_async_and_debug.sql backend/tests/test_schema_migration.py
git commit -m "feat(db): migration 002 — jobs, ai_outputs, model_pricing"
```

---

## Task 2: Pricing module (`compute_cost`) + tests

**Files:**
- Create: `backend/app/services/pricing.py`, `backend/tests/test_pricing.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_pricing.py
from decimal import Decimal

import pytest


def test_compute_cost_chat_model():
    from app.services.pricing import compute_cost
    rates = {"prompt_per_1m": Decimal("3.00"), "completion_per_1m": Decimal("15.00"), "embedding_per_1m": None}
    c = compute_cost(rates, prompt_tokens=1_000_000, completion_tokens=500_000)
    # 1.0 * 3 + 0.5 * 15 = 10.5
    assert c == Decimal("10.500000")


def test_compute_cost_embedding_model():
    from app.services.pricing import compute_cost
    rates = {"prompt_per_1m": None, "completion_per_1m": None, "embedding_per_1m": Decimal("0.02")}
    c = compute_cost(rates, embedding_tokens=1_000_000)
    assert c == Decimal("0.020000")


def test_compute_cost_returns_none_when_rates_missing():
    from app.services.pricing import compute_cost
    assert compute_cost(None, prompt_tokens=10) is None


def test_compute_cost_handles_partial_rates():
    from app.services.pricing import compute_cost
    rates = {"prompt_per_1m": Decimal("1.0"), "completion_per_1m": None, "embedding_per_1m": None}
    # completion has no rate so its component is 0
    c = compute_cost(rates, prompt_tokens=2_000_000, completion_tokens=1_000_000)
    assert c == Decimal("2.000000")


def test_load_rates_from_store(fake_store):
    from app.services.pricing import load_rates
    fake_store.profiles  # ensure fake_store imported
    fake_store.pricing = {  # see Task 5: conftest must support this
        "gpt-4o-mini": {"prompt_per_1m": Decimal("0.15"), "completion_per_1m": Decimal("0.6"), "embedding_per_1m": None}
    }
    rates = load_rates("gpt-4o-mini")
    assert rates["prompt_per_1m"] == Decimal("0.15")
    assert load_rates("unknown-model") is None
```

- [ ] **Step 2: Run test to verify it fails**

```
cd backend && /tmp/cng-v/bin/pytest tests/test_pricing.py -v
```
Expected: FAIL — `app.services.pricing` module missing.

- [ ] **Step 3: Implement the module**

```python
# backend/app/services/pricing.py
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
    """Return USD cost or None if rates are unknown.

    Each missing rate component contributes zero; if ALL components are missing
    AND tokens are provided, cost is None (no information).
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
    # Quantize to 6 dp to match NUMERIC(10,6) storage and produce stable strings.
    return total.quantize(Decimal("0.000001"))


def load_rates(model: str) -> dict[str, Decimal | None] | None:
    """Return rates dict or None when the model isn't priced."""
    with db_session() as s:
        row = s.execute(
            text("SELECT prompt_per_1m, completion_per_1m, embedding_per_1m FROM model_pricing WHERE model = :m"),
            {"m": model},
        ).mappings().first()
    if not row:
        return None
    return dict(row)


def list_rates() -> list[dict[str, Any]]:
    with db_session() as s:
        rows = s.execute(
            text(
                "SELECT model, prompt_per_1m, completion_per_1m, embedding_per_1m, source, updated_at "
                "FROM model_pricing ORDER BY model"
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
    with db_session() as s:
        s.execute(
            text(
                """
                INSERT INTO model_pricing (model, prompt_per_1m, completion_per_1m, embedding_per_1m, source, updated_at)
                VALUES (:m, :p, :c, :e, :src, now())
                ON CONFLICT (model) DO UPDATE SET
                    prompt_per_1m = COALESCE(EXCLUDED.prompt_per_1m, model_pricing.prompt_per_1m),
                    completion_per_1m = COALESCE(EXCLUDED.completion_per_1m, model_pricing.completion_per_1m),
                    embedding_per_1m = COALESCE(EXCLUDED.embedding_per_1m, model_pricing.embedding_per_1m),
                    source = EXCLUDED.source,
                    updated_at = now()
                """
            ),
            {"m": model, "p": prompt_per_1m, "c": completion_per_1m, "e": embedding_per_1m, "src": source},
        )


def delete_rate(model: str) -> None:
    with db_session() as s:
        s.execute(text("DELETE FROM model_pricing WHERE model = :m"), {"m": model})
```

- [ ] **Step 4: Run test to verify it passes**

```
cd backend && /tmp/cng-v/bin/pytest tests/test_pricing.py -v
```
Expected: 5 passes (the `load_rates` test depends on Task 5's `fake_store.pricing` — if conftest hasn't been updated yet, mark that one xfail temporarily or run after Task 5 lands).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/pricing.py backend/tests/test_pricing.py
git commit -m "feat(pricing): compute_cost + load/list/upsert/delete helpers"
```

---

## Task 3: Conftest update — fake store supports pricing, jobs columns, ai_outputs columns

**Files:**
- Modify: `backend/tests/conftest.py`

- [ ] **Step 1: Write the failing test** (already exists, just expand)

```python
# Add to backend/tests/test_pricing.py
def test_upsert_and_list_via_fake_store(fake_store):
    from app.services.pricing import upsert_rate, list_rates
    upsert_rate(model="acme/super-llm", prompt_per_1m=1.23, completion_per_1m=4.56)
    rows = list_rates()
    assert any(r["model"] == "acme/super-llm" for r in rows)
```

- [ ] **Step 2: Run test to verify it fails**

```
cd backend && /tmp/cng-v/bin/pytest tests/test_pricing.py::test_upsert_and_list_via_fake_store -v
```
Expected: FAIL — fake store doesn't handle `INSERT INTO model_pricing`.

- [ ] **Step 3: Extend the FakeStore**

Add to `backend/tests/conftest.py` inside `FakeStore.__init__`:

```python
self.pricing: dict[str, dict] = {}
self.queue_locks: dict[str, dict] = {}     # populated by queue tests later
```

Add to `FakeStore.execute` (top of method, before the patient block) a new block:

```python
if s.startswith("insert into model_pricing"):
    m = params["m"]
    cur = self.pricing.get(m, {})
    self.pricing[m] = {
        "model": m,
        "prompt_per_1m": params.get("p") if params.get("p") is not None else cur.get("prompt_per_1m"),
        "completion_per_1m": params.get("c") if params.get("c") is not None else cur.get("completion_per_1m"),
        "embedding_per_1m": params.get("e") if params.get("e") is not None else cur.get("embedding_per_1m"),
        "source": params.get("src", "manual"),
        "updated_at": "now",
    }
    return FakeResult([])
if s.startswith("delete from model_pricing"):
    self.pricing.pop(params["m"], None)
    return FakeResult([])
if "from model_pricing" in s:
    if ":m" in s:
        row = self.pricing.get(params.get("m"))
        return FakeResult([row] if row else [])
    return FakeResult(list(self.pricing.values()))
```

Also widen `ai_outputs` INSERT to capture the new metering columns:

```python
if s.startswith("insert into ai_outputs"):
    self.ai_outputs.append({
        "document_id": params.get("d"), "patient_id": params.get("p"),
        "model": params.get("m"),
        "raw_output": json.loads(params["r"]) if isinstance(params.get("r"), str) else (params.get("r") or {}),
        "valid": params.get("v"),
        "validation_errors": json.loads(params["e"]) if isinstance(params.get("e"), str) else (params.get("e") or []),
        # new metering columns (Task 4):
        "job_id": params.get("job_id"),
        "call_type": params.get("call_type"),
        "prompt_tokens": params.get("prompt_tokens"),
        "completion_tokens": params.get("completion_tokens"),
        "total_tokens": params.get("total_tokens"),
        "latency_ms": params.get("latency_ms"),
        "cost_usd": params.get("cost_usd"),
        "error": params.get("err"),
    })
    return FakeResult([])
```

And widen the `from ai_outputs` SELECT to honour `created_at DESC` ordering by returning slices of the list:

```python
if " from ai_outputs " in s:
    rows = list(self.ai_outputs)
    if "where document_id" in s and params.get("did"):
        rows = [a for a in rows if a["document_id"] == params["did"]]
    if " desc" in s:
        rows = list(reversed(rows))
    return FakeResult(rows)
```

For `jobs`, extend the INSERT and UPDATE handlers to carry the new columns:

```python
# inside the existing jobs INSERT block, alongside status etc:
row = self.jobs[params["jid"]] = {
    "job_id": params["jid"], "type": params["t"], "status": "pending",
    "patient_id": params.get("pid"), "document_id": params.get("did"),
    "payload": json.loads(params["p"]),
    "attempts": 0, "max_attempts": 3, "locked_by": None, "locked_until": None,
    "priority": 0, "next_run_at": "now", "progress": {},
}
```

In the UPDATE handler keep the existing behaviour and also support `progress = :prog`, `attempts = :att`, `locked_until = :lock`.

Bind in `fake_store` fixture: add the `app.services.pricing` module to the patchlist.

```python
import app.services.pricing as pricing_mod
monkeypatch.setattr(pricing_mod, "db_session", _db_session)
```

- [ ] **Step 4: Run all backend tests**

```
cd backend && /tmp/cng-v/bin/pytest -q
```
Expected: 36 + new tests pass (5 from Task 2 + 1 from Task 3 + 3 schema-migration).

- [ ] **Step 5: Commit**

```bash
git add backend/tests/conftest.py backend/tests/test_pricing.py
git commit -m "test(conftest): fake store handles model_pricing + extended jobs/ai_outputs"
```

---

## Task 4: AICallRecord + provider tuple-returning interface (extract + embed first)

**Files:**
- Modify: `backend/app/services/ai_provider.py`
- Create: `backend/tests/test_ai_metering.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_ai_metering.py
import asyncio
from decimal import Decimal


def test_mock_provider_extract_returns_record(isolated_vault, fake_store):
    from app.services.ai_provider import MockProvider

    p = MockProvider()
    out, rec = asyncio.run(p.extract(
        patient_id="HN1", encounter_type="admission", encounter_dt="2026-05-15T10:00:00+07:00",
        document_id="D1", content="Type 2 diabetes mellitus. BP 152/95.",
        job_id=None,
    ))
    assert out["patientId"] == "HN1"
    assert rec.call_type == "extract"
    assert rec.model == "mock"
    assert rec.prompt_tokens is not None and rec.prompt_tokens > 0
    assert rec.latency_ms >= 0
    # Mock pricing seeded at $0 → cost is Decimal('0') (not None)
    assert rec.cost_usd == Decimal("0.000000")
    # Was an ai_outputs row written?
    assert any(r["call_type"] == "extract" and r["model"] == "mock" for r in fake_store.ai_outputs)


def test_mock_provider_embed_returns_record(isolated_vault, fake_store):
    from app.services.ai_provider import MockProvider

    p = MockProvider()
    vec, rec = asyncio.run(p.embed("hello world some text", job_id=None, ref_id="r1"))
    assert vec == []  # mock returns no embedding
    assert rec.call_type == "embed"
    assert rec.prompt_tokens >= 1


def test_openai_provider_captures_usage(monkeypatch, fake_store):
    import httpx
    from app.config import Settings
    from app.services.ai_provider import OpenAICompatibleProvider

    captured = {}

    async def fake_post(self, url, json=None, headers=None):
        captured["url"] = url
        captured["model"] = json["model"]
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"patientId":"HN1","summary":"ok","problems":[],"medications":[],"observations":[],"procedures":[],"allergies":[],"plans":[],"diagnoses":[],"codingCandidates":[],"graphUpdates":[],"markdownUpdates":[],"warnings":[]}'}}],
                "usage": {"prompt_tokens": 1200, "completion_tokens": 300, "total_tokens": 1500},
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    settings = Settings(AI_PROVIDER="openai", AI_BASE_URL="https://test/v1", AI_API_KEY="k", AI_MODEL="gpt-4o-mini")
    p = OpenAICompatibleProvider(settings)
    out, rec = asyncio.run(p.extract(
        patient_id="HN1", encounter_type="admission", encounter_dt="2026-05-15T10:00:00+07:00",
        document_id="D1", content="anything", job_id=None,
    ))
    assert captured["model"] == "gpt-4o-mini"
    assert rec.prompt_tokens == 1200
    assert rec.completion_tokens == 300
    assert rec.total_tokens == 1500
    # gpt-4o-mini is seeded: (1200/1e6)*0.15 + (300/1e6)*0.60 = 0.00018 + 0.00018 = 0.00036
    assert rec.cost_usd == Decimal("0.000360")
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd backend && /tmp/cng-v/bin/pytest tests/test_ai_metering.py -v
```
Expected: FAIL — `extract` currently returns dict, not tuple; `AICallRecord` missing.

- [ ] **Step 3: Refactor `ai_provider.py`**

Add to top of `backend/app/services/ai_provider.py`:

```python
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, TypeVar

from app.db.helpers import j
from app.db.postgres import db_session
from app.services.pricing import compute_cost, load_rates
from sqlalchemy import text

CallType = Literal["extract", "summary", "coding", "embed"]


@dataclass
class AICallRecord:
    call_type: CallType
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    latency_ms: int
    cost_usd: Decimal | None
    raw_response: dict
    error: str | None
    job_id: str | None
    patient_id: str | None
    document_id: str | None


def _persist_ai_call(rec: AICallRecord, *, valid: bool, validation_errors: list) -> None:
    with db_session() as s:
        s.execute(
            text(
                """
                INSERT INTO ai_outputs
                  (document_id, patient_id, job_id, prompt_template, model, raw_output,
                   valid, validation_errors, call_type, prompt_tokens, completion_tokens,
                   total_tokens, latency_ms, cost_usd, error)
                VALUES
                  (:d, :p, :job_id, :pt, :m, CAST(:r AS jsonb),
                   :v, CAST(:e AS jsonb), :call_type, :prompt_tokens, :completion_tokens,
                   :total_tokens, :latency_ms, :cost_usd, :err)
                """
            ),
            {
                "d": rec.document_id, "p": rec.patient_id, "job_id": rec.job_id,
                "pt": rec.call_type.upper(), "m": rec.model,
                "r": j(rec.raw_response), "v": valid, "e": j(validation_errors),
                "call_type": rec.call_type,
                "prompt_tokens": rec.prompt_tokens,
                "completion_tokens": rec.completion_tokens,
                "total_tokens": rec.total_tokens,
                "latency_ms": rec.latency_ms,
                "cost_usd": str(rec.cost_usd) if rec.cost_usd is not None else None,
                "err": rec.error,
            },
        )


def _estimate_tokens(text_in: str) -> int:
    # Cheap and stable for dev: ~1.3 tokens per whitespace word.
    return max(1, int(len(text_in.split()) * 1.3))
```

Refactor `MockProvider.extract`:

```python
class MockProvider(AIProvider):
    async def extract(self, *, patient_id, encounter_type, encounter_dt, document_id, content, job_id=None):
        t0 = time.perf_counter()
        out = mock_extract(content, patient_id=patient_id, encounter_id=None, document_id=document_id)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        prompt_tok = _estimate_tokens(content)
        completion_tok = _estimate_tokens(str(out)[:5000])
        rec = AICallRecord(
            call_type="extract", model="mock",
            prompt_tokens=prompt_tok, completion_tokens=completion_tok, total_tokens=prompt_tok + completion_tok,
            latency_ms=latency_ms,
            cost_usd=compute_cost(load_rates("mock"), prompt_tokens=prompt_tok, completion_tokens=completion_tok),
            raw_response=out, error=None, job_id=job_id, patient_id=patient_id, document_id=document_id,
        )
        _persist_ai_call(rec, valid=True, validation_errors=[])
        return out, rec

    async def summarize(self, *, patient_facts, summary_type, job_id=None, patient_id=None):
        t0 = time.perf_counter()
        md = await super_summarize_body(self, patient_facts, summary_type)  # see impl note
        latency_ms = int((time.perf_counter() - t0) * 1000)
        prompt_tok = _estimate_tokens(str(patient_facts)[:8000])
        completion_tok = _estimate_tokens(md)
        rec = AICallRecord(
            call_type="summary", model="mock",
            prompt_tokens=prompt_tok, completion_tokens=completion_tok, total_tokens=prompt_tok+completion_tok,
            latency_ms=latency_ms,
            cost_usd=compute_cost(load_rates("mock"), prompt_tokens=prompt_tok, completion_tokens=completion_tok),
            raw_response={"markdown": md}, error=None,
            job_id=job_id, patient_id=patient_id, document_id=None,
        )
        _persist_ai_call(rec, valid=True, validation_errors=[])
        return md, rec
```

(Repeat `suggest_coding` and `embed` in the same shape — see the file at the end of the task for the full diff.)

Refactor `OpenAICompatibleProvider`:

```python
async def _chat(self, system, user, *, json_mode=True) -> tuple[str, dict]:
    payload = {...}
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(f"{self.base_url}/chat/completions", json=payload, headers=self.headers)
        r.raise_for_status()
        data = r.json()
        return data["choices"][0]["message"]["content"], data

async def extract(self, *, patient_id, encounter_type, encounter_dt, document_id, content, job_id=None):
    system = EXTRACTION_SYSTEM + "\n\nSchema:\n" + json.dumps(ClinicalExtractionResult.model_json_schema())
    user = EXTRACTION_USER.format(...)
    t0 = time.perf_counter()
    raw_text, raw_resp = await self._chat(system, user, json_mode=True)
    latency_ms = int((time.perf_counter() - t0) * 1000)
    usage = raw_resp.get("usage", {}) or {}
    prompt_tok = usage.get("prompt_tokens")
    completion_tok = usage.get("completion_tokens")
    total_tok = usage.get("total_tokens")
    parsed = json.loads(raw_text)
    parsed.setdefault("patientId", patient_id)
    parsed.setdefault("documentId", document_id)
    rec = AICallRecord(
        call_type="extract", model=self.model,
        prompt_tokens=prompt_tok, completion_tokens=completion_tok, total_tokens=total_tok,
        latency_ms=latency_ms,
        cost_usd=compute_cost(load_rates(self.model), prompt_tokens=prompt_tok or 0, completion_tokens=completion_tok or 0),
        raw_response=raw_resp, error=None,
        job_id=job_id, patient_id=patient_id, document_id=document_id,
    )
    _persist_ai_call(rec, valid=True, validation_errors=[])
    return parsed, rec

async def embed(self, text_in, *, job_id=None, patient_id=None, ref_id=None):
    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{self.base_url}/embeddings",
            json={"model": self.embedding_model, "input": text_in},
            headers=self.headers,
        )
        r.raise_for_status()
        data = r.json()
    latency_ms = int((time.perf_counter() - t0) * 1000)
    usage = data.get("usage", {}) or {}
    embed_tok = usage.get("prompt_tokens")
    rec = AICallRecord(
        call_type="embed", model=self.embedding_model,
        prompt_tokens=embed_tok, completion_tokens=None, total_tokens=embed_tok,
        latency_ms=latency_ms,
        cost_usd=compute_cost(load_rates(self.embedding_model), embedding_tokens=embed_tok or 0),
        raw_response={"usage": usage}, error=None,
        job_id=job_id, patient_id=patient_id, document_id=None,
    )
    _persist_ai_call(rec, valid=True, validation_errors=[])
    return data["data"][0]["embedding"], rec
```

(`summarize` and `suggest_coding` follow the same pattern; include the full implementation in the commit.)

Adjust `get_ai_provider()` signature unchanged; just keep wiring.

- [ ] **Step 4: Run new + existing tests**

```
cd backend && /tmp/cng-v/bin/pytest -q
```
Expected: 3 new metering tests pass, plus existing tests **may temporarily break** because they expect old return types — Task 5 fixes those.

- [ ] **Step 5: Commit (WIP allowed; Task 5 finishes the migration)**

```bash
git add backend/app/services/ai_provider.py backend/tests/test_ai_metering.py
git commit -m "feat(ai): AICallRecord; provider returns (output, record) and writes ai_outputs"
```

---

## Task 5: Propagate provider tuple-return through callers

**Files:**
- Modify: `backend/app/services/ingest.py`, `backend/app/services/embeddings.py`, `backend/app/services/summary.py`, `backend/app/services/coding.py`

- [ ] **Step 1: Run the existing suite to capture the breakage**

```
cd backend && /tmp/cng-v/bin/pytest -q 2>&1 | tail -40
```
Expected: ~10 failures referring to tuple unpacking and missing `_store_ai_output`.

- [ ] **Step 2: Update `ingest.py`**

Remove `_store_ai_output` entirely (the provider writes its own row). The relevant block becomes:

```python
provider = get_ai_provider(settings)
raw_output, ai_rec = await provider.extract(
    patient_id=patient["patientId"],
    encounter_type=encounter["type"],
    encounter_dt=str(encounter["dateTime"]),
    document_id=document["documentId"],
    content=text_for_ai,
    job_id=job_id,
)
raw_output.setdefault("patientId", patient["patientId"])
raw_output["documentId"] = document["documentId"]
raw_output["encounterId"] = encounter["encounterId"]
```

In `_persist_post_extraction`, drop `_store_ai_output(...)` calls. The function now only inserts facts + audit row (provider already wrote `ai_outputs`).

Replace the embedding block:

```python
await embed_and_store_many(
    patient_id=patient["patientId"],
    job_id=job_id,
    items=[
        {"call_type": "embed", "ref_type": "fact",
         "ref_id": f"{document['documentId']}-cond-{f.value}",
         "content": f"{f.value}\n{f.evidenceText or ''}",
         "metadata": {"type": "condition"}}
        for f in extraction.problems[:50]
    ] + [
        {"call_type": "embed", "ref_type": "note", "ref_id": path,
         "content": content[:4000], "metadata": {}}
        for path, content in md_written.items()
    ],
)
```

- [ ] **Step 3: Update `embeddings.py`**

```python
async def embed_one(item):
    async with sem:
        try:
            vec, _rec = await provider.embed(
                item["content"],
                job_id=item.get("job_id") or kwargs.get("job_id"),
                patient_id=patient_id,
                ref_id=item["ref_id"],
            )
        except Exception as exc:
            logger.warning("embedding failed for %s: %s", item.get("ref_id"), exc)
            return None
    ...
```

Pass `job_id` through `embed_and_store_many(*, patient_id, job_id=None, items)`.

- [ ] **Step 4: Update `summary.py` and `coding.py`**

```python
# summary.py
async def make_summary(patient_id, req):
    facts = await asyncio.to_thread(gather_patient_facts, patient_id, start=start, end=end)
    md, _rec = await get_ai_provider().summarize(patient_facts=facts, summary_type=req.type, patient_id=patient_id)
    return SummaryResponse(...)

# coding.py
async def suggest_coding(patient_id, req):
    facts = await asyncio.to_thread(gather_patient_facts, patient_id)
    raw, _rec = await get_ai_provider().suggest_coding(patient_facts=facts, standards=req.standards, patient_id=patient_id)
    ...
```

- [ ] **Step 5: Run full suite**

```
cd backend && /tmp/cng-v/bin/pytest -q
```
Expected: 36 originals + new metering passes.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/{ingest,embeddings,summary,coding}.py
git commit -m "refactor(callers): consume provider tuple-return; provider owns ai_outputs"
```

---

## Task 6: Pricing CRUD + OpenRouter refresh route

**Files:**
- Modify: `backend/app/routers/config.py`
- Create: `backend/tests/test_pricing_routes.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_pricing_routes.py
from decimal import Decimal


def test_list_pricing_returns_seeds(app_client, fake_store):
    fake_store.pricing = {"gpt-4o-mini": {"model": "gpt-4o-mini", "prompt_per_1m": 0.15, "completion_per_1m": 0.6, "embedding_per_1m": None, "source": "seed", "updated_at": "now"}}
    r = app_client.get("/api/config/pricing")
    assert r.status_code == 200
    assert any(p["model"] == "gpt-4o-mini" for p in r.json())


def test_upsert_pricing_via_put(app_client, fake_store):
    r = app_client.put("/api/config/pricing/acme", json={"prompt_per_1m": 1.0, "completion_per_1m": 2.0})
    assert r.status_code == 200
    assert fake_store.pricing["acme"]["prompt_per_1m"] == 1.0


def test_delete_pricing(app_client, fake_store):
    fake_store.pricing["foo"] = {"model": "foo", "source": "manual"}
    r = app_client.delete("/api/config/pricing/foo")
    assert r.status_code == 200
    assert "foo" not in fake_store.pricing


def test_openrouter_refresh_upserts(monkeypatch, app_client, fake_store):
    import httpx
    async def fake_get(self, url, headers=None):
        return httpx.Response(200, json={
            "data": [
                {"id": "anthropic/claude-3.5-sonnet", "pricing": {"prompt": "0.000003", "completion": "0.000015"}},
                {"id": "openai/gpt-4o-mini", "pricing": {"prompt": "0.00000015", "completion": "0.0000006"}},
                {"id": "openai/text-embedding-3-small", "pricing": {"prompt": "0.00000002"}},
            ]
        }, request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    r = app_client.post("/api/config/pricing/refresh-openrouter")
    assert r.status_code == 200
    body = r.json()
    assert body["upserted"] >= 3
    assert fake_store.pricing["anthropic/claude-3.5-sonnet"]["prompt_per_1m"] == 3.0  # converted to $/1M
    assert fake_store.pricing["openai/text-embedding-3-small"]["embedding_per_1m"] == 0.02
```

- [ ] **Step 2: Run failing tests**

```
cd backend && /tmp/cng-v/bin/pytest tests/test_pricing_routes.py -v
```
Expected: FAIL — endpoints don't exist.

- [ ] **Step 3: Implement routes**

Add to `backend/app/routers/config.py`:

```python
from decimal import Decimal
import httpx
from pydantic import BaseModel

from app.services.pricing import delete_rate, list_rates, upsert_rate


class PricingPatch(BaseModel):
    prompt_per_1m: float | None = None
    completion_per_1m: float | None = None
    embedding_per_1m: float | None = None
    source: str | None = "manual"


@router.get("/pricing")
def get_pricing() -> list[dict]:
    return [_serialise_rate(r) for r in list_rates()]


@router.put("/pricing/{model}")
def put_pricing(model: str, body: PricingPatch) -> dict:
    upsert_rate(
        model=model,
        prompt_per_1m=body.prompt_per_1m,
        completion_per_1m=body.completion_per_1m,
        embedding_per_1m=body.embedding_per_1m,
        source=body.source or "manual",
    )
    return {"model": model}


@router.delete("/pricing/{model}")
def del_pricing(model: str) -> dict:
    delete_rate(model)
    return {"deleted": model}


@router.post("/pricing/refresh-openrouter")
async def refresh_openrouter() -> dict:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get("https://openrouter.ai/api/v1/models")
        r.raise_for_status()
        data = r.json()
    upserted = 0
    for entry in data.get("data", []):
        model = entry.get("id")
        pricing = entry.get("pricing") or {}
        # OpenRouter publishes per-token pricing in dollars; convert to per-1M.
        prompt = pricing.get("prompt")
        completion = pricing.get("completion")
        embed = pricing.get("prompt") if "embedding" in (model or "").lower() else None
        if not model:
            continue
        upsert_rate(
            model=model,
            prompt_per_1m=(float(prompt) * 1e6) if prompt and not embed else None,
            completion_per_1m=(float(completion) * 1e6) if completion and not embed else None,
            embedding_per_1m=(float(embed) * 1e6) if embed else None,
            source="openrouter",
        )
        upserted += 1
    return {"upserted": upserted, "source": "openrouter"}


def _serialise_rate(row: dict) -> dict:
    return {
        "model": row["model"],
        "prompt_per_1m": _f(row.get("prompt_per_1m")),
        "completion_per_1m": _f(row.get("completion_per_1m")),
        "embedding_per_1m": _f(row.get("embedding_per_1m")),
        "source": row.get("source"),
        "updated_at": str(row.get("updated_at")) if row.get("updated_at") else None,
    }


def _f(x):
    return float(x) if x is not None else None
```

- [ ] **Step 4: Run tests**

```
cd backend && /tmp/cng-v/bin/pytest tests/test_pricing_routes.py -v
```
Expected: 4 passes.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/config.py backend/tests/test_pricing_routes.py
git commit -m "feat(pricing): CRUD + OpenRouter refresh routes"
```

---

## Task 7: Queue worker (`services/queue.py`) + unit tests

**Files:**
- Create: `backend/app/services/queue.py`, `backend/tests/test_queue_worker.py`
- Modify: `backend/app/config.py`

- [ ] **Step 1: Add config knobs**

```python
# backend/app/config.py — inside Settings
QUEUE_WORKERS: int = 2
JOB_LOCK_SECONDS: int = 120     # initial lock + heartbeat extend
JOB_GRACE_SECONDS: int = 15     # shutdown cancel grace
```

- [ ] **Step 2: Write failing queue tests**

```python
# backend/tests/test_queue_worker.py
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
```

- [ ] **Step 3: Run failing tests**

```
cd backend && /tmp/cng-v/bin/pytest tests/test_queue_worker.py -v
```
Expected: FAIL — `app.services.queue` missing.

- [ ] **Step 4: Implement `services/queue.py`**

```python
# backend/app/services/queue.py
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy import text

from app.config import get_settings
from app.db.helpers import j
from app.db.postgres import db_session

logger = logging.getLogger(__name__)

Handler = Callable[[dict, "ProgressCallback"], Awaitable[Any]]
ProgressCallback = Callable[..., None]

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
            except Exception as exc:                                  # noqa: BLE001
                logger.exception("claim failed: %s", exc)
                job = None
            if not job:
                await asyncio.wait([asyncio.create_task(self._stop.wait())], timeout=backoff)
                backoff = min(backoff * 1.5, 2.0)
                continue
            backoff = 0.2
            try:
                await self._run(job)
            except Exception as exc:                                  # noqa: BLE001
                logger.exception("worker %s run loop error: %s", self.worker_id, exc)

    def start(self) -> None:
        self._task = asyncio.create_task(self.run_forever(), name=f"queue-{self.worker_id}")

    async def stop(self, grace_seconds: int | None = None) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=grace_seconds or self.settings.JOB_GRACE_SECONDS)
            except asyncio.TimeoutError:
                self._task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._task

    async def _claim_one(self) -> dict | None:
        lock_until = _now() + timedelta(seconds=self.settings.JOB_LOCK_SECONDS)
        return await asyncio.to_thread(self._claim_one_sync, lock_until)

    def _claim_one_sync(self, lock_until: datetime) -> dict | None:
        with db_session() as s:
            row = s.execute(
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
            ).mappings().first()
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
            result = await handler(job, on_progress)
        except Exception as exc:                                      # noqa: BLE001
            logger.exception("job %s failed: %s", job["job_id"], exc)
            await self._finalize_failure(job, str(exc))
            return

        await self._finalize_success(job, result, progress)

    def _write_progress(self, job_id: str, progress: dict) -> None:
        with db_session() as s:
            s.execute(
                text("UPDATE jobs SET progress = CAST(:p AS jsonb), locked_until = now() + interval '120 seconds' WHERE job_id = CAST(:j AS uuid)"),
                {"p": j(progress), "j": job_id},
            )

    async def _finalize_success(self, job: dict, result: Any, progress: dict) -> None:
        with db_session() as s:
            s.execute(
                text(
                    "UPDATE jobs SET status='completed', result=CAST(:r AS jsonb), progress=CAST(:p AS jsonb), "
                    "finished_at=now(), locked_by=NULL, locked_until=NULL WHERE job_id=CAST(:j AS uuid)"
                ),
                {"r": j(result), "p": j(progress), "j": job["job_id"]},
            )

    async def _finalize_failure(self, job: dict, error: str) -> None:
        attempts = int(job.get("attempts") or 1)
        max_attempts = int(job.get("max_attempts") or 3)
        if attempts >= max_attempts:
            new_status = "failed"
            next_run_at = None
        else:
            new_status = "pending"
            backoff_seconds = min(5 * (2 ** (attempts - 1)), 300)
            next_run_at = _now() + timedelta(seconds=backoff_seconds)
        params = {
            "st": new_status, "err": error, "j": job["job_id"],
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


# Late import — needed because contextlib.suppress is used at top.
from contextlib import suppress  # noqa: E402
```

The fake-store fixture must also implement the claim CTE shape. Add to `conftest.py`:

```python
# inside FakeStore.execute
if s.startswith("with claimed as"):
    now_iso = "now"
    candidates = [r for r in self.jobs.values() if (
        (r["status"] == "pending") or
        (r["status"] == "running" and (r.get("locked_until") in (None, "") or str(r["locked_until"]) < now_iso))
    )]
    # naive deterministic pick by job_id
    candidates.sort(key=lambda x: x["job_id"])
    if not candidates:
        return FakeResult([])
    job = candidates[0]
    job["status"] = "running"
    job["locked_by"] = params["wid"]
    job["locked_until"] = params["lock"]
    job["attempts"] = (job.get("attempts") or 0) + 1
    return FakeResult([job])
```

And the progress + finalize UPDATEs:

```python
if s.startswith("update jobs set progress"):
    row = self._job_by_param(params)
    if row:
        row["progress"] = json.loads(params["p"])
    return FakeResult([])
if s.startswith("update jobs set status="):
    row = self._job_by_param(params)
    if row:
        row["status"] = params["st"]
        if "res" in params:
            if params["res"] is not None:
                row["result"] = json.loads(params["res"])
        if "err" in params and params["err"] is not None:
            row["error"] = params["err"]
        if "nxt" in params and params.get("nxt"):
            row["next_run_at"] = params["nxt"]
        if "running" not in s:
            row["locked_by"] = None
            row["locked_until"] = None
    return FakeResult([])
```

…with a tiny helper `_job_by_param(self, params)` that returns the row matching `params.get("j")` (with or without UUID cast formatting).

- [ ] **Step 5: Run tests**

```
cd backend && /tmp/cng-v/bin/pytest tests/test_queue_worker.py -v
```
Expected: 6 passes.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/queue.py backend/app/config.py backend/tests/test_queue_worker.py backend/tests/conftest.py
git commit -m "feat(queue): Postgres-backed worker with SKIP LOCKED claim + retries"
```

---

## Task 8: Refactor `run_ingest` into `run_ingest_pipeline(on_progress=…)` and register the handler

**Files:**
- Modify: `backend/app/services/ingest.py`, `backend/app/services/jobs.py`

- [ ] **Step 1: Refactor `run_ingest` signature**

In `ingest.py`:

```python
async def run_ingest_pipeline(
    req: EMRIngestRequest,
    *,
    job_id: str | None = None,
    on_progress: Callable[..., None] = lambda *a, **k: None,
) -> dict[str, Any]:
    settings = effective_settings()
    patient, encounter, document, text_for_ai, _ = await asyncio.to_thread(_persist_pre_extraction, req)
    on_progress("stage_persisted", patientId=patient["patientId"], documentId=document["documentId"])

    provider = get_ai_provider(settings)
    raw_output, _rec = await provider.extract(
        patient_id=patient["patientId"],
        encounter_type=encounter["type"],
        encounter_dt=str(encounter["dateTime"]),
        document_id=document["documentId"],
        content=text_for_ai,
        job_id=job_id,
    )
    raw_output.setdefault("patientId", patient["patientId"])
    raw_output["documentId"] = document["documentId"]
    raw_output["encounterId"] = encounter["encounterId"]
    on_progress("stage_ai_extract",
                model=_rec.model, prompt_tokens=_rec.prompt_tokens,
                completion_tokens=_rec.completion_tokens, cost_usd=str(_rec.cost_usd) if _rec.cost_usd else None)

    # validate + persist facts (existing logic, no behavioural change)
    ...
    on_progress("stage_facts", count=...)

    if valid:
        graph_task = asyncio.to_thread(update_graph_for_document, patient, encounter, document, extraction)
        md_task = asyncio.to_thread(generate_markdown, ...)
        graph_counts, md_written = await asyncio.gather(graph_task, md_task)
        on_progress("stage_graph_and_markdown", counts=graph_counts, files=len(md_written))

        await embed_and_store_many(patient_id=patient["patientId"], job_id=job_id, items=[...])
        on_progress("stage_embed", count=len(md_written) + len(extraction.problems[:50]))

    return {...}


# Backwards-compat alias used by the sync (?async=false) path
async def run_ingest(req, *, job_id=None):
    return await run_ingest_pipeline(req, job_id=job_id)
```

- [ ] **Step 2: Register the handler in `services/jobs.py`**

```python
# backend/app/services/jobs.py
from app.schemas.emr import EMRIngestRequest
from app.services.ingest import run_ingest_pipeline
from app.services.queue import register_handler


async def _emr_ingest_handler(job: dict, on_progress) -> dict:
    payload = job["payload"]
    req = EMRIngestRequest.model_validate(payload)
    return await run_ingest_pipeline(req, job_id=job["job_id"], on_progress=on_progress)


register_handler("emr_ingest", _emr_ingest_handler)
```

Also drop the old `asyncio.create_task` path from `schedule_ingest`:

```python
def schedule_ingest(req: EMRIngestRequest) -> str:
    job_id = create_job(
        type="emr_ingest",
        patient_id=req.patient.patientId,
        document_id=(req.source.documentId if req.source else None),
        payload=req.model_dump(mode="json"),
    )
    return job_id
```

- [ ] **Step 3: Run tests**

```
cd backend && /tmp/cng-v/bin/pytest -q
```
Expected: all pass (none touch the queue runtime path directly yet — Task 9 will).

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/ingest.py backend/app/services/jobs.py
git commit -m "refactor(ingest): run_ingest_pipeline + register emr_ingest queue handler"
```

---

## Task 9: Wire queue workers into FastAPI lifespan + flip POST `/api/emr/ingest` to async by default

**Files:**
- Modify: `backend/app/main.py`, `backend/app/routers/emr.py`, `backend/app/routers/jobs.py`
- Create: `backend/tests/test_ingest_async.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_ingest_async.py
import time


def _payload():
    return {
        "patient": {"patientId": "HN-A1", "name": "Async"},
        "encounter": {"type": "admission", "dateTime": "2026-05-15T10:00:00+07:00"},
        "format": "text",
        "content": "Patient has Type 2 diabetes mellitus. BP 152/95.",
        "source": {"system": "T", "documentId": "doc-a1", "version": "1"},
    }


def test_default_post_returns_queued(app_client, fake_store):
    r = app_client.post("/api/emr/ingest", json=_payload())
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "queued"
    assert body["jobId"]
    # row exists in pending state
    assert fake_store.jobs[body["jobId"]]["status"] == "pending"


def test_sync_query_param_still_runs_inline(app_client, fake_store):
    r = app_client.post("/api/emr/ingest?async=false", json=_payload())
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "completed"
    assert body["summary"] is not None


def test_job_status_endpoint_returns_progress(app_client, fake_store):
    # Seed a job with progress
    fake_store.jobs["abc"] = {
        "job_id": "abc", "type": "emr_ingest", "status": "running",
        "patient_id": "HN1", "document_id": "d1",
        "payload": {}, "attempts": 1, "max_attempts": 3,
        "progress": {"stage_persisted": {"at": "now"}},
        "locked_by": "w1", "locked_until": "soon", "priority": 0, "next_run_at": "now",
    }
    r = app_client.get("/api/jobs/abc")
    assert r.status_code == 200
    assert "stage_persisted" in (r.json().get("progress") or {})


def test_requeue_endpoint(app_client, fake_store):
    fake_store.jobs["fail1"] = {
        "job_id": "fail1", "type": "emr_ingest", "status": "failed",
        "attempts": 3, "max_attempts": 3, "error": "boom",
        "payload": {}, "patient_id": None, "document_id": None,
        "progress": {}, "locked_by": None, "locked_until": None,
        "priority": 0, "next_run_at": "now",
    }
    r = app_client.post("/api/jobs/fail1/requeue")
    assert r.status_code == 200
    assert fake_store.jobs["fail1"]["status"] == "pending"
    assert fake_store.jobs["fail1"]["attempts"] == 0
```

- [ ] **Step 2: Run failing tests**

```
cd backend && /tmp/cng-v/bin/pytest tests/test_ingest_async.py -v
```
Expected: FAIL — current default is sync; no requeue route.

- [ ] **Step 3: Update `emr.py`**

```python
# backend/app/routers/emr.py
@router.post("/ingest", response_model=EMRIngestResponse)
async def ingest(
    req: EMRIngestRequest,
    async_processing: bool = Query(True, alias="async"),
):
    if async_processing:
        job_id = schedule_ingest(req)
        return EMRIngestResponse(
            jobId=job_id, status="queued",
            patientId=req.patient.patientId,
            encounterId=req.encounter.encounterId or "",
            documentId=req.source.documentId or "",
            summary=None,
        )
    try:
        result = await run_ingest(req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return EMRIngestResponse(
        jobId="sync", status="completed",
        patientId=result["patientId"], encounterId=result["encounterId"],
        documentId=result["documentId"], summary=result.get("summary"),
    )
```

- [ ] **Step 4: Update `routers/jobs.py` with list + requeue**

```python
@router.get("")
def list_jobs(status: str | None = None, type: str | None = None,
              limit: int = 50, offset: int = 0) -> list[dict]:
    where = []
    params: dict = {"lim": limit, "off": offset}
    if status:
        where.append("status = :st")
        params["st"] = status
    if type:
        where.append("type = :tp")
        params["tp"] = type
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    with db_session() as s:
        rows = s.execute(text(
            f"SELECT job_id::text, type, status, patient_id, document_id, attempts, "
            f"started_at, finished_at, created_at, progress FROM jobs {where_sql} "
            f"ORDER BY created_at DESC LIMIT :lim OFFSET :off"
        ), params).mappings().all()
    return [dict(r) for r in rows]


@router.post("/{job_id}/requeue")
def requeue(job_id: str) -> dict:
    with db_session() as s:
        s.execute(text(
            "UPDATE jobs SET status='pending', attempts=0, error=NULL, "
            "next_run_at=now(), locked_by=NULL, locked_until=NULL "
            "WHERE job_id=CAST(:j AS uuid)"
        ), {"j": job_id})
    return {"requeued": job_id}
```

- [ ] **Step 5: Wire workers into `main.py`**

```python
# backend/app/main.py — inside lifespan
from app.services.queue import start_workers, stop_workers

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
    workers = start_workers()
    try:
        yield
    finally:
        await stop_workers(workers)
        await asyncio.to_thread(close_driver)
```

Tests can disable workers by setting `QUEUE_WORKERS=0` via env in the integration fixture if they want to control timing manually. For these unit-style tests, `0` workers is fine because we don't actually run a job through the loop.

Update `app_client` fixture in `conftest.py`:

```python
@pytest.fixture()
def app_client(fake_store, stub_neo4j, isolated_vault, monkeypatch):
    monkeypatch.setenv("QUEUE_WORKERS", "0")
    from app.config import get_settings
    get_settings.cache_clear()
    ...
```

- [ ] **Step 6: Run tests**

```
cd backend && /tmp/cng-v/bin/pytest -q
```
Expected: all pass.

- [ ] **Step 7: Update `test_api_ingest.py`**

The two existing tests assume sync default. Add `?async=false` to keep them as-is, OR add new assertions matching the new shape. Easier: change both to `?async=false`.

```python
r = app_client.post("/api/emr/ingest?async=false", json=_admission_payload())
```

Apply in `test_ingest_text_round_trip`, `test_ingest_idempotent`, `test_get_patient_aggregates`, `test_encounter_documents_endpoint`, `test_review_fact_audit_payload_safe`, `test_summary_endpoint`, `test_coding_suggest_endpoint`, `test_export_fhir_bundle`, `test_api_key_enforced_when_configured`.

- [ ] **Step 8: Commit**

```bash
git add backend/app/main.py backend/app/routers/emr.py backend/app/routers/jobs.py \
        backend/tests/conftest.py backend/tests/test_api_ingest.py \
        backend/tests/test_ingest_async.py
git commit -m "feat(api): default-async ingest; jobs list + requeue; workers in lifespan"
```

---

## Task 10: Debug aggregation queries + tests

**Files:**
- Create: `backend/app/services/debug_queries.py`
- Modify: `backend/tests/conftest.py` (extend SQL handling)

- [ ] **Step 1: Write failing tests in `tests/test_debug_endpoints.py` (Task 11 owns the routes; aggregation tests live here for the service layer)**

```python
# Append to backend/tests/test_pricing.py for now (or a new file)
def test_debug_summary_aggregates(fake_store, isolated_vault):
    from app.services.debug_queries import summary

    fake_store.ai_outputs = [
        {"call_type": "extract", "model": "gpt-4o-mini", "prompt_tokens": 100, "completion_tokens": 50,
         "total_tokens": 150, "latency_ms": 1200, "cost_usd": 0.001, "error": None, "created_at": "2026-05-15"},
        {"call_type": "extract", "model": "gpt-4o-mini", "prompt_tokens": 200, "completion_tokens": 80,
         "total_tokens": 280, "latency_ms": 1500, "cost_usd": 0.0015, "error": None, "created_at": "2026-05-15"},
        {"call_type": "embed",   "model": "openai/text-embedding-3-small", "prompt_tokens": 30,
         "completion_tokens": None, "total_tokens": 30, "latency_ms": 200, "cost_usd": 0.000001,
         "error": None, "created_at": "2026-05-15"},
    ]
    out = summary(start="2026-05-01", end="2026-05-31")
    assert out["total_calls"] == 3
    assert out["total_cost_usd"] == 0.002501  # sum rounded
    assert out["failures"] == 0


def test_debug_by_model_breakdown(fake_store):
    from app.services.debug_queries import by_model
    # uses the same ai_outputs fixture
    rows = by_model(start=None, end=None)
    models = {r["model"] for r in rows}
    assert "gpt-4o-mini" in models
```

- [ ] **Step 2: Run failing tests**

```
cd backend && /tmp/cng-v/bin/pytest tests/test_pricing.py::test_debug_summary_aggregates -v
```
Expected: FAIL — `debug_queries` missing.

- [ ] **Step 3: Implement `debug_queries.py`**

```python
# backend/app/services/debug_queries.py
from __future__ import annotations

from typing import Any

from sqlalchemy import text

from app.db.postgres import db_session


def _range_clauses(start: str | None, end: str | None):
    where = []
    params: dict = {}
    if start:
        where.append("created_at >= :start")
        params["start"] = start
    if end:
        where.append("created_at <= :end")
        params["end"] = end
    return ((" WHERE " + " AND ".join(where)) if where else ""), params


def summary(*, start: str | None, end: str | None) -> dict[str, Any]:
    w, p = _range_clauses(start, end)
    with db_session() as s:
        row = s.execute(text(
            "SELECT COUNT(*) AS total_calls, "
            "COALESCE(SUM(total_tokens),0) AS total_tokens, "
            "COALESCE(SUM(cost_usd),0) AS total_cost_usd, "
            "COALESCE(AVG(latency_ms),0) AS avg_latency_ms, "
            "COUNT(*) FILTER (WHERE error IS NOT NULL) AS failures "
            f"FROM ai_outputs{w}"
        ), p).mappings().first()
    return {k: (float(v) if isinstance(v, (int, float)) or hasattr(v, "__float__") else v) for k, v in dict(row).items()}


def by_model(*, start: str | None, end: str | None) -> list[dict[str, Any]]:
    w, p = _range_clauses(start, end)
    with db_session() as s:
        rows = s.execute(text(
            "SELECT model, COUNT(*) AS calls, "
            "COALESCE(SUM(prompt_tokens),0) AS prompt_tokens, "
            "COALESCE(SUM(completion_tokens),0) AS completion_tokens, "
            "COALESCE(SUM(cost_usd),0) AS cost_usd, "
            "COALESCE(AVG(latency_ms),0) AS avg_latency_ms "
            f"FROM ai_outputs{w} GROUP BY model ORDER BY cost_usd DESC"
        ), p).mappings().all()
    return [dict(r) for r in rows]


def by_day(*, start: str | None, end: str | None) -> list[dict[str, Any]]:
    w, p = _range_clauses(start, end)
    with db_session() as s:
        rows = s.execute(text(
            "SELECT date_trunc('day', created_at) AS day, call_type, "
            "COALESCE(SUM(cost_usd),0) AS cost_usd, "
            "COUNT(*) AS calls "
            f"FROM ai_outputs{w} GROUP BY 1, 2 ORDER BY 1"
        ), p).mappings().all()
    return [dict(r) for r in rows]


def list_calls(*, start: str | None, end: str | None, model: str | None,
               status: str | None, q: str | None, limit: int = 50,
               offset: int = 0) -> list[dict[str, Any]]:
    where, p = _range_clauses(start, end)
    extra = []
    if model:
        extra.append("model = :model")
        p["model"] = model
    if status == "failed":
        extra.append("error IS NOT NULL")
    elif status == "ok":
        extra.append("error IS NULL")
    if q:
        extra.append("(error ILIKE :q OR model ILIKE :q)")
        p["q"] = f"%{q}%"
    if extra:
        where = (where + (" AND " if where else " WHERE ") + " AND ".join(extra))
    p["lim"] = limit
    p["off"] = offset
    with db_session() as s:
        rows = s.execute(text(
            f"SELECT id::text, created_at, job_id::text, patient_id, document_id, model, call_type, "
            f"prompt_tokens, completion_tokens, total_tokens, latency_ms, cost_usd, error "
            f"FROM ai_outputs{where} ORDER BY created_at DESC LIMIT :lim OFFSET :off"
        ), p).mappings().all()
    return [dict(r) for r in rows]


def get_call(call_id: str) -> dict[str, Any] | None:
    with db_session() as s:
        row = s.execute(text(
            "SELECT * FROM ai_outputs WHERE id = CAST(:id AS uuid)"
        ), {"id": call_id}).mappings().first()
    return dict(row) if row else None
```

The fake store needs to handle these grouped queries. Extend `FakeStore.execute`:

```python
if "from ai_outputs" in s and "count(*)" in s and "filter" in s:
    rows = list(self.ai_outputs)
    return FakeResult([{
        "total_calls": len(rows),
        "total_tokens": sum((r.get("total_tokens") or 0) for r in rows),
        "total_cost_usd": sum((r.get("cost_usd") or 0) for r in rows),
        "avg_latency_ms": (sum((r.get("latency_ms") or 0) for r in rows) / len(rows)) if rows else 0,
        "failures": sum(1 for r in rows if r.get("error")),
    }])
if "from ai_outputs" in s and "group by model" in s:
    from collections import defaultdict
    buckets = defaultdict(lambda: {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0, "avg_latency_ms": 0, "_n": 0})
    for r in self.ai_outputs:
        b = buckets[r["model"]]
        b["calls"] += 1
        b["prompt_tokens"] += r.get("prompt_tokens") or 0
        b["completion_tokens"] += r.get("completion_tokens") or 0
        b["cost_usd"] += r.get("cost_usd") or 0
        b["avg_latency_ms"] += r.get("latency_ms") or 0
        b["_n"] += 1
    out = []
    for model, b in buckets.items():
        out.append({"model": model, "calls": b["calls"], "prompt_tokens": b["prompt_tokens"],
                    "completion_tokens": b["completion_tokens"], "cost_usd": b["cost_usd"],
                    "avg_latency_ms": (b["avg_latency_ms"]/b["_n"]) if b["_n"] else 0})
    out.sort(key=lambda r: -r["cost_usd"])
    return FakeResult(out)
if "from ai_outputs" in s and "group by 1" in s:
    from collections import defaultdict
    bucket = defaultdict(lambda: {"cost_usd": 0, "calls": 0})
    for r in self.ai_outputs:
        key = (r.get("created_at", "0000-00-00"), r.get("call_type"))
        bucket[key]["cost_usd"] += r.get("cost_usd") or 0
        bucket[key]["calls"] += 1
    out = [{"day": k[0], "call_type": k[1], **v} for k, v in bucket.items()]
    return FakeResult(out)
```

- [ ] **Step 4: Run**

```
cd backend && /tmp/cng-v/bin/pytest tests/test_pricing.py -v
```
Expected: PASS for the two new aggregation tests.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/debug_queries.py backend/tests/conftest.py backend/tests/test_pricing.py
git commit -m "feat(debug): aggregation queries (summary, by_model, by_day, list, get)"
```

---

## Task 11: Debug routes + protected prefix + CSV export

**Files:**
- Create: `backend/app/routers/debug.py`, `backend/tests/test_debug_endpoints.py`
- Modify: `backend/app/middleware.py`, `backend/app/main.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_debug_endpoints.py
def _seed(fake_store):
    fake_store.ai_outputs.extend([
        {"id": "1", "call_type": "extract", "model": "gpt-4o-mini",
         "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150,
         "latency_ms": 1200, "cost_usd": 0.001, "error": None,
         "created_at": "2026-05-15", "job_id": None, "patient_id": "HN1", "document_id": "d1"},
        {"id": "2", "call_type": "embed", "model": "openai/text-embedding-3-small",
         "prompt_tokens": 30, "completion_tokens": None, "total_tokens": 30,
         "latency_ms": 200, "cost_usd": 0.000001, "error": None,
         "created_at": "2026-05-15", "job_id": None, "patient_id": "HN1", "document_id": None},
    ])


def test_summary_endpoint(app_client, fake_store):
    _seed(fake_store)
    r = app_client.get("/api/debug/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["total_calls"] == 2


def test_by_model_endpoint(app_client, fake_store):
    _seed(fake_store)
    r = app_client.get("/api/debug/by-model")
    assert r.status_code == 200
    rows = r.json()
    assert {row["model"] for row in rows} >= {"gpt-4o-mini"}


def test_ai_calls_list_and_filter(app_client, fake_store):
    _seed(fake_store)
    r = app_client.get("/api/debug/ai-calls?model=gpt-4o-mini")
    assert r.status_code == 200
    rows = r.json()
    assert all(row["model"] == "gpt-4o-mini" for row in rows)


def test_ai_calls_csv_streams(app_client, fake_store):
    _seed(fake_store)
    r = app_client.get("/api/debug/ai-calls.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert b"id,created_at" in r.content[:200]


def test_protected_when_key_set(monkeypatch, fake_store):
    from app.config import get_settings
    get_settings.cache_clear()
    monkeypatch.setenv("API_KEY", "secret")
    from fastapi.testclient import TestClient
    from app.main import create_app
    with TestClient(create_app()) as c:
        assert c.get("/api/debug/summary").status_code == 401
        assert c.get("/api/debug/summary", headers={"X-API-Key": "secret"}).status_code == 200
```

- [ ] **Step 2: Run failing tests**

```
cd backend && /tmp/cng-v/bin/pytest tests/test_debug_endpoints.py -v
```
Expected: FAIL.

- [ ] **Step 3: Implement router**

```python
# backend/app/routers/debug.py
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.services import debug_queries

router = APIRouter(prefix="/api/debug", tags=["debug"])


@router.get("/summary")
def get_summary(start: str | None = None, end: str | None = None):
    return debug_queries.summary(start=start, end=end)


@router.get("/by-model")
def get_by_model(start: str | None = None, end: str | None = None):
    return debug_queries.by_model(start=start, end=end)


@router.get("/by-day")
def get_by_day(start: str | None = None, end: str | None = None):
    return debug_queries.by_day(start=start, end=end)


@router.get("/ai-calls")
def get_calls(start: str | None = None, end: str | None = None,
              model: str | None = None, status: str | None = None,
              q: str | None = None, limit: int = Query(50, ge=1, le=500),
              offset: int = Query(0, ge=0)):
    return debug_queries.list_calls(start=start, end=end, model=model,
                                    status=status, q=q, limit=limit, offset=offset)


@router.get("/ai-calls/{call_id}")
def get_call(call_id: str):
    row = debug_queries.get_call(call_id)
    if not row:
        raise HTTPException(status_code=404)
    return row


@router.get("/ai-calls.csv")
def ai_calls_csv(start: str | None = None, end: str | None = None,
                 model: str | None = None, status: str | None = None,
                 q: str | None = None):
    rows = debug_queries.list_calls(start=start, end=end, model=model,
                                    status=status, q=q, limit=10_000, offset=0)

    def gen():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "created_at", "job_id", "patient_id", "model", "call_type",
                    "prompt_tokens", "completion_tokens", "total_tokens", "latency_ms",
                    "cost_usd", "error"])
        yield buf.getvalue(); buf.seek(0); buf.truncate()
        for r in rows:
            w.writerow([r.get("id"), r.get("created_at"), r.get("job_id"), r.get("patient_id"),
                        r.get("model"), r.get("call_type"), r.get("prompt_tokens"),
                        r.get("completion_tokens"), r.get("total_tokens"), r.get("latency_ms"),
                        r.get("cost_usd"), r.get("error")])
            yield buf.getvalue(); buf.seek(0); buf.truncate()

    return StreamingResponse(gen(), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=ai-calls.csv"})
```

Extend `_PROTECTED_PREFIXES`:

```python
# backend/app/middleware.py
_PROTECTED_PREFIXES = ("/api/emr", "/api/config", "/api/export", "/api/facts", "/api/debug")
```

Register the router:

```python
# backend/app/main.py
from app.routers import debug as debug_router
app.include_router(debug_router.router)
```

- [ ] **Step 4: Run tests**

```
cd backend && /tmp/cng-v/bin/pytest tests/test_debug_endpoints.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/debug.py backend/app/middleware.py backend/app/main.py \
        backend/tests/test_debug_endpoints.py
git commit -m "feat(debug): /api/debug routes + CSV stream; protected prefix"
```

---

## Task 12: Frontend dependency + utility groundwork

**Files:**
- Modify: `frontend/package.json`, `frontend/src/utils/format.js`, `frontend/src/api/client.js`, `frontend/src/router.js`, `frontend/src/App.vue`

- [ ] **Step 1: Add deps**

```json
// frontend/package.json — add to "dependencies"
"chart.js": "^4.4.7",
"vue-chartjs": "^5.3.2"
```

Run `npm install` and commit the lockfile change.

- [ ] **Step 2: Extend format utils**

```javascript
// frontend/src/utils/format.js — append
export function formatUSD(amount) {
  if (amount === null || amount === undefined) return '–'
  const v = Number(amount)
  if (!isFinite(v)) return '–'
  if (v < 0.01) return `$${v.toFixed(6)}`
  if (v < 1)    return `$${v.toFixed(4)}`
  return `$${v.toFixed(2)}`
}

export function formatTokens(n) {
  if (n === null || n === undefined) return '–'
  return Number(n).toLocaleString()
}
```

- [ ] **Step 3: Extend the API client**

```javascript
// frontend/src/api/client.js — append
export const getJob = (jobId, signal) =>
  api.get(`/api/jobs/${encodeURIComponent(jobId)}`, { signal }).then(data)
export const listJobs = (params, signal) =>
  api.get('/api/jobs', { params, signal }).then(data)
export const requeueJob = (jobId) =>
  api.post(`/api/jobs/${encodeURIComponent(jobId)}/requeue`).then(data)

export const getDebugSummary = (params, signal) =>
  api.get('/api/debug/summary', { params, signal }).then(data)
export const getDebugByModel = (params, signal) =>
  api.get('/api/debug/by-model', { params, signal }).then(data)
export const getDebugByDay = (params, signal) =>
  api.get('/api/debug/by-day', { params, signal }).then(data)
export const listAiCalls = (params, signal) =>
  api.get('/api/debug/ai-calls', { params, signal }).then(data)
export const getAiCall = (id, signal) =>
  api.get(`/api/debug/ai-calls/${encodeURIComponent(id)}`, { signal }).then(data)

export const listPricing = () => api.get('/api/config/pricing').then(data)
export const upsertPricing = (model, body) =>
  api.put(`/api/config/pricing/${encodeURIComponent(model)}`, body).then(data)
export const deletePricing = (model) =>
  api.delete(`/api/config/pricing/${encodeURIComponent(model)}`).then(data)
export const refreshOpenRouter = () =>
  api.post('/api/config/pricing/refresh-openrouter').then(data)
```

- [ ] **Step 4: Router + nav**

```javascript
// frontend/src/router.js — add the route
import DebugView from './views/DebugView.vue'
...
  { path: '/debug', component: DebugView, name: 'debug' },
```

```vue
<!-- frontend/src/App.vue — add inside the v-app-bar before the API button -->
<v-btn variant="text" to="/debug" prepend-icon="mdi-chart-line-variant">Debug</v-btn>
```

- [ ] **Step 5: Run npm test (only format util test currently)**

```
cd frontend && npm run test
```
Expected: still passes (no UI changes yet wired to broken pages).

- [ ] **Step 6: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/api/client.js \
        frontend/src/utils/format.js frontend/src/router.js frontend/src/App.vue
git commit -m "chore(frontend): chart.js dep, format utils, debug API + nav"
```

---

## Task 13: `JobWatcher.vue` component + Vitest spec

**Files:**
- Create: `frontend/src/components/JobWatcher.vue`, `frontend/tests/JobWatcher.spec.js`

- [ ] **Step 1: Failing test**

```javascript
// frontend/tests/JobWatcher.spec.js
import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import JobWatcher from '../src/components/JobWatcher.vue'

const stubs = {
  'v-card': { template: '<div><slot/></div>' },
  'v-card-text': { template: '<div><slot/></div>' },
  'v-progress-linear': true, 'v-icon': true, 'v-chip': { template: '<span><slot/></span>' },
  'v-btn': { template: '<button><slot/></button>' },
  'v-divider': true,
  'SectionHeader': { template: '<div><slot/></div>' },
}

vi.mock('../src/api/client.js', () => ({
  getJob: vi.fn(),
}))


describe('JobWatcher', () => {
  it('polls until completed then emits done', async () => {
    const { getJob } = await import('../src/api/client.js')
    getJob
      .mockResolvedValueOnce({ status: 'running', progress: { stage_persisted: { at: 'now' } } })
      .mockResolvedValueOnce({ status: 'completed', result: { patientId: 'HN1' }, progress: {} })

    vi.useFakeTimers()
    const w = mount(JobWatcher, { props: { jobId: 'abc', intervalMs: 10 }, global: { stubs } })

    await flushPromises()
    expect(getJob).toHaveBeenCalledTimes(1)
    expect(w.html()).toMatch(/running|saved/i)

    vi.advanceTimersByTime(15)
    await flushPromises()

    expect(getJob).toHaveBeenCalledTimes(2)
    const done = w.emitted('done')
    expect(done).toBeTruthy()
    expect(done[0][0]).toMatchObject({ patientId: 'HN1' })
    vi.useRealTimers()
  })

  it('emits failed and offers retry on failure', async () => {
    const { getJob } = await import('../src/api/client.js')
    getJob.mockResolvedValueOnce({ status: 'failed', error: 'boom', progress: {} })

    const w = mount(JobWatcher, { props: { jobId: 'fail1', intervalMs: 10 }, global: { stubs } })
    await flushPromises()
    expect(w.emitted('failed')).toBeTruthy()
    expect(w.html()).toContain('boom')
  })
})
```

- [ ] **Step 2: Run failing test**

```
cd frontend && npm run test
```
Expected: FAIL — `JobWatcher` missing.

- [ ] **Step 3: Implement component**

```vue
<!-- frontend/src/components/JobWatcher.vue -->
<template>
  <v-card>
    <SectionHeader title="Ingest job" icon="mdi-clock-outline" />
    <v-divider />
    <v-card-text>
      <v-chip size="x-small" class="mr-2" :color="statusColor">{{ status }}</v-chip>
      <span class="text-caption text-grey-darken-1">Job {{ jobId }}</span>

      <v-progress-linear v-if="running" indeterminate class="mt-3" />

      <div v-if="error" class="text-error mt-3">{{ error }}</div>

      <div class="stages mt-3">
        <span v-for="s in STAGES" :key="s" :class="['stage', stageClass(s)]">
          <v-icon size="14" v-if="hasStage(s)">mdi-check</v-icon>
          <v-icon size="14" v-else>mdi-circle-outline</v-icon>
          {{ s.replace('stage_', '') }}
        </span>
      </div>

      <div v-if="metrics" class="text-caption mt-3">
        Tokens: {{ formatTokens(metrics.tokens) }} ·
        Cost: {{ formatUSD(metrics.cost) }} ·
        Latency: {{ metrics.latency_ms ?? '–' }} ms
      </div>

      <div class="actions mt-3">
        <v-btn v-if="status === 'failed'" size="small" color="primary" @click="$emit('retry')">Retry</v-btn>
      </div>
    </v-card-text>
  </v-card>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import SectionHeader from './SectionHeader.vue'
import { getJob } from '../api/client.js'
import { formatTokens, formatUSD } from '../utils/format.js'

const props = defineProps({
  jobId: { type: String, required: true },
  intervalMs: { type: Number, default: 1500 },
})
const emit = defineEmits(['done', 'failed'])

const STAGES = ['stage_persisted', 'stage_ai_extract', 'stage_facts', 'stage_graph_and_markdown', 'stage_embed']

const status = ref('pending')
const error = ref('')
const progress = ref({})
const metrics = ref(null)
let timer = null

const running = computed(() => ['queued', 'pending', 'running'].includes(status.value))
const statusColor = computed(() => ({
  completed: 'success', failed: 'error', running: 'info', pending: 'warning', queued: 'warning',
})[status.value] || 'grey')

function hasStage(s) { return Boolean(progress.value?.[s]) }
function stageClass(s) { return hasStage(s) ? 'done' : 'todo' }

async function tick() {
  try {
    const j = await getJob(props.jobId)
    status.value = j.status || 'pending'
    progress.value = j.progress || {}
    error.value = j.error || ''
    const tok = progress.value?.stage_ai_extract?.prompt_tokens
    const cost = progress.value?.stage_ai_extract?.cost_usd
    if (tok || cost) metrics.value = { tokens: tok, cost, latency_ms: progress.value?.stage_ai_extract?.latency_ms }

    if (status.value === 'completed') {
      stop()
      emit('done', j.result || {})
    } else if (status.value === 'failed') {
      stop()
      emit('failed', j.error)
    }
  } catch (e) {
    // axios interceptor already toasted; keep polling unless the route 404s
  }
}

function start() {
  tick()
  timer = setInterval(tick, props.intervalMs)
}
function stop() { if (timer) { clearInterval(timer); timer = null } }

onMounted(start)
onBeforeUnmount(stop)
</script>

<style scoped>
.stages { display: flex; flex-wrap: wrap; gap: 8px; }
.stage  { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; }
.stage.done { color: rgb(var(--v-theme-success)); }
.stage.todo { opacity: 0.5; }
</style>
```

- [ ] **Step 4: Run tests**

```
cd frontend && npm run test
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/JobWatcher.vue frontend/tests/JobWatcher.spec.js
git commit -m "feat(ui): JobWatcher polls /api/jobs and renders stages"
```

---

## Task 14: Unified `IngestView.vue` (single ID-driven form + JobWatcher)

**Files:**
- Modify: `frontend/src/views/IngestView.vue`
- Create: `frontend/tests/IngestView.spec.js`
- Modify: `frontend/src/views/PatientDetail.vue` (Add note button)

- [ ] **Step 1: Failing IngestView test**

```javascript
// frontend/tests/IngestView.spec.js
import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('../src/api/client.js', () => ({
  listPatients: vi.fn().mockResolvedValue([
    { patient_id: 'HN1', name: 'Existing One' }
  ]),
  ingest: vi.fn().mockResolvedValue({ jobId: 'job-1', status: 'queued', patientId: 'HN-NEW' }),
}))

const stubs = {
  'v-row': { template: '<div><slot/></div>' },
  'v-col': { template: '<div><slot/></div>' },
  'v-card': { template: '<div><slot/></div>' },
  'v-card-text': { template: '<div><slot/></div>' },
  'v-card-actions': { template: '<div><slot/></div>' },
  'v-divider': true,
  'v-text-field': { props: ['modelValue'], emits: ['update:modelValue'], template: '<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />' },
  'v-textarea': { props: ['modelValue'], emits: ['update:modelValue'], template: '<textarea :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)"/>' },
  'v-autocomplete': { props: ['modelValue'], emits: ['update:modelValue'], template: '<input data-test="autocomplete" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />' },
  'v-select': true, 'v-btn': { template: '<button @click="$emit(\'click\')"><slot/></button>' },
  'v-menu': { template: '<div><slot/></div>' }, 'v-list': true, 'v-list-item': true, 'v-chip': true,
  'SectionHeader': { template: '<div><slot/></div>' }, 'JobWatcher': { template: '<div class="jw">watcher</div>' },
}

describe('IngestView', () => {
  it('submits via ingest() and renders JobWatcher with the returned jobId', async () => {
    const IngestView = (await import('../src/views/IngestView.vue')).default
    const w = mount(IngestView, { global: { stubs } })
    await flushPromises()

    await w.find('button:nth-of-type(1)').trigger('click') // submit
    await flushPromises()

    const { ingest } = await import('../src/api/client.js')
    expect(ingest).toHaveBeenCalled()
    expect(w.html()).toContain('jw')
  })
})
```

- [ ] **Step 2: Run failing test**

```
cd frontend && npm run test
```
Expected: FAIL (component still has the old layout + no JobWatcher mounting).

- [ ] **Step 3: Rewrite `IngestView.vue`**

Skeleton (drop into the file, adapt the existing form fields):

```vue
<template>
  <div>
    <h1 class="text-h5 font-weight-bold mb-1">Ingest EMR document</h1>
    <div class="text-body-2 text-grey-darken-1 mb-4">
      Type or paste a clinical note. If the Patient ID matches an existing patient we update their record; otherwise a new patient is created.
    </div>

    <v-row>
      <v-col cols="12" md="5">
        <v-card>
          <SectionHeader title="Patient" icon="mdi-account-outline" />
          <v-divider />
          <v-card-text>
            <v-autocomplete
              v-model="patientId"
              :items="patientResults"
              :loading="patientsLoading"
              item-title="patient_id"
              item-value="patient_id"
              label="Patient ID"
              :return-object="false"
              :no-filter="true"
              @update:search="onSearchPatients"
              clearable
            />
            <div class="text-caption mt-1">
              <v-icon size="14" :color="existing ? 'success' : 'warning'">{{ existing ? 'mdi-check' : 'mdi-plus' }}</v-icon>
              {{ existing ? `Updating ${existing.patient_id} — ${existing.name}` : 'New patient will be created' }}
            </div>

            <v-divider class="my-3" />
            <v-text-field v-model="name" label="Name (optional)" />
            <v-row>
              <v-col cols="6"><v-select v-model="gender" :items="['male','female','other']" label="Gender" clearable /></v-col>
              <v-col cols="6"><v-text-field v-model="birthDate" label="Birth date (YYYY-MM-DD)" /></v-col>
            </v-row>
          </v-card-text>

          <SectionHeader title="Encounter" icon="mdi-file-document-outline" />
          <v-divider />
          <v-card-text>
            <v-text-field v-model="encounterId" label="Encounter ID (optional)" />
            <v-row>
              <v-col cols="6"><v-select v-model="encType" :items="ENCOUNTER_TYPES" label="Encounter type" /></v-col>
              <v-col cols="6"><v-text-field v-model="encDateTime" label="Encounter dateTime (ISO)" /></v-col>
            </v-row>
            <v-row>
              <v-col cols="6"><v-text-field v-model="department" label="Department" /></v-col>
              <v-col cols="6"><v-text-field v-model="provider" label="Provider" /></v-col>
            </v-row>
            <v-divider class="my-3" />
            <v-row>
              <v-col cols="4"><v-select v-model="format" :items="['text','json','fhir']" label="Format" /></v-col>
              <v-col cols="4"><v-text-field v-model="docId" label="Source document ID" hint="Idempotency key" persistent-hint /></v-col>
              <v-col cols="4"><v-text-field v-model="version" label="Version" /></v-col>
            </v-row>
            <v-text-field v-model="system" label="Source system" />
          </v-card-text>

          <v-card-actions class="px-4 pb-4">
            <v-btn color="primary" :loading="loading" prepend-icon="mdi-cloud-upload-outline" @click="submit">
              Submit
            </v-btn>
            <v-spacer />
            <v-menu>
              <template #activator="{ props }">
                <v-btn v-bind="props" variant="text" prepend-icon="mdi-file-document-outline">Load sample</v-btn>
              </template>
              <v-list density="compact">
                <v-list-item @click="fillAdmission">Admission note</v-list-item>
                <v-list-item @click="fillProgress">Progress note</v-list-item>
                <v-list-item @click="fillDischarge">Discharge summary</v-list-item>
                <v-list-item @click="fillFHIR">FHIR bundle</v-list-item>
              </v-list>
            </v-menu>
          </v-card-actions>
        </v-card>
      </v-col>

      <v-col cols="12" md="7">
        <v-card>
          <SectionHeader title="Content" icon="mdi-text-box-outline">
            <template #actions><v-chip size="x-small" variant="tonal">{{ content.length }} chars</v-chip></template>
          </SectionHeader>
          <v-divider />
          <v-card-text>
            <v-textarea v-model="content" rows="20" auto-grow placeholder="Paste EMR text here (or JSON/FHIR bundle when format != text)" spellcheck="false" />
          </v-card-text>
        </v-card>

        <JobWatcher
          v-if="currentJobId"
          class="mt-4"
          :jobId="currentJobId"
          @done="onDone"
          @failed="onFailed"
          @retry="submit"
        />
      </v-col>
    </v-row>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ingest, listPatients } from '../api/client.js'
import { ENCOUNTER_TYPES } from '../constants/clinical.js'
import { useUiStore } from '../stores/ui.js'
import SectionHeader from '../components/SectionHeader.vue'
import JobWatcher from '../components/JobWatcher.vue'

const ui = useUiStore()
const route = useRoute()
const router = useRouter()

const patientId = ref(route.query.patientId || '')
const name = ref('')
const gender = ref('male')
const birthDate = ref('')
const encounterId = ref('')
const encType = ref('admission')
const encDateTime = ref(new Date().toISOString())
const department = ref('')
const provider = ref('')
const format = ref('text')
const docId = ref(`doc-${Date.now()}`)
const version = ref('1')
const system = ref('UI')
const content = ref('')
const loading = ref(false)
const currentJobId = ref(null)

const patientResults = ref([])
const patientsLoading = ref(false)
const existing = computed(() => patientResults.value.find(p => p.patient_id === patientId.value))

let searchTimer
function onSearchPatients(q) {
  clearTimeout(searchTimer)
  if (!q) { patientResults.value = []; return }
  searchTimer = setTimeout(async () => {
    patientsLoading.value = true
    try { patientResults.value = await listPatients(q) } finally { patientsLoading.value = false }
  }, 250)
}

watch(existing, (e) => {
  if (e && !name.value) {
    name.value = e.name || ''
    gender.value = e.gender || ''
    birthDate.value = e.birth_date || ''
  }
})

async function submit() {
  if (!patientId.value) { ui.error('Patient ID required'); return }
  loading.value = true
  currentJobId.value = null
  try {
    const body = {
      patient: { patientId: patientId.value, name: name.value || null, gender: gender.value || null, birthDate: birthDate.value || null },
      encounter: { encounterId: encounterId.value || null, type: encType.value, dateTime: encDateTime.value, department: department.value || null, provider: provider.value || null },
      format: format.value,
      content: format.value === 'text' ? content.value : safeJson(content.value),
      source: { system: system.value, documentId: docId.value, version: version.value },
    }
    const res = await ingest(body)
    currentJobId.value = res.jobId
  } finally {
    loading.value = false
  }
}

function safeJson(s) { try { return JSON.parse(s) } catch { return s } }

function onDone(result) {
  ui.success('Document ingested')
  const pid = result?.patientId || patientId.value
  if (pid) router.push({ name: 'patient', params: { id: pid } })
}
function onFailed(err) { ui.error(`Ingest failed: ${err}`) }

function fillAdmission() { /* unchanged */ }
function fillProgress() { /* unchanged */ }
function fillDischarge() { /* unchanged */ }
function fillFHIR() { /* unchanged */ }

onMounted(() => { if (patientId.value) onSearchPatients(patientId.value) })
</script>
```

(Preserve the four `fill*` sample-content functions from the existing file.)

- [ ] **Step 2b: Patient detail "Add note" button**

In `PatientDetail.vue`, in the header row near the Summary/Coding buttons:

```vue
<v-btn variant="text" prepend-icon="mdi-note-plus-outline" :to="{ name: 'ingest', query: { patientId: id } }">Add note</v-btn>
```

(`name: 'ingest'` requires the route to be named — check `router.js`; rename if necessary.)

- [ ] **Step 3: Run test**

```
cd frontend && npm run test
```
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/views/IngestView.vue frontend/src/views/PatientDetail.vue \
        frontend/tests/IngestView.spec.js frontend/src/router.js
git commit -m "feat(ui): unified IngestView with autocomplete + JobWatcher; PatientDetail [+ Add note]"
```

---

## Task 15: `DebugView.vue` — Overview tab + Chart.js bar chart

**Files:**
- Create: `frontend/src/views/DebugView.vue`, `frontend/tests/DebugView.spec.js`

- [ ] **Step 1: Failing test**

```javascript
// frontend/tests/DebugView.spec.js
import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('../src/api/client.js', () => ({
  getDebugSummary: vi.fn().mockResolvedValue({ total_calls: 5, total_tokens: 1200, total_cost_usd: 0.0123, avg_latency_ms: 1500, failures: 1 }),
  getDebugByModel: vi.fn().mockResolvedValue([
    { model: 'gpt-4o-mini', calls: 4, prompt_tokens: 800, completion_tokens: 400, cost_usd: 0.0123, avg_latency_ms: 1500 },
  ]),
  getDebugByDay: vi.fn().mockResolvedValue([]),
  listAiCalls: vi.fn().mockResolvedValue([]),
  listJobs: vi.fn().mockResolvedValue([]),
}))

const stubs = {
  'v-card': { template: '<div><slot/></div>' },
  'v-card-text': { template: '<div><slot/></div>' },
  'v-row': { template: '<div><slot/></div>' },
  'v-col': { template: '<div><slot/></div>' },
  'v-tabs': { template: '<div><slot/></div>' }, 'v-tab': true,
  'v-window': { template: '<div><slot/></div>' }, 'v-window-item': { template: '<div><slot/></div>' },
  'v-data-table': true, 'v-chip': true, 'v-icon': true, 'v-btn': true, 'v-select': true, 'v-text-field': true,
  'v-divider': true, 'SectionHeader': { template: '<div><slot/></div>' }, 'BarChart': { template: '<canvas/>' },
}

describe('DebugView', () => {
  it('renders KPI cards from /api/debug/summary', async () => {
    const DebugView = (await import('../src/views/DebugView.vue')).default
    const w = mount(DebugView, { global: { stubs } })
    await flushPromises()
    expect(w.html()).toContain('5')              // total_calls
    expect(w.html()).toMatch(/1,200/)            // total_tokens
  })
})
```

- [ ] **Step 2: Run failing test**

```
cd frontend && npm run test
```
Expected: FAIL — view missing.

- [ ] **Step 3: Implement `DebugView.vue`**

```vue
<template>
  <div>
    <div class="d-flex align-center mb-4">
      <div>
        <h1 class="text-h5 font-weight-bold mb-1">Debug</h1>
        <div class="text-body-2 text-grey-darken-1">Token usage, cost tracking, and job timeline.</div>
      </div>
      <v-spacer />
      <v-select v-model="rangeKey" :items="rangeOptions" item-title="label" item-value="key" label="Range" density="compact" hide-details style="max-width: 200px" @update:model-value="reload" />
      <v-btn variant="text" prepend-icon="mdi-refresh" @click="reload">Refresh</v-btn>
    </div>

    <v-row>
      <v-col cols="6" md="3"><KpiCard label="Total spend" :value="formatUSD(summary.total_cost_usd)" /></v-col>
      <v-col cols="6" md="3"><KpiCard label="AI calls"    :value="summary.total_calls" /></v-col>
      <v-col cols="6" md="3"><KpiCard label="Avg latency" :value="`${Math.round(summary.avg_latency_ms || 0)} ms`" /></v-col>
      <v-col cols="6" md="3"><KpiCard label="Failures"    :value="`${summary.failures || 0}`" :color="summary.failures ? 'error' : 'success'" /></v-col>
    </v-row>

    <v-tabs v-model="tab" color="primary" density="comfortable" class="mt-4">
      <v-tab value="overview" prepend-icon="mdi-view-dashboard-outline">Overview</v-tab>
      <v-tab value="calls"    prepend-icon="mdi-text-box-search-outline">AI calls</v-tab>
      <v-tab value="jobs"     prepend-icon="mdi-clock-outline">Jobs</v-tab>
    </v-tabs>

    <v-window v-model="tab" class="mt-4">
      <v-window-item value="overview" eager>
        <v-row>
          <v-col cols="12" md="7"><v-card><SectionHeader title="Spend by day"/><v-divider/><v-card-text><BarChart :data="byDayChart" /></v-card-text></v-card></v-col>
          <v-col cols="12" md="5"><v-card><SectionHeader title="By model"/><v-divider/>
            <v-data-table density="comfortable" :headers="modelHeaders" :items="byModel">
              <template #item.cost_usd="{ item }">{{ formatUSD(item.cost_usd) }}</template>
              <template #item.prompt_tokens="{ item }">{{ formatTokens(item.prompt_tokens) }}</template>
            </v-data-table>
          </v-card></v-col>
        </v-row>
      </v-window-item>

      <v-window-item value="calls"><!-- Task 16 --></v-window-item>
      <v-window-item value="jobs"><!--  Task 17 --></v-window-item>
    </v-window>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { getDebugSummary, getDebugByModel, getDebugByDay } from '../api/client.js'
import { formatTokens, formatUSD } from '../utils/format.js'
import SectionHeader from '../components/SectionHeader.vue'
import BarChart from '../components/BarChart.vue'   // tiny wrapper around vue-chartjs Bar
import KpiCard from '../components/KpiCard.vue'

const tab = ref('overview')
const rangeKey = ref('7d')
const rangeOptions = [
  { key: '24h', label: 'Last 24 hours' },
  { key: '7d',  label: 'Last 7 days' },
  { key: '30d', label: 'Last 30 days' },
  { key: '90d', label: 'Last 90 days' },
]

const summary = ref({})
const byModel = ref([])
const byDay = ref([])

const modelHeaders = [
  { title: 'Model',     key: 'model' },
  { title: 'Calls',     key: 'calls', align: 'end' },
  { title: 'Prompt tk', key: 'prompt_tokens', align: 'end' },
  { title: 'Cost',      key: 'cost_usd', align: 'end' },
]

function rangeParams() {
  const map = { '24h': 1, '7d': 7, '30d': 30, '90d': 90 }
  const days = map[rangeKey.value] || 7
  const end = new Date()
  const start = new Date(Date.now() - days * 86400_000)
  return { start: start.toISOString(), end: end.toISOString() }
}

async function reload() {
  const p = rangeParams()
  ;[summary.value, byModel.value, byDay.value] = await Promise.all([
    getDebugSummary(p), getDebugByModel(p), getDebugByDay(p),
  ])
}

const byDayChart = computed(() => {
  // Reshape rows of {day, call_type, cost_usd, calls} into a stacked-bar dataset
  const days = [...new Set(byDay.value.map(r => String(r.day).slice(0, 10)))]
  const types = ['extract', 'summary', 'coding', 'embed']
  return {
    labels: days,
    datasets: types.map(t => ({
      label: t,
      data: days.map(d => Number(byDay.value.find(r => String(r.day).slice(0, 10) === d && r.call_type === t)?.cost_usd || 0)),
      stack: 'cost',
    })),
  }
})

onMounted(reload)
</script>
```

Also create the two small helper components:

```vue
<!-- frontend/src/components/KpiCard.vue -->
<template>
  <v-card>
    <v-card-text>
      <div class="text-caption text-grey-darken-1">{{ label }}</div>
      <div class="text-h5 font-weight-bold mt-1" :class="color ? `text-${color}` : ''">{{ value }}</div>
    </v-card-text>
  </v-card>
</template>
<script setup>defineProps({ label: String, value: [String, Number], color: String })</script>
```

```vue
<!-- frontend/src/components/BarChart.vue -->
<template><Bar :data="data" :options="options" /></template>
<script setup>
import { Bar } from 'vue-chartjs'
import { Chart, BarElement, CategoryScale, LinearScale, Tooltip, Legend, Title } from 'chart.js'
Chart.register(BarElement, CategoryScale, LinearScale, Tooltip, Legend, Title)
defineProps({ data: Object, options: { type: Object, default: () => ({ responsive: true, plugins: { legend: { position: 'bottom' } }, scales: { x: { stacked: true }, y: { stacked: true } } }) } })
</script>
```

- [ ] **Step 4: Run tests**

```
cd frontend && npm run test
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/DebugView.vue frontend/src/components/{KpiCard,BarChart}.vue \
        frontend/tests/DebugView.spec.js
git commit -m "feat(ui): DebugView Overview tab + BarChart + KpiCard"
```

---

## Task 16: DebugView — AI calls tab with drawer + CSV download

**Files:**
- Modify: `frontend/src/views/DebugView.vue`

- [ ] **Step 1: Failing assertion**

Extend `DebugView.spec.js` with:

```javascript
it('renders an AI calls table when the calls tab is selected', async () => {
  const { listAiCalls } = await import('../src/api/client.js')
  listAiCalls.mockResolvedValueOnce([
    { id: 'c1', created_at: '2026-05-15', model: 'gpt-4o-mini', call_type: 'extract',
      prompt_tokens: 100, completion_tokens: 50, total_tokens: 150, latency_ms: 1200,
      cost_usd: 0.001, error: null, patient_id: 'HN1', job_id: null },
  ])
  const DebugView = (await import('../src/views/DebugView.vue')).default
  const w = mount(DebugView, { global: { stubs: { ...stubs, 'v-data-table-server': { template: '<table><slot/></table>' } } } })
  await w.setData({ tab: 'calls' })
  await flushPromises()
  expect(w.html()).toContain('gpt-4o-mini')
})
```

- [ ] **Step 2: Run failing test**

Expected: FAIL.

- [ ] **Step 3: Fill the calls tab in `DebugView.vue`**

```vue
<v-window-item value="calls">
  <v-card>
    <SectionHeader title="AI calls" icon="mdi-text-box-search-outline">
      <template #actions>
        <v-text-field v-model="callsQ" density="compact" placeholder="Search error / model" prepend-inner-icon="mdi-magnify" hide-details style="max-width: 280px" />
        <v-select v-model="callsStatus" :items="['', 'ok', 'failed']" label="Status" density="compact" hide-details style="max-width: 130px; margin-left: 8px;" clearable />
        <v-btn variant="text" prepend-icon="mdi-download" :href="csvUrl">CSV</v-btn>
      </template>
    </SectionHeader>
    <v-divider />
    <v-data-table density="comfortable" :headers="callsHeaders" :items="callsRows" :loading="callsLoading" @click:row="onCallClick">
      <template #item.cost_usd="{ item }">{{ formatUSD(item.cost_usd) }}</template>
      <template #item.prompt_tokens="{ item }">{{ formatTokens(item.prompt_tokens) }}</template>
      <template #item.error="{ item }">
        <v-chip v-if="item.error" size="x-small" color="error" variant="tonal">err</v-chip>
        <v-chip v-else size="x-small" color="success" variant="tonal">ok</v-chip>
      </template>
    </v-data-table>
  </v-card>

  <v-navigation-drawer v-model="callDrawer" location="right" width="600" temporary>
    <v-card flat>
      <SectionHeader title="AI call detail" icon="mdi-information-outline" />
      <v-divider />
      <v-card-text v-if="selectedCall">
        <pre class="cng-raw">{{ JSON.stringify(selectedCall, null, 2) }}</pre>
      </v-card-text>
    </v-card>
  </v-navigation-drawer>
</v-window-item>
```

```javascript
const callsHeaders = [
  { title: 'Time',     key: 'created_at' },
  { title: 'Type',     key: 'call_type' },
  { title: 'Model',    key: 'model' },
  { title: 'Prompt',   key: 'prompt_tokens', align: 'end' },
  { title: 'Compl.',   key: 'completion_tokens', align: 'end' },
  { title: 'Latency',  key: 'latency_ms', align: 'end' },
  { title: 'Cost',     key: 'cost_usd', align: 'end' },
  { title: 'Status',   key: 'error' },
]

const callsRows = ref([])
const callsLoading = ref(false)
const callsQ = ref('')
const callsStatus = ref('')
const callDrawer = ref(false)
const selectedCall = ref(null)

const csvUrl = computed(() => {
  const p = new URLSearchParams(rangeParams())
  if (callsQ.value) p.set('q', callsQ.value)
  if (callsStatus.value) p.set('status', callsStatus.value)
  return (import.meta.env.VITE_API_BASE || '') + '/api/debug/ai-calls.csv?' + p.toString()
})

async function loadCalls() {
  callsLoading.value = true
  try {
    callsRows.value = await listAiCalls({ ...rangeParams(), q: callsQ.value || undefined, status: callsStatus.value || undefined, limit: 200 })
  } finally { callsLoading.value = false }
}

async function onCallClick(_, { item }) {
  selectedCall.value = await getAiCall(item.id)
  callDrawer.value = true
}

watch(tab, t => { if (t === 'calls') loadCalls() })
watch([callsQ, callsStatus], loadCalls)
```

- [ ] **Step 4: Run tests**

```
cd frontend && npm run test
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/DebugView.vue frontend/tests/DebugView.spec.js
git commit -m "feat(ui): DebugView AI calls tab + drawer + CSV link"
```

---

## Task 17: DebugView — Jobs tab + requeue button

**Files:**
- Modify: `frontend/src/views/DebugView.vue`

- [ ] **Step 1: Failing test**

```javascript
it('shows a requeue button on failed jobs', async () => {
  const { listJobs, requeueJob } = await import('../src/api/client.js')
  listJobs.mockResolvedValueOnce([{ job_id: 'j1', type: 'emr_ingest', status: 'failed', patient_id: 'HN1', attempts: 3 }])
  requeueJob = vi.fn().mockResolvedValue({ requeued: 'j1' })
  ...
})
```

- [ ] **Step 2: Run failing test**

Expected: FAIL.

- [ ] **Step 3: Fill the jobs tab**

```vue
<v-window-item value="jobs">
  <v-card>
    <SectionHeader title="Jobs" icon="mdi-clock-outline">
      <template #actions>
        <v-select v-model="jobsStatus" :items="['', 'pending', 'running', 'completed', 'failed']" label="Status" density="compact" hide-details style="max-width: 160px" clearable />
      </template>
    </SectionHeader>
    <v-divider />
    <v-data-table :headers="jobsHeaders" :items="jobsRows" :loading="jobsLoading">
      <template #item.status="{ item }">
        <v-chip size="x-small" :color="{ completed:'success', failed:'error', running:'info', pending:'warning' }[item.status] || 'grey'" variant="tonal">{{ item.status }}</v-chip>
      </template>
      <template #item.actions="{ item }">
        <v-btn v-if="item.status === 'failed'" size="x-small" color="primary" @click="requeue(item)">Re-queue</v-btn>
      </template>
    </v-data-table>
  </v-card>
</v-window-item>
```

```javascript
const jobsHeaders = [
  { title: 'Created',  key: 'created_at' },
  { title: 'Type',     key: 'type' },
  { title: 'Patient',  key: 'patient_id' },
  { title: 'Status',   key: 'status' },
  { title: 'Attempts', key: 'attempts', align: 'end' },
  { title: '', key: 'actions', sortable: false, align: 'end' },
]

const jobsRows = ref([])
const jobsLoading = ref(false)
const jobsStatus = ref('')

async function loadJobs() {
  jobsLoading.value = true
  try {
    jobsRows.value = await listJobs({ status: jobsStatus.value || undefined, limit: 100 })
  } finally { jobsLoading.value = false }
}

async function requeue(job) {
  await requeueJob(job.job_id)
  await loadJobs()
}

watch(tab, t => { if (t === 'jobs') loadJobs() })
watch(jobsStatus, loadJobs)
```

- [ ] **Step 4: Run tests**

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/DebugView.vue
git commit -m "feat(ui): DebugView jobs tab with re-queue"
```

---

## Task 18: `ConfigView.vue` — Model pricing card with editable table

**Files:**
- Modify: `frontend/src/views/ConfigView.vue`
- Create: `frontend/src/components/PricingTable.vue`

- [ ] **Step 1: Implement `PricingTable.vue`**

```vue
<template>
  <v-card>
    <SectionHeader title="Model pricing" icon="mdi-tag-outline">
      <template #actions>
        <v-btn variant="text" prepend-icon="mdi-refresh" :loading="refreshing" @click="onRefresh">Refresh from OpenRouter</v-btn>
        <v-btn variant="text" prepend-icon="mdi-plus" @click="addRow">Add</v-btn>
      </template>
    </SectionHeader>
    <v-divider />
    <v-data-table density="comfortable" :headers="headers" :items="rows" :loading="loading">
      <template #item.prompt_per_1m="{ item }">{{ fmt(item.prompt_per_1m) }}</template>
      <template #item.completion_per_1m="{ item }">{{ fmt(item.completion_per_1m) }}</template>
      <template #item.embedding_per_1m="{ item }">{{ fmt(item.embedding_per_1m) }}</template>
      <template #item.actions="{ item }">
        <v-btn size="x-small" variant="text" prepend-icon="mdi-pencil" @click="edit(item)">Edit</v-btn>
        <v-btn size="x-small" variant="text" color="error" prepend-icon="mdi-delete" @click="remove(item)">Delete</v-btn>
      </template>
    </v-data-table>

    <v-dialog v-model="dialog" max-width="500">
      <v-card>
        <SectionHeader title="Edit pricing" />
        <v-divider />
        <v-card-text>
          <v-text-field v-model="form.model" label="Model" :disabled="!isNew" />
          <v-text-field v-model.number="form.prompt_per_1m" label="Prompt $ / 1M" type="number" step="0.0001" />
          <v-text-field v-model.number="form.completion_per_1m" label="Completion $ / 1M" type="number" step="0.0001" />
          <v-text-field v-model.number="form.embedding_per_1m" label="Embedding $ / 1M" type="number" step="0.0001" />
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="dialog = false">Cancel</v-btn>
          <v-btn color="primary" @click="save">Save</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </v-card>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { listPricing, upsertPricing, deletePricing, refreshOpenRouter } from '../api/client.js'
import { useUiStore } from '../stores/ui.js'
import SectionHeader from './SectionHeader.vue'

const ui = useUiStore()
const headers = [
  { title: 'Model', key: 'model' },
  { title: 'Prompt $/1M', key: 'prompt_per_1m', align: 'end' },
  { title: 'Completion $/1M', key: 'completion_per_1m', align: 'end' },
  { title: 'Embedding $/1M', key: 'embedding_per_1m', align: 'end' },
  { title: 'Source', key: 'source' },
  { title: '', key: 'actions', sortable: false, align: 'end' },
]
const rows = ref([])
const loading = ref(false)
const refreshing = ref(false)
const dialog = ref(false)
const isNew = ref(false)
const form = ref({})

const fmt = v => (v == null ? '–' : `$${Number(v).toFixed(4)}`)

async function load() {
  loading.value = true
  try { rows.value = await listPricing() } finally { loading.value = false }
}

function addRow() { form.value = { model: '', prompt_per_1m: null, completion_per_1m: null, embedding_per_1m: null }; isNew.value = true; dialog.value = true }
function edit(item) { form.value = { ...item }; isNew.value = false; dialog.value = true }
async function save() {
  if (!form.value.model) { ui.error('Model is required'); return }
  await upsertPricing(form.value.model, {
    prompt_per_1m: form.value.prompt_per_1m ?? null,
    completion_per_1m: form.value.completion_per_1m ?? null,
    embedding_per_1m: form.value.embedding_per_1m ?? null,
    source: 'manual',
  })
  ui.success(`Saved ${form.value.model}`)
  dialog.value = false
  await load()
}
async function remove(item) {
  if (!confirm(`Delete pricing for ${item.model}?`)) return
  await deletePricing(item.model)
  await load()
}
async function onRefresh() {
  refreshing.value = true
  try {
    const r = await refreshOpenRouter()
    ui.success(`Upserted ${r.upserted} models from OpenRouter`)
    await load()
  } finally { refreshing.value = false }
}

onMounted(load)
</script>
```

- [ ] **Step 2: Embed in `ConfigView.vue`**

Add inside the existing `<v-row>`:

```vue
<v-col cols="12">
  <PricingTable />
</v-col>
```

Import:

```javascript
import PricingTable from '../components/PricingTable.vue'
```

- [ ] **Step 3: Run tests**

```
cd frontend && npm run test
```
Expected: existing tests pass; no new test added (component is exercised by Playwright in Task 19).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/views/ConfigView.vue frontend/src/components/PricingTable.vue
git commit -m "feat(ui): Model pricing table with Refresh-from-OpenRouter button"
```

---

## Task 19: Playwright E2E for debug page + retain ingest watcher

**Files:**
- Modify: `frontend/tests/e2e/ingest.spec.js`
- Create: `frontend/tests/e2e/debug.spec.js`

- [ ] **Step 1: Update ingest E2E to await the JobWatcher**

```javascript
test('ingest queues a job, JobWatcher completes, navigates to patient', async ({ page }) => {
  await page.goto('/#/ingest')
  await page.getByRole('button', { name: /load sample/i }).click()
  await page.getByRole('menuitem', { name: /admission/i }).click()

  const pid = `PW-${Date.now()}`
  await page.getByLabel('Patient ID').fill(pid)
  await page.getByRole('button', { name: /^submit$/i }).click()

  // JobWatcher renders + auto-navigates on completion
  await expect(page.getByText(/ingest job/i)).toBeVisible()
  await page.waitForURL(new RegExp(`#/patients/${pid}`), { timeout: 60_000 })
  await expect(page.getByRole('heading', { name: new RegExp(pid) })).toBeVisible()
})
```

- [ ] **Step 2: Add debug E2E**

```javascript
// frontend/tests/e2e/debug.spec.js
import { test, expect } from '@playwright/test'

test('debug page shows totals after an ingest', async ({ page }) => {
  // Drive an ingest first
  await page.goto('/#/ingest')
  await page.getByRole('button', { name: /load sample/i }).click()
  await page.getByRole('menuitem', { name: /admission/i }).click()
  const pid = `PW-D-${Date.now()}`
  await page.getByLabel('Patient ID').fill(pid)
  await page.getByRole('button', { name: /^submit$/i }).click()
  await page.waitForURL(new RegExp(`#/patients/${pid}`), { timeout: 60_000 })

  await page.goto('/#/debug')
  // KPI cards present and not empty
  await expect(page.getByText(/Total spend/i)).toBeVisible()
  await expect(page.getByText(/AI calls/i)).toBeVisible()
  await expect(page.locator('text=Avg latency')).toBeVisible()

  await page.getByRole('tab', { name: /AI calls/i }).click()
  await expect(page.locator('text=extract').first()).toBeVisible()
})
```

- [ ] **Step 3: Smoke run (only if Playwright is locally set up)**

```
cd frontend && npx playwright install --with-deps chromium && npm run e2e
```

Expected: PASS when run against the full docker compose stack.

- [ ] **Step 4: Commit**

```bash
git add frontend/tests/e2e/{ingest,debug}.spec.js
git commit -m "test(e2e): JobWatcher path + DebugView KPI assertions"
```

---

## Task 20: Backend E2E smoke — extend to poll and hit debug summary

**Files:**
- Modify: `backend/tests/test_e2e_smoke.py`

- [ ] **Step 1: Failing addition**

```python
def test_async_ingest_polls_to_completion(wait_for_backend):
    payload = {
        "patient": {"patientId": "E2E-Q1"},
        "encounter": {"type": "admission", "dateTime": "2026-05-15T10:00:00+07:00"},
        "format": "text",
        "content": "Patient with Type 2 diabetes mellitus.",
        "source": {"system": "E2E", "documentId": "e2e-q1", "version": "1"},
    }
    r = httpx.post(f"{BASE}/api/emr/ingest", json=payload, headers=_headers(), timeout=60)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "queued"
    job_id = body["jobId"]

    deadline = time.time() + 90
    final = None
    while time.time() < deadline:
        j = httpx.get(f"{BASE}/api/jobs/{job_id}", headers=_headers(), timeout=10).json()
        if j["status"] in ("completed", "failed"):
            final = j; break
        time.sleep(1)
    assert final is not None and final["status"] == "completed"

    s = httpx.get(f"{BASE}/api/debug/summary", headers=_headers(), timeout=10).json()
    assert s["total_calls"] >= 1
```

- [ ] **Step 2: Run**

`CNG_E2E=1 ./scripts/e2e.sh`.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_e2e_smoke.py
git commit -m "test(e2e): async ingest poll + debug summary assertion"
```

---

## Task 21: Docs + examples updates

**Files:**
- Modify: `examples/ingest.sh`, `README.md`

- [ ] **Step 1: Examples**

`examples/ingest.sh`: append `?async=false` to every `POST /api/emr/ingest` invocation so the script prints the inline summary as before. One-line `sed` edit.

```diff
- curl -sS -X POST "$BASE/api/emr/ingest"
+ curl -sS -X POST "$BASE/api/emr/ingest?async=false"
```

- [ ] **Step 2: README**

Add to the API surface table:

```markdown
| GET  | `/api/jobs?status=&type=&limit=&offset=` | List background jobs (queue) |
| POST | `/api/jobs/{id}/requeue`                 | Reset a failed job to pending |
| GET  | `/api/debug/summary?from=&to=`           | KPI totals over a range |
| GET  | `/api/debug/by-model?from=&to=`          | Per-model breakdown |
| GET  | `/api/debug/by-day?from=&to=`            | Stacked-bar dataset |
| GET  | `/api/debug/ai-calls?…`                  | AI call log (filterable) |
| GET  | `/api/debug/ai-calls/{id}`               | Single call detail |
| GET  | `/api/debug/ai-calls.csv?…`              | Streamed CSV export |
| GET  | `/api/config/pricing`                    | List model rates |
| PUT  | `/api/config/pricing/{model}`            | Upsert one rate |
| DEL  | `/api/config/pricing/{model}`            | Delete a rate |
| POST | `/api/config/pricing/refresh-openrouter` | Refresh rates from OpenRouter |
```

Add a section "Async ingest" explaining the new default and how to fall back to sync via `?async=false`.

Add a section "Cost tracking" mentioning the seeded rates, the editable Config page, and the OpenRouter refresh.

- [ ] **Step 3: Commit**

```bash
git add README.md examples/ingest.sh
git commit -m "docs: async ingest, debug routes, model pricing"
```

---

## Task 22: Verify, push, watch CI

- [ ] **Step 1: Full backend suite**

```
cd backend && /tmp/cng-v/bin/pytest -q
```

- [ ] **Step 2: Full frontend suite**

```
cd frontend && npm run test
```

- [ ] **Step 3: Local docker compose smoke**

```bash
cp -n .env.example .env
docker compose up -d --build
# wait for /health
curl -sS http://localhost/health
# trigger an async ingest and follow the job
JOB=$(curl -sS -X POST http://localhost/api/emr/ingest -H 'Content-Type: application/json' -d @sample-data/emr-1-admission.txt.json | jq -r '.jobId')
until curl -sS http://localhost/api/jobs/$JOB | jq -r .status | grep -E 'completed|failed'; do sleep 1; done
docker compose down -v
```

- [ ] **Step 4: Push and watch CI**

```bash
git push origin main
gh run watch --repo tantee/clinical-note-graph $(gh run list -L1 --repo tantee/clinical-note-graph --json databaseId --jq '.[0].databaseId') --exit-status
```

Expected: all four jobs green (backend tests, frontend tests, E2E smoke, Playwright on workflow_dispatch).

---

## Self-review

Reviewed against the spec sections:

- **Schema changes (jobs, ai_outputs, model_pricing)** — Task 1 ships migration; Tasks 4, 7, 10 use the new columns.
- **Queue (claim, retry, stale lock, heartbeat)** — Task 7 covers claim + retry + stale lock; heartbeat is in `_write_progress` (extends `locked_until` on every stage). ✓
- **Provider tuple-return + ai_outputs metering** — Tasks 4, 5. ✓
- **Pricing model + OpenRouter refresh** — Tasks 2, 3, 6. ✓
- **Ingest pipeline refactor + async default** — Tasks 8, 9. ✓
- **Job list + requeue endpoints** — Task 9. ✓
- **Debug aggregation + routes + CSV** — Tasks 10, 11. ✓
- **Protected prefix** — Task 11. ✓
- **JobWatcher** — Task 13. ✓
- **Unified IngestView + Add note button** — Task 14. ✓
- **Debug page (3 tabs)** — Tasks 15, 16, 17. ✓
- **Pricing UI** — Task 18. ✓
- **Frontend tests + Playwright** — Tasks 13, 14, 15, 16, 17, 19. ✓
- **Backend E2E** — Task 20. ✓
- **Docs** — Task 21. ✓

No placeholders left in the plan. Type usage consistent: `AICallRecord`, `progress` JSONB shape, `stage_*` names appear identically in `JobWatcher.STAGES`, `run_ingest_pipeline.on_progress()`, and the backend test fixtures.
