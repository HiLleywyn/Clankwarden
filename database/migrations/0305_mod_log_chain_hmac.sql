-- 0305_mod_log_chain_hmac.sql -- key the tamper-evident mod-log chain.
--
-- 0302 introduced an unkeyed SHA-256 hash chain: tamper-*evident* (an out-of-
-- band row edit/delete breaks the chain) but not tamper-*proof* (anyone able to
-- recompute SHA-256 -- e.g. a party with DB-write access -- could forge a valid
-- chain). The logger now writes a keyed HMAC-SHA256 (version 2) that also binds
-- channel_id. Each row records the version it was written under so pre-upgrade
-- rows keep verifying under the legacy algorithm; new rows use the keyed one.
--
-- NULL hash_version means a legacy (v1) row.

ALTER TABLE mod_log_events ADD COLUMN IF NOT EXISTS hash_version SMALLINT;
