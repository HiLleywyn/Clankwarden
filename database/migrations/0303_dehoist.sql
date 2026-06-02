-- 0303_dehoist.sql -- Clankwarden smart-dehoist: cached per-guild signals + an
-- event log that backs the dehoist analytics (mirrors the clanker_* style).

-- Cached, derived "what impersonation looks like here" signals. Refreshed by the
-- dehoist cog's background task and on demand; read on the hot path so the
-- on-join/on-message checks never have to rescan the whole guild.
CREATE TABLE IF NOT EXISTS dehoist_signals (
    guild_id     BIGINT PRIMARY KEY,
    topics       TEXT[]      NOT NULL DEFAULT '{}',
    brand_terms  TEXT[]      NOT NULL DEFAULT '{}',
    staff_terms  TEXT[]      NOT NULL DEFAULT '{}',
    lexicon      TEXT[]      NOT NULL DEFAULT '{}',
    refreshed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per dehoist action -- the analytics/audit source for the tankboard.
CREATE TABLE IF NOT EXISTS dehoist_events (
    id         BIGSERIAL PRIMARY KEY,
    guild_id   BIGINT      NOT NULL,
    user_id    BIGINT      NOT NULL,
    old_name   TEXT        NOT NULL,
    new_name   TEXT        NOT NULL,
    kind       TEXT        NOT NULL,           -- 'hoist' | 'impersonation' | 'both'
    matched    TEXT        NOT NULL DEFAULT '',-- lexicon phrase or hoist run
    action     TEXT        NOT NULL,           -- 'rename' | 'rename_clank' | 'warn'
    trigger    TEXT        NOT NULL,           -- 'join' | 'message' | 'update' | 'sweep' | 'manual'
    confidence REAL        NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_dehoist_events_guild_time
    ON dehoist_events (guild_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_dehoist_events_guild_kind
    ON dehoist_events (guild_id, kind);
