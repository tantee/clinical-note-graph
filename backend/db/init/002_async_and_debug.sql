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
