-- 0304_clank_levels.sql -- 5-level clanktank depth system.
-- A clanker is placed at a depth (1..5) matching their threat. They climb toward
-- the surface (L1) by passing each level's trial; failing a level's gate sinks
-- them deeper and accrues "rust" (which lengthens deeper trials). L5 = Clankermax.
ALTER TABLE clanker_records ADD COLUMN IF NOT EXISTS level            SMALLINT    NOT NULL DEFAULT 1;
ALTER TABLE clanker_records ADD COLUMN IF NOT EXISTS entry_level      SMALLINT    NOT NULL DEFAULT 1;
ALTER TABLE clanker_records ADD COLUMN IF NOT EXISTS rust             SMALLINT    NOT NULL DEFAULT 0;
ALTER TABLE clanker_records ADD COLUMN IF NOT EXISTS level_changed_at TIMESTAMPTZ NOT NULL DEFAULT now();

-- The escape view's authoritative in-trial depth, so restored persistent views
-- can reconstruct (level, station) on restart without joining clanker_records.
ALTER TABLE clank_escape   ADD COLUMN IF NOT EXISTS level             SMALLINT    NOT NULL DEFAULT 1;

CREATE INDEX IF NOT EXISTS idx_clanker_records_level ON clanker_records (guild_id, level);

-- Backfill: every clanker that already exists predates the level system, so seat
-- them all at L3 (STANDARD CONTAINMENT). New manual clanks default to L1; this
-- one-time UPDATE only touches the rows present when the migration first runs.
UPDATE clanker_records SET level = 3, entry_level = 3;
UPDATE clank_escape   SET level = 3 WHERE completed_at IS NULL;
