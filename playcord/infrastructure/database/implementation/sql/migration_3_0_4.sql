-- Migration 3.0.4
-- Match IDs are always application-assigned (Discord thread snowflake). No serial fallback.

ALTER TABLE matches ALTER COLUMN match_id DROP IDENTITY IF EXISTS;

ALTER TABLE matches ALTER COLUMN match_id DROP DEFAULT;

DROP SEQUENCE IF EXISTS matches_match_id_seq CASCADE;
