# Clankwarden Privacy & Data Handling

Clankwarden is a Discord containment and moderation bot. This document
describes exactly what data it stores, why, how long, and how it is deleted.
It reflects the behaviour in this repository (see `database/migrations/*.sql`
and `clanklib/retention.py`); operators publishing the bot should host this as
their privacy policy and add their own contact details below.

## What is collected, and why

Clankwarden stores only the data its moderation features need. All of it is
scoped to the server (`guild_id`) it was produced in.

| Data | Where | Why |
|---|---|---|
| Server configuration (channels, roles, thresholds, toggles) | `guild_settings`, `dehoist_signals` | Per-server behaviour; no personal data |
| Containment state (user id, level, score, stored roles, timestamps) | `clanker_records`, `clank_escape`, `clanker_history` | Apply, track and release the containment (`.clank`) system |
| **Message content captured at containment time** | `clanker_evidence` | Evidence for a moderation decision (the messages that triggered a clank) |
| Account-link / cluster analysis (user ids, relationships) | `clanker_connections`, `clanker_clusters`, `clanker_cluster_members`, `clanker_patterns` | Detect coordinated scammer/bot rings |
| Warnings (user id, reason, moderator) | `mod_warnings` | The `.warn` system |
| Moderation audit log (actor id, target id, action summary; for message delete/edit events, the message text) | `mod_log_events` | Tamper-evident record of moderation actions (`.modlog`) |
| Dehoist actions (user id, action, the name before and after) | `dehoist_events` | Audit of automatic impersonation handling |

Clankwarden does **not** store presence, full channel transcripts, emails, IPs,
or any payment data. Raw message *content* is stored in only two places: the
evidence captured when a user is contained (`clanker_evidence`), and the text of
deleted/edited messages recorded in the moderation audit log (`mod_log_events`).
Both have retention windows (see below).

## Gateway intents

The bot requests `message_content` (to route prefix commands and scan for scam
signals), `members` (join/leave/update handling for containment and logging),
and `auto_moderation_execution` (to act on Discord AutoMod hits). It does not
request `presences`.

## Retention

- Audit events can be pruned by an operator with `.modlog prune <days>`.
- Containment records are removed when a user is released, and on the
  data-deletion paths below.
- **When the bot is removed from a server, all of that server's data is
  deleted automatically** (`on_guild_remove` -> `clanklib.retention.purge_guild_data`).
- **Stored message evidence has an automatic retention window.** Set
  `EVIDENCE_RETENTION_DAYS` (operator setting; `0` = keep forever) and a daily
  task deletes `clanker_evidence` rows older than that. Off by default so no
  existing deployment silently loses moderation evidence; operators turn it on
  to cap how long content is kept.
- **The moderation audit log has an automatic retention window too.** Set
  `MOD_LOG_RETENTION_DAYS` (operator setting; `0` = keep forever) and a daily
  task deletes `mod_log_events` rows older than that -- which is where the audit
  log's stored message text ages out. Pruning the oldest rows keeps the tail of
  the tamper-evident chain verifiable. `.modlog prune <days>` remains for manual
  one-off pruning.

## Deletion (your rights)

- **Per server:** removing the bot from a server erases that server's stored
  data (see above).
- **Per user:** a server moderator can run `.modlog forget @user` to erase a
  user's stored data in that server (containment records, captured evidence,
  warnings, dehoist and case data). This is per server: a user present in
  several servers must request it in each one. Tamper-evident audit-log entries
  are retained for integrity; an operator can erase those too on request
  (a full purge including the audit chain).
- To request deletion of your data, contact the moderators of the server in
  question, or the bot operator at the address below.

## Sharing

Clankwarden does not sell data and does not send Discord user data to
third-party analytics or advertising services. Data lives in the operator's own
Postgres database. (If an operator enables the optional AI bridge, the content
sent to it is governed by that operator's configuration.)

## Operator contact

> Operators: replace this line with a reachable contact (email or support
> server invite) before listing the bot in the App Directory.
