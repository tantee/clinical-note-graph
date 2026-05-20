-- De-identification audit columns on ai_outputs.
--
-- `deidentified` records whether the outbound payload that produced this row
-- was passed through `Deidentifier.redact_*` before leaving the host.
-- `redaction_counts` is the per-category catch count (e.g.
-- {"PERSON": 2, "PHONE_NUMBER": 1, "DATE_TIME": 5}) so an auditor can see at
-- a glance what the redactor matched on this call.
--
-- `raw_response` in ai_outputs stores what actually left the host. With the
-- redactor active, that means the redacted text — which is exactly what we
-- want for audit purposes (we should be able to prove the redacted payload
-- is free of PHI).

ALTER TABLE ai_outputs
    ADD COLUMN IF NOT EXISTS deidentified BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS redaction_counts JSONB;

CREATE INDEX IF NOT EXISTS ai_outputs_deidentified_idx
    ON ai_outputs (deidentified, created_at DESC);
