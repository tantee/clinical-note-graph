-- Per-patient AI summary and coding results, persisted so re-opening a
-- patient page is instant instead of paying another AI call. The most recent
-- row per (patient_id, kind, type) is what the UI loads on mount; older rows
-- stay for history / Debug-page cost comparison.

CREATE TABLE IF NOT EXISTS patient_summaries (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    patient_id TEXT NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('summary', 'coding')),
    type TEXT,                  -- 'detailed' / 'brief' for summary; null for coding
    model TEXT,
    markdown TEXT,              -- summary text (kind='summary')
    payload JSONB,              -- structured coding result (kind='coding')
    evidence JSONB,             -- the facts dict the AI saw, if includeEvidence
    cost_usd NUMERIC(10, 6),
    latency_ms INTEGER,
    vault_path TEXT,            -- relative path under VAULT_PATH if mirrored to vault
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS patient_summaries_lookup
    ON patient_summaries(patient_id, kind, created_at DESC);
