-- backend/db/init/006_curated_facts.sql
-- Curated longitudinal layer for temporal problems & medications.
-- One reconciled row per distinct clinical item per patient. Human edits live
-- here and always win over AI. The append-only `facts` table stays the AI
-- evidence trail. Idempotent: safe to re-apply (matches the numbered-migration
-- pattern in 001..005). uuid_generate_v4() / pgcrypto already provisioned in 001.

CREATE TABLE IF NOT EXISTS curated_facts (
    id                    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    patient_id            TEXT NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
    type                  TEXT NOT NULL,                       -- 'condition' | 'medication'
    normalized_key        TEXT NOT NULL,                       -- identity key
    display_value         TEXT NOT NULL,
    normalized_code       TEXT,
    coding_system         TEXT,
    start_date            DATE,                                -- may be an estimate
    start_qualifier       TEXT NOT NULL DEFAULT 'unknown',     -- exact|estimated|before|unknown
    stop_date             DATE,
    stop_qualifier        TEXT NOT NULL DEFAULT 'unknown',     -- exact|estimated|ongoing|unknown
    start_text            TEXT,                                -- original phrase ("4 months ago")
    stop_text             TEXT,
    schedule_text         TEXT,                                -- free text ("q3wk x 6 cycles")
    status                TEXT,                                -- clinical status (problems) / action (meds)
    record_state          TEXT NOT NULL DEFAULT 'active',      -- active | dismissed (soft-delete)
    review_status         TEXT NOT NULL DEFAULT 'ai_suggested',-- ai_suggested | human_confirmed
    origin                TEXT NOT NULL DEFAULT 'ai',          -- ai | human
    human_edited_fields   JSONB NOT NULL DEFAULT '[]'::jsonb,  -- column names the human overrode
    last_evidence_fact_id UUID,                                -- most recent facts.id that fed this row (soft ref, no FK: facts are append-only)
    updated_by            TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS curated_facts_identity_idx
    ON curated_facts (patient_id, type, normalized_key);

CREATE INDEX IF NOT EXISTS curated_facts_patient_idx
    ON curated_facts (patient_id, type, record_state);
