"""Shared fixtures.

The DB-touching tests run against in-memory fakes so the suite runs without
docker. Tests that need a real Postgres/Neo4j (marked `e2e`) skip by default
and only run when CNG_E2E=1.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections import defaultdict
from typing import Any, Iterable

import pytest


# ---------------------------------------------------------------------------
# Postgres fake — captures everything written so tests can assert on it.
# ---------------------------------------------------------------------------


class FakeResult:
    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class FakeSession:
    def __init__(self, store: "FakeStore"):
        self.store = store
        self.committed = False
        self.rolled_back = False

    def execute(self, stmt, params=None):
        sql = str(stmt).strip()
        params = params or {}
        return self.store.execute(sql, params)

    def commit(self): self.committed = True
    def rollback(self): self.rolled_back = True
    def close(self): pass


class FakeStore:
    """Minimal SQL-ish dispatcher matching the actual statements the app issues."""

    def __init__(self):
        self.patients: dict[str, dict] = {}
        self.encounters: dict[str, dict] = {}
        self.documents: dict[str, dict] = {}
        self.facts: list[dict] = []
        self.ai_outputs: list[dict] = []
        self.audit_log: list[dict] = []
        self.jobs: dict[str, dict] = {}
        self.config: dict[str, Any] = {}
        self.profiles: dict[str, dict] = {
            "default-summary": {
                "profile_id": "default-summary",
                "name": "Default Summary",
                "config": {"fields": ["problems", "medications"], "format": "json", "includeEvidence": True},
            },
        }
        self.embeddings: list[dict] = []
        self.pricing: dict[str, dict] = {}

    def execute(self, sql: str, params: dict[str, Any]) -> FakeResult:
        s = sql.lower()
        if s.startswith("insert into patients"):
            pid = params["patient_id"]
            existing = self.patients.get(pid, {})
            self.patients[pid] = {
                "patient_id": pid,
                "name": params.get("name") or existing.get("name"),
                "gender": params.get("gender") or existing.get("gender"),
                "birth_date": params.get("birth_date") or existing.get("birth_date"),
                "metadata": json.loads(params.get("meta") or "{}"),
                "updated_at": "now",
            }
            return FakeResult([])
        if s.startswith("insert into encounters"):
            self.encounters[params["eid"]] = {
                "encounter_id": params["eid"],
                "patient_id": params["pid"],
                "type": params["type"],
                "date_time": params["dt"],
                "department": params.get("dept"),
                "provider": params.get("prov"),
            }
            return FakeResult([])
        if s.startswith("insert into documents"):
            self.documents[params["did"]] = {
                "document_id": params["did"], "patient_id": params["pid"],
                "encounter_id": params["eid"], "source_system": params.get("sys"),
                "source_document_id": params.get("sdid"), "version": params.get("ver"),
                "format": params.get("fmt"), "raw_content": params.get("raw"),
                "raw_json": json.loads(params["rj"]) if params.get("rj") else None,
                "received_at": "now",
            }
            return FakeResult([])
        if s.startswith("insert into facts"):
            # SQLAlchemy passes the same dict or a list. Our prod code passes a list.
            rows = params if isinstance(params, list) else [params]
            for p in rows:
                self.facts.append({
                    "id": f"fact-{len(self.facts)}",
                    **p,
                    "extra": json.loads(p["extra"]) if isinstance(p.get("extra"), str) else (p.get("extra") or {}),
                })
            return FakeResult([])
        if s.startswith("insert into ai_outputs"):
            self.ai_outputs.append({
                "id": f"ai-{len(self.ai_outputs)}",
                "document_id": params.get("d"), "patient_id": params.get("p"),
                "model": params.get("m"),
                "raw_output": json.loads(params["r"]) if isinstance(params.get("r"), str) else (params.get("r") or {}),
                "valid": params.get("v"),
                "validation_errors": json.loads(params["e"]) if isinstance(params.get("e"), str) else (params.get("e") or []),
                "job_id": params.get("job_id"),
                "call_type": params.get("call_type"),
                "prompt_tokens": params.get("prompt_tokens"),
                "completion_tokens": params.get("completion_tokens"),
                "total_tokens": params.get("total_tokens"),
                "latency_ms": params.get("latency_ms"),
                "cost_usd": params.get("cost_usd"),
                "error": params.get("err"),
                "created_at": "now",
            })
            return FakeResult([])
        if s.startswith("insert into audit_log"):
            self.audit_log.append({
                "actor": params["a"], "action": params["ac"],
                "target_type": params["tt"], "target_id": params["ti"],
                "payload": json.loads(params["p"]),
            })
            return FakeResult([])
        if s.startswith("insert into jobs"):
            self.jobs[params["jid"]] = {
                "job_id": params["jid"], "type": params["t"], "status": "pending",
                "patient_id": params.get("pid"), "document_id": params.get("did"),
                "payload": json.loads(params["p"]),
                "attempts": 0, "max_attempts": 3,
                "locked_by": None, "locked_until": None,
                "priority": 0, "next_run_at": "now",
                "progress": {},
            }
            return FakeResult([])
        if s.startswith("update jobs"):
            job_id = params.get("jid") or params.get("j")
            row = self.jobs.get(job_id)
            if row is None:
                return FakeResult([])
            if "set progress" in s:
                row["progress"] = json.loads(params["p"]) if isinstance(params.get("p"), str) else (params.get("p") or {})
                return FakeResult([])
            if "status='pending'" in s or "status = 'pending'" in s:
                # requeue path (Task 9)
                row["status"] = "pending"
                row["attempts"] = 0
                row["error"] = None
                row["locked_by"] = None
                row["locked_until"] = None
                return FakeResult([])
            if "set status" in s:
                row["status"] = params["st"]
                if params.get("res") is not None:
                    row["result"] = json.loads(params["res"])
                if params.get("err") is not None:
                    row["error"] = params["err"]
                if params.get("nxt") is not None:
                    row["next_run_at"] = params["nxt"]
                if "running" not in (params.get("st") or ""):
                    row["locked_by"] = None
                    row["locked_until"] = None
                return FakeResult([])
            return FakeResult([])
        if s.startswith("insert into app_config"):
            self.config[params["k"]] = json.loads(params["v"])
            return FakeResult([])
        if s.startswith("insert into export_profiles"):
            self.profiles[params["p"]] = {
                "profile_id": params["p"], "name": params["n"], "config": json.loads(params["c"]),
            }
            return FakeResult([])
        if s.startswith("delete from export_profiles"):
            self.profiles.pop(params["p"], None)
            return FakeResult([])
        if s.startswith("insert into embeddings"):
            rows = params if isinstance(params, list) else [params]
            for p in rows:
                self.embeddings.append({"patient_id": p["p"], "ref_type": p["rt"], "ref_id": p["ri"], "content": p["c"]})
            return FakeResult([])
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
            if "where model" in s and params.get("m") is not None:
                row = self.pricing.get(params["m"])
                return FakeResult([row] if row else [])
            return FakeResult(sorted(self.pricing.values(), key=lambda r: r["model"]))

        # SELECTs
        if "select" in s and " from patients" in s:
            if ":pid" in s and not "ilike" in s:
                pid = params.get("pid")
                row = self.patients.get(pid)
                return FakeResult([row] if row else [])
            if "ilike" in s:
                q = params.get("q", "").strip("%").lower()
                rows = [p for p in self.patients.values() if q in (p.get("name") or "").lower() or q in p["patient_id"].lower()]
                return FakeResult(rows)
            return FakeResult(list(self.patients.values()))
        if " from encounters " in s or " from encounters\n" in s:
            pid = params.get("pid")
            base = [e for e in self.encounters.values() if e["patient_id"] == pid]
            if "left join documents" in s:
                for e in base:
                    e["document_count"] = sum(1 for d in self.documents.values() if d["encounter_id"] == e["encounter_id"])
                    e["fact_count"] = sum(1 for f in self.facts if f.get("encounter_id") == e["encounter_id"])
            return FakeResult(base)
        if " from facts " in s or "from facts\n" in s:
            pid = params.get("pid")
            did = params.get("did")
            rows = [f for f in self.facts if f.get("patient_id") == pid and (did is None or f.get("document_id") == did)]
            return FakeResult(rows)
        if " from documents " in s or "from documents\n" in s:
            pid = params.get("pid")
            did = params.get("did")
            eid = params.get("eid")
            rows = [d for d in self.documents.values() if d["patient_id"] == pid]
            if did is not None:
                rows = [d for d in rows if d["document_id"] == did]
            if eid is not None:
                rows = [d for d in rows if d["encounter_id"] == eid]
            return FakeResult(rows)
        if " from ai_outputs " in s:
            did = params.get("did")
            rows = [a for a in self.ai_outputs if a["document_id"] == did]
            return FakeResult(rows[-1:] if rows else [])
        if " from jobs " in s:
            j = self.jobs.get(params.get("j"))
            return FakeResult([j] if j else [])
        if " from app_config" in s:
            return FakeResult([{"key": k, "value": v} for k, v in self.config.items()])
        if " from export_profiles" in s:
            return FakeResult(list(self.profiles.values()))
        if "select 1" in s:
            return FakeResult([{"?column?": 1}])

        return FakeResult([])


@pytest.fixture()
def fake_store(monkeypatch):
    store = FakeStore()

    from contextlib import contextmanager

    @contextmanager
    def _db_session():
        sess = FakeSession(store)
        try:
            yield sess
            sess.commit()
        except Exception:
            sess.rollback()
            raise

    import app.db.postgres as pg
    monkeypatch.setattr(pg, "db_session", _db_session)
    import app.services.ingest as ingest_mod
    monkeypatch.setattr(ingest_mod, "db_session", _db_session)
    import app.services.embeddings as emb_mod
    monkeypatch.setattr(emb_mod, "db_session", _db_session)
    import app.services.jobs as jobs_mod
    monkeypatch.setattr(jobs_mod, "db_session", _db_session)
    import app.services.patient_facts as pf_mod
    monkeypatch.setattr(pf_mod, "db_session", _db_session)
    import app.services.export as export_mod
    monkeypatch.setattr(export_mod, "db_session", _db_session)
    import app.services.runtime_config as rc_mod
    monkeypatch.setattr(rc_mod, "db_session", _db_session)
    import app.services.pricing as pricing_mod
    monkeypatch.setattr(pricing_mod, "db_session", _db_session)
    import app.routers.config as cfg_router
    monkeypatch.setattr(cfg_router, "db_session", _db_session)
    import app.routers.patient as p_router
    monkeypatch.setattr(p_router, "db_session", _db_session)
    import app.db.helpers as h_mod
    # audit() uses db_session-bound Session.execute via the passed sess, no monkeypatch needed.
    return store


@pytest.fixture()
def stub_neo4j(monkeypatch):
    """Stub all Neo4j calls so the ingest pipeline runs without a real database."""
    calls: list[tuple[str, dict]] = []

    def fake_run_cypher(q, params=None):
        calls.append((q.strip(), params or {}))
        return []

    class FakeSess:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def run(self, q, params=None):
            calls.append((q.strip(), params or {}))
            class _R:
                def data(self): return []
                def __iter__(self): return iter([])
            return _R()
        def close(self): pass

    from contextlib import contextmanager

    @contextmanager
    def fake_session():
        yield FakeSess()

    import app.db.neo4j_client as n4
    monkeypatch.setattr(n4, "run_cypher", fake_run_cypher)
    monkeypatch.setattr(n4, "neo4j_session", fake_session)
    import app.services.graph_updater as gu
    monkeypatch.setattr(gu, "neo4j_session", fake_session)
    monkeypatch.setattr(gu, "run_cypher", fake_run_cypher)
    monkeypatch.setattr(gu, "_CONSTRAINTS_READY", True, raising=False)
    return calls


@pytest.fixture()
def isolated_vault(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_PATH", str(tmp_path / "vault"))
    from app.config import get_settings
    get_settings.cache_clear()
    return tmp_path / "vault"


@pytest.fixture()
def app_client(fake_store, stub_neo4j, isolated_vault, monkeypatch):
    # Reset effective-settings cache so test env vars take effect cleanly.
    from app.services import runtime_config
    monkeypatch.setattr(runtime_config, "_OVERRIDES", {}, raising=False)
    monkeypatch.setattr(runtime_config, "_LOADED_AT", 0.0, raising=False)

    from fastapi.testclient import TestClient
    from app.main import create_app

    app = create_app()
    with TestClient(app) as client:
        yield client


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    from app.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def pytest_collection_modifyitems(config, items):
    if os.environ.get("CNG_E2E") == "1":
        return
    skip_e2e = pytest.mark.skip(reason="set CNG_E2E=1 to run E2E tests")
    for item in items:
        if "e2e" in item.keywords:
            item.add_marker(skip_e2e)
