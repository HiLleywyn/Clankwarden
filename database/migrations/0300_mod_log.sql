-- 0300_mod_log.sql -- the comprehensive moderation event log.
--
-- One append-only row per tracked event across every category (security,
-- moderation, member, message, role, channel, command, config, AI,
-- infrastructure, clanktank, analytics). Powers the mod-log channel panels,
-- the .modlog timeline, per-actor/target history, and retention pruning.

CREATE TABLE IF NOT EXISTS mod_log_events (
    id          BIGSERIAL    PRIMARY KEY,
    event_id    TEXT         NOT NULL,                 -- short public id (evt_xxved)
    guild_id    BIGINT       NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    category    TEXT         NOT NULL,
    severity    TEXT         NOT NULL DEFAULT 'info',
    event_type  TEXT         NOT NULL,
    actor_id    BIGINT,
    target_id   BIGINT,
    channel_id  BIGINT,
    summary     TEXT         NOT NULL DEFAULT '',
    metadata    JSONB        NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_mod_log_guild_time
    ON mod_log_events (guild_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_mod_log_guild_cat
    ON mod_log_events (guild_id, category, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_mod_log_guild_actor
    ON mod_log_events (guild_id, actor_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_mod_log_guild_target
    ON mod_log_events (guild_id, target_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mod_log_event_id
    ON mod_log_events (guild_id, event_id);
