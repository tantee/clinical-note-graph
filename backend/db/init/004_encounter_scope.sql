-- Encounter-scoped AI summaries + coding.
-- Adds a nullable encounter_id to patient_summaries so the same table holds
-- both patient-level rows (NULL) and encounter-level rows (NOT NULL).
-- IF NOT EXISTS so the startup migration runner can replay this safely against
-- a database that already has the column (idempotent like the other migrations).
ALTER TABLE patient_summaries
    ADD COLUMN IF NOT EXISTS encounter_id TEXT
    REFERENCES encounters(encounter_id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS patient_summaries_encounter_idx
    ON patient_summaries (encounter_id, kind, created_at DESC)
    WHERE encounter_id IS NOT NULL;
