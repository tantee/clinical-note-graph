from pathlib import Path

MIGRATION = Path(__file__).resolve().parents[1] / "db" / "init" / "006_curated_facts.sql"


def test_migration_file_exists():
    assert MIGRATION.is_file()


def test_migration_is_idempotent_shaped():
    """Every create statement must be guarded so re-applying the file is a no-op.

    A real double-apply needs a live Postgres; in unit context we assert the file
    only uses IF-NOT-EXISTS constructs and never bare CREATE/ALTER that would error
    on a second run."""
    sql = MIGRATION.read_text().lower()
    assert "create table if not exists curated_facts" in sql
    assert sql.count("create unique index if not exists") >= 1
    assert sql.count("create index if not exists") >= 1
    assert "create table curated_facts" not in sql
    assert "alter table curated_facts add column " not in sql


def test_migration_declares_identity_and_state_columns():
    sql = MIGRATION.read_text().lower()
    for col in (
        "normalized_key", "display_value", "start_date", "start_qualifier",
        "stop_date", "stop_qualifier", "schedule_text", "record_state",
        "review_status", "origin", "human_edited_fields", "last_evidence_fact_id",
    ):
        assert col in sql, f"missing column {col}"
