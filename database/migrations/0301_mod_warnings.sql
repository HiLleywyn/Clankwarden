-- 0301_mod_warnings.sql -- per-user moderator warnings.
--
-- Backs .warn / .warnings / .delwarn. Each row is one warning issued by a
-- moderator against a member; soft-deleted via active=false so history is kept.

CREATE TABLE IF NOT EXISTS mod_warnings (
    id            BIGSERIAL    PRIMARY KEY,
    guild_id      BIGINT       NOT NULL,
    user_id       BIGINT       NOT NULL,
    moderator_id  BIGINT       NOT NULL,
    reason        TEXT         NOT NULL DEFAULT 'No reason given',
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    active        BOOLEAN      NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_mod_warnings_user
    ON mod_warnings (guild_id, user_id, active, created_at DESC);
