"""Startup SQL migration runner.

`docker-entrypoint-initdb.d` only runs on a *fresh* Postgres volume, so numbered
migrations added after a DB was first created never reach it (the cause of the
'relation "curated_facts" does not exist' error). The runner replays every
db/init/*.sql on startup, idempotently, so existing databases catch up.
"""
from contextlib import contextmanager

from app.db import migrations


def test_migration_files_sorted_and_complete():
    names = [f.name for f in migrations._migration_files()]
    assert names == sorted(names), "migrations must run in lexical (numbered) order"
    assert "006_curated_facts.sql" in names
    assert "007_curated_aliases.sql" in names
    assert names[0].startswith("001_")


def test_run_migrations_executes_every_file_in_order(monkeypatch):
    executed: list[str] = []

    class _FakeConn:
        def exec_driver_sql(self, sql):
            executed.append(sql)

    class _FakeEngine:
        @contextmanager
        def begin(self):
            yield _FakeConn()

    monkeypatch.setattr(migrations, "get_engine", lambda: _FakeEngine())
    applied = migrations.run_migrations()

    assert applied == [f.name for f in migrations._migration_files()]
    # The curated_facts DDL actually reached the connection.
    assert any("CREATE TABLE IF NOT EXISTS curated_facts" in s for s in executed)
    assert any("ADD COLUMN IF NOT EXISTS aliases" in s for s in executed)


def test_run_migrations_continues_when_one_file_fails(monkeypatch):
    """A failing migration is logged and does not abort the rest (best-effort,
    matching the resilient-startup pattern)."""
    seen: list[str] = []

    class _FakeConn:
        def __init__(self, name):
            self.name = name

        def exec_driver_sql(self, sql):
            seen.append(self.name)
            if "002_" in self.name:
                raise RuntimeError("boom")

    class _FakeEngine:
        def __init__(self):
            self._i = 0

        @contextmanager
        def begin(self):
            files = migrations._migration_files()
            conn = _FakeConn(files[self._i].name)
            self._i += 1
            yield conn

    monkeypatch.setattr(migrations, "get_engine", lambda: _FakeEngine())
    applied = migrations.run_migrations()

    assert "002_async_and_debug.sql" not in applied   # failed, excluded from applied
    assert "001_schema.sql" in applied
    assert "006_curated_facts.sql" in applied          # later files still ran
