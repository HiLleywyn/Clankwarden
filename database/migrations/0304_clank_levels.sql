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
