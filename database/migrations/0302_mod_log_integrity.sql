-- 0302_mod_log_integrity.sql -- tamper-evident hash chaining for the mod log.
--
-- Each event stores the hash of the previous event in its guild's chain plus
-- its own hash (derived from prev_hash + the event's canonical fields). A break
-- in the chain means a row was altered or deleted out of band; .modlog verify
-- walks the chain and reports the first break.

ALTER TABLE mod_log_events ADD COLUMN IF NOT EXISTS prev_hash TEXT;
ALTER TABLE mod_log_events ADD COLUMN IF NOT EXISTS hash      TEXT;
