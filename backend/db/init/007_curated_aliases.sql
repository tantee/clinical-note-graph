-- backend/db/init/007_curated_aliases.sql
-- Rename provenance for the curated longitudinal layer.
--
-- `aliases` accumulates every prior display value a curated row has carried
-- (and every differing AI source value reconcile has seen for it). It is the
-- bridge that keeps the curated identity sticky across a rename:
--   * reconcile re-links a re-mention of an old name to the same row instead of
--     minting a duplicate (matched case-insensitively against this list);
--   * the ingest reconcile path and graph/rebuild post-pass relabel/collapse the
--     stale old-value Neo4j node onto the curated display value, so the graph
--     never diverges after a rename.
-- Idempotent: safe to re-apply (matches the numbered-migration pattern in 001..006).

ALTER TABLE curated_facts
    ADD COLUMN IF NOT EXISTS aliases JSONB NOT NULL DEFAULT '[]'::jsonb;
