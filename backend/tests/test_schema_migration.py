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
