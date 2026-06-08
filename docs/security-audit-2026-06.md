# Clankwarden -- Security Audit (2026-06)

A defensive audit in the spirit of the `snake/audits` corpus: **characterize,
don't exploit; disclose fix-first.** Method: enumerate the privileged/destructive
actions and the trust boundaries they cross (command -> role mutation, event ->
audit chain, API key -> per-guild settings, untrusted input -> matcher), then dig
where authority is granted. "No finding by this analysis" is not proof of absence.

Verdict in one line: the data plane is well-parameterized and cross-guild
isolation holds; the findings were an audit chain that was *evident* but not
*proof*, a re-arm window that lapsed under restart, a non-constant-time key
compare, and a missing hierarchy guard on `.clank`. All four are fixed.

## Findings

| # | Severity | Location | Status |
|---|----------|----------|--------|
| 1 | Medium | `cogs/clank.py` Pause-DMs re-arm (`_security_sweep` / `_ensure_dms_paused`) | **Fixed** |
| 2 | Medium | `clanklib/modlog.py` `_event_hash` -- unkeyed audit chain | **Fixed** |
| 2b | Medium | `clanklib/modlog.py` `verify_chain` -- only checked oldest 5000 events | **Fixed** |
| 3 | Medium | `api/v2/main.py` `require_key` -- non-constant-time compare | **Fixed** |
| 4 | Low | `cogs/clank.py` `_do_clank` -- missing invoker/target hierarchy guard | **Fixed** |
| 5 | Low | `api/v2/main.py` -- single global `CLANK_API_KEY`, no per-guild scope | Residual (by design) |
| 6 | Low | `clanklib/modlog.py` -- `metadata` not bound into the chain | Follow-up |

### 1. Pause-DMs lapses after ~24h and is not re-enabled (Medium, fixed)
Discord caps each "Pause DMs" security action at 24h; the bot arms a 23h55m
window and re-arms it. The re-arm ran hourly and only when under a 6h floor, so
any ~6h period where the loop did not tick before expiry (a restart/redeploy or
outage on the managed platform) let the pause lapse with no catch-up. Separately,
discord.py rebuilds `guild._incidents_data` from every `GUILD_UPDATE`, and most
such payloads omit `incidents_data` -- so an unrelated guild change silently
clobbers `guild.dms_paused_until` to `None`, making the headroom check unreliable.
This is the "enabled until 1:59pm, gone after 1:59pm today" symptom.
**Fix:** sweep every 15 min, re-arm with a 12h margin, and remember the expiry we
last armed so a clobbered gateway value can't defeat the headroom check. The
window now only lapses on a >12h continuous outage ending at expiry, and any
restart restores it within minutes. Covered by new unit tests.

### 2. Audit chain is tamper-evident but not tamper-proof (Medium, fixed)
`_event_hash` was an **unkeyed** SHA-256 over nine fields. That detects an
out-of-band row edit/delete (the chain breaks), but anyone who can recompute
SHA-256 -- e.g. a party with DB-write access -- could edit a row and recompute
every subsequent hash, and `verify_chain` would still report "verified." The
`.modlog verify` copy overstated this as proof.
**Fix:** new events use a keyed **HMAC-SHA256** (`MODLOG_CHAIN_KEY`, else the
platform `AUREN_PROVISION_SECRET`) that also binds `channel_id`. A `hash_version`
column (migration `0305`) lets pre-upgrade rows keep verifying under the legacy
algorithm. The verify wording now says "no row altered/removed by anything
without the chain key." If no key is configured the bot warns once.

### 2b. `verify_chain` only verified the oldest 5000 events (Medium, fixed)
`verify_chain(limit=5000)` ran `ORDER BY id ASC LIMIT 5000`, so for any guild
with more than 5000 events it walked only the **oldest** page and returned
`{"ok": true}` while never looking at newer events -- exactly the rows an
attacker would most likely tamper with. The keyed chain (#2) is moot if the
verifier skips most of it.
**Fix:** `verify_chain` now paginates by `id` and checks every persisted event;
`page` is only the batch size. Covered by a one-row-per-page test.

### 3. Non-constant-time API key compare (Medium, fixed)
`require_key` compared `x_api_key != configured` with `!=`. Replaced with
`hmac.compare_digest`. Low network exploitability, but it is the single auth
check the settings API rests on.

### 4. Missing hierarchy guard on manual `.clank` (Low, fixed)
`mod.py` guards every command with `_hierarchy_ok` (cannot action owner/self/a
member who outranks you). The containment path only made owners/admins/bot-self
immune, so a junior mod with Manage Roles could clank a senior non-admin mod.
**Fix:** `_do_clank` now refuses a manual clank whose target's `top_role` is
>= the moderator's (owners/admins bypass; automatic and hunter flows unaffected,
since scammers sit below staff).

### 5. Single global settings-API key (Low, residual / by design)
`/api/v2/guilds/{guild_id}/settings` authenticates with one process-wide
`CLANK_API_KEY` and accepts any `guild_id`. Within the documented single-tenant
control-plane model (the Auren control plane is the sole intended key holder)
this is not an IDOR; there is no defense-in-depth pinning a key to guilds. No
change -- flagged so it stays a conscious decision. Revisit if a lower-trust
party ever holds the key.

### 6. `metadata` is persisted but not bound into the chain (Low, follow-up)
The chain (even v2) does not cover the free-form `metadata` dict (recorded ban
reason, deleted-message content, invite attribution), so an attacker editing only
`metadata` leaves the chain intact. Reliable hashing requires persisting a
canonical, non-truncated metadata string to hash deterministically (the current
store truncates to a byte cap), which is a storage-format change -- deferred to
avoid false chain breaks. Tracked for a follow-up migration.

## Verified clean (no finding)
- **SQL:** every user-influenced value is a bound `$n` parameter. The few dynamic
  SQL sites interpolate only column names from fixed internal allowlists
  (`_GUILD_SETTING_COLUMNS`, literal column lists), never user strings.
- **Permission gating:** `ModCog.cog_check` gates all `.set`/`.modlog`/`.dehoist`
  subcommands to manage_guild/admin; `mod.py` carries matching
  `has_/bot_has_guild_permissions` + hierarchy guards.
- **Cross-guild isolation:** all clanker/escape/cluster/case queries are
  `guild_id`-scoped; the two historical leaks (global escape-thread override,
  cross-server mod-log) are fixed and verified; the settings cache is per-guild.
- **Untrusted input:** join-name blacklist uses substring matching (no
  user-supplied regex / ReDoS); dehoist regexes are linear; the hunter report
  flow drops bots/self/mods/admins/hunters and ignores reply-injected mentions.
- **Secrets:** no token/key/DSN is logged or returned in API/error bodies.
