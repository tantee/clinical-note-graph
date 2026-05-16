CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Application configuration (key/value)
CREATE TABLE IF NOT EXISTS app_config (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS patients (
    patient_id TEXT PRIMARY KEY,
    name TEXT,
    gender TEXT,
    birth_date DATE,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS encounters (
    encounter_id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    date_time TIMESTAMPTZ NOT NULL,
    department TEXT,
    provider TEXT,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS encounters_patient_idx ON encounters(patient_id, date_time);

CREATE TABLE IF NOT EXISTS documents (
    document_id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
    encounter_id TEXT REFERENCES encounters(encounter_id) ON DELETE SET NULL,
    source_system TEXT,
    source_document_id TEXT,
    version TEXT,
    format TEXT NOT NULL,
    raw_content TEXT NOT NULL,
    raw_json JSONB,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (patient_id, source_document_id, version)
);

CREATE INDEX IF NOT EXISTS documents_patient_idx ON documents(patient_id);

CREATE TABLE IF NOT EXISTS facts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    patient_id TEXT NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
    encounter_id TEXT REFERENCES encounters(encounter_id) ON DELETE SET NULL,
    document_id TEXT REFERENCES documents(document_id) ON DELETE SET NULL,
    type TEXT NOT NULL,
    value TEXT NOT NULL,
    normalized_code TEXT,
    coding_system TEXT,
    date_time TIMESTAMPTZ,
    evidence_text TEXT,
    confidence REAL,
    review_status TEXT NOT NULL DEFAULT 'ai_suggested',
    extra JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS facts_patient_idx ON facts(patient_id, type);
CREATE INDEX IF NOT EXISTS facts_doc_idx ON facts(document_id);

CREATE TABLE IF NOT EXISTS ai_outputs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id TEXT REFERENCES documents(document_id) ON DELETE SET NULL,
    patient_id TEXT REFERENCES patients(patient_id) ON DELETE SET NULL,
    prompt_template TEXT,
    model TEXT,
    raw_output JSONB,
    valid BOOLEAN,
    validation_errors JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_log (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    actor TEXT,
    action TEXT NOT NULL,
    target_type TEXT,
    target_id TEXT,
    payload JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    patient_id TEXT,
    document_id TEXT,
    payload JSONB,
    result JSONB,
    error TEXT,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Embeddings for facts and notes
CREATE TABLE IF NOT EXISTS embeddings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    patient_id TEXT,
    ref_type TEXT NOT NULL,  -- 'fact' | 'note' | 'document'
    ref_id TEXT NOT NULL,
    content TEXT NOT NULL,
    embedding vector(1536),
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS embeddings_patient_idx ON embeddings(patient_id);

-- Export profiles
CREATE TABLE IF NOT EXISTS export_profiles (
    profile_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    config JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO export_profiles (profile_id, name, config) VALUES
('default-summary', 'Default Summary', '{"fields":["problems","medications","observations","plans"],"format":"markdown","includeEvidence":true,"codingStandards":["ICD10","SNOMEDCT"]}'::jsonb)
ON CONFLICT (profile_id) DO NOTHING;
