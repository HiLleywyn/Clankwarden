# Changelog

## [clankwarden] -- 2026-06-03

### Containment -- 5-level Clank Tank
- **Depth-based containment.** A clanker is now placed at a depth (L1..L5) that
  matches their threat instead of one flat escape room. **L1 (Orientation)** is
  a short, friendly education gate -- read the server rules / etiquette / scam
  safety and answer one question to walk free. **L5 (Clankermax)** is the full
  gauntlet reserved for confirmed scammers. To leave, a clanker must **pass each
  level's trial to rise one level toward the surface**; clearing L1 releases
  them. **Failing a level's gate sinks them one level deeper** (toward L5) and
  adds **rust**, which lengthens deeper reflection waits -- a cycle of rust. A
  Clankermax must climb all five.
- **Auto-assessed entry depth.** Scam-username / celebrity-impersonation /
  CCI-100% joins and cluster cleaves enter at **L5** (and gain the Clankermax
  role); hunter reports and clutch sweeps at **L4**; AutoMod hits at **L3**;
  dehoist/impersonation at **L3**. Manual clanks default to **L1**; a mod may
  set the depth: `.clank @user 5 reason`, the `/clank` `level` option, or move
  someone with `.clank level @user <1-5>`.
- **One new role: Clankermax.** Layered on top of the base Clanker role at L5
  (so all existing enforcement is unchanged), added on reaching L5 and removed
  on rising above it or on release. Auto-created by name if unset; configurable
  via `CLANKERMAX_ROLE_ID`.
- **Fun to watch.** A public **Tank Board** (`.clank tank`) shows every
  clanker's depth, a rust meter, and a depth ladder; short descent/ascent
  animations play in the tank channel on each level change. Toggle with
  `CLANK_TANK_BOARD`. Level changes also route through the mod-log
  (`clanktank` category: placed / ascend / descend / released).
- **Every current clanker starts at L3.** The 0304 migration backfills all
  existing containment records to depth **L3** (STANDARD CONTAINMENT), and
  members already wearing the Clanker role from a previous bot are adopted into
  the system at **L3** on startup (and via `.clank import`). New manual clanks
  still default to L1. Legacy imports have no stored roles, so a configurable
  default role
  (`CLANK_DEFAULT_ROLE_ID`, falling back to a role named User/Member) is
  registered as their restore set -- handed back only when they are unclanked.
- `.clank info` now shows depth, entry depth, rust, and Clankermax status.
- New `.set` options: `clankermax` (the L5 role), `defaultrole` (the legacy
  restore role), and `tankboard` (on/off). `.settings` shows all three.

### Settings / mod-log -- display fixes
- **Escape-room thread no longer shows as "(missing)".** The settings/mod-log
  channel renderer used `guild.get_channel`, which does not resolve threads, so
  a correctly-configured `clank_escape_thread` always displayed as missing. It
  now uses `get_channel_or_thread` and falls back to a raw `<#id>` mention.
- **Stale mod-log routes are pruned.** `.modlog` dropped per-category routes
  (and the default/alert channel + ignore entries) that point at channels which
  have since been deleted, instead of listing dead/old channels that are not
  present anymore. Cleanup also runs automatically when a channel is deleted.

## [clankwarden] -- 2026-06-02

### Containment -- critical fixes
- **Per-guild escape rooms.** Escape-thread resolution no longer prefers a
  single global override seeded from "some guild's" `clank_escape_thread`
  (`LIMIT 1`), which pointed every server's clankers at one server's escape
  room and made `.clank escape` report "not configured" when a server's thread
  was, in fact, set. Each guild now resolves its OWN `clank_escape_thread`
  first (env/override only as a fallback), threaded through every caller
  (`.clank escape`, the station-4 refresh, view restore, and the `.clank er`
  admin commands). The tank channel is likewise resolved per-guild everywhere
  it was still read straight from `CLANKTANK_CHANNEL_ID`.
- **Mod-log: no more dead channels.** `.init` created a `log-<category>`
  channel for every category, but `command`, `ai`, `clanktank` and `analytics`
  have no event producer, so those channels stayed permanently empty ("half the
  logs don't populate"). `.init` now provisions a channel only for categories
  that actually emit. (Existing servers can delete the four empty channels or
  re-run `.init`.)
- **Clank logs no longer leak across servers.** `_log_mod` resolved the
  clank-log channel from "whichever guild the bot is in has one configured,"
  which sent one server's containment logs into a *different* server's
  clank-log channel when the bot moderates more than one guild. Every clank
  log now carries the guild the event happened in (`guild_id` is a required
  argument), so a panel can only ever post to its own server.
- **Clankers can talk in the tank and the escape room again.**
  `_is_tank_surface` only recognised the tank via the `CLANKTANK_CHANNEL_ID`
  env var, so a server set up through `.init` (which stores the tank as a
  per-guild setting) had every clanker message -- including in the escape-room
  thread -- treated as "outside containment" and deleted, making `.clank
  escape` impossible. It now resolves the tank and escape thread from the
  guild's own settings.

### Mod-log
- **Global ignore list (new).** A per-server ignore list the mod log excludes
  entirely -- ignored events are still recorded for the timeline but never
  posted and never escalate. It now covers:
  - **Bots** -- `.modlog ignore bots` toggles ignoring *every* bot (any event
    whose actor or target is a bot), so the log stops being flooded by other
    bots. `bots on` / `bots off` set it explicitly.
  - **Users** -- `.modlog ignore @user` (ignored as actor or target).
  - **Roles** -- `.modlog ignore @role` (any actor/target member with the role).
  - **Channels** -- `.modlog ignore #channel` (message edits/deletes there are
    not even recorded, so the channel stays private).
  Mentions can be mixed (`.modlog ignore #spam @user @role`); `remove`/`rm`
  un-ignore and `clear` wipes everything. The `.modlog` panel summarises what's
  being ignored.

### Pre-deploy cleanup
- **Plain command names (dropped the prison theme).** `.clank @user` now clanks
  directly and `.unclank @user` releases (the `book`/`parole` subcommands are
  gone). All the cute names were reverted to plain ones, with the old cryptic
  names kept as aliases: `hunter` (was snitch), `links` (was snitchline/cline),
  `guard`/`clean`/`automod`/`sweep`/`isolate`/`quiet`/`check`/`taunt` (were
  clamp/clear/clasp/clutch/cloister/clad/clink/clarion), and
  `list`/`info`/`evidence`/`scan`/`sync`/`tree`/`stats`.
- **More slash commands.** Added `/help`, `/clank` and `/unclank` (the last two
  restricted to the mod team + the clanker-hunter role); fixed the blank
  descriptions on `/kick` and `/untimeout`; every slash command now `defer()`s so
  it answers instantly instead of burning Discord's 3s budget.
- **Report flow.** You can no longer report yourself, a bot, a moderator, or a
  clanker hunter. Every report alert now carries a **False report (30m)** button
  that clanks the *reporter* into the tank for 30 minutes.
- **Tank hardening.** Clankers can no longer message in threads (no escape-room
  spam), post images/attachments/embeds, create threads, run polls, or react in
  the tank.
- **`.init` sets up clanker hunters.** The wizard now creates the Clanker Hunter
  role and a `clanker-hunters` report channel under the Clanktank category and
  saves both (`scam_hunter_role` / `scam_report_channel`).
- **Auto-delete (new, opt-in).** Two per-guild tiers: `autodelete_replies` for
  command/mod replies and `autodelete_info` for informational panels
  (`.help`/`.about`/`.settings`), set via `.set autodelete`/`.set autodeleteinfo`
  or the web UI. Both default to off; escape-room messages are never deleted.
- **`.settings` shows everything**, including the dehoist and auto-delete
  sections.

### Changes
- **Rebrand: Clanksimus Prime is now Clankwarden.** Manifest (`slug`/`name`/
  `repo`), entrypoint, help/about/invite UI, API title and all docs updated.
  The `.clank` command verb, the `clanker_*`/`clank_*` tables, migration files
  and the `clanklib/` package are intentionally unchanged (no DB or import
  churn). The GitHub repository rename to `clankwarden` is a separate, manual
  step.
- **New: Smart Dehoist (`cogs/dehoist.py`, `clanklib/dehoist.py`).** A
  server-aware impersonation/hoist guard. It scans each guild's own signals
  (role names, staff, channel names + topics, description) to learn the
  impersonation handles that community actually attracts -- an investing server
  breeds "Binance Support" / "Crypto Team" -- then on join / message / nick
  change it dehoists (renames) and, by default, auto-clanks offenders **below a
  configurable floor role** (staff and trusted roles are never touched). Unicode
  normalization folds zalgo / fullwidth / cyrillic homoglyphs so "Binance" and
  "Binаnce" match. Renames are paced through `BulkRunner` to avoid rate-limit
  bans; every action is logged via `bot.modlog` and recorded to the new
  `dehoist_events` table for analytics. Configurable per guild
  (`DEHOIST_ENABLED`/`DEHOIST_MODE`/`DEHOIST_FLOOR_ROLE_ID`/`DEHOIST_LOG_CHANNEL_ID`
  /`DEHOIST_TRIGGERS`/`DEHOIST_TOPICS`) and gated behind a new `manage_nicknames`
  permission (invite value `1494917180662` -> `1495051398390`).
- **Actionable Components V2 panels.** Dehoist alerts carry inline mod buttons
  (Undo rename / Whitelist / Clank now); `/report` posts an actionable alert with
  a Clank button; the config panel (`.dehoist`) is an interactive wizard with an
  enable toggle, mode cycle, and role/channel selects.
- **Some slash commands for in-the-moment actions.** `/report` (public),
  `/dehoist <user>` (mod), and hybrid `/ban` `/kick` `/timeout` `/untimeout`.
  Config/analytics/cluster/escape-room commands stay prefix-only.
- **Friendlier `.clank` command names.** The cryptic `cl-` soup was renamed with
  the old names kept as hidden aliases (nothing breaks): `clamp`->`lockdown`,
  `clamp clear`->`lockdown confiscate`, `clamp clasp`->`lockdown muzzle`,
  `clutch`->`dragnet`, `cloister`->`solitary`, `clad`->`lightsout`,
  `clink`->`frisk`, `clarion`->`intercom`, `cline`->`snitchline`, plus
  `add`->`book`, `remove`->`parole`, `list`->`cellblock`, `info`->`rapsheet`,
  `evidence`->`receipts`, `scan`->`shakedown`, `sync`->`headcount`,
  `tree`->`lineage`, `chart`->`tankboard`, `hunter`->`snitch`.

## [clanksimus-prime] -- 2026-06-02

### Changes
- **Invite now covers every feature (still no Administrator).** The previous
  least-privilege set was missing six permissions the bot genuinely uses, so
  some actions failed after a fresh invite. Added to `clanklib/permissions.py`
  (the single source of truth): `add_reactions` (the `.clank` confirmation
  prompts), `attach_files` (the Clanktank chart image), `create_private_threads`
  and `manage_threads` (the `.clank` cloister thread -- creating it and pulling
  the contained user into a private, non-invitable thread), `create_public_threads`
  (the `.setup` escape-room thread), and `send_messages_in_threads` (posting in
  both). The invite/`.setup` audit/manifest value moves from `1099780156598` to
  `1494917180662`; the manifest-vs-code test keeps them in lockstep.
- **Least-privilege invite everywhere.** The leftover `.help` "Add to server"
  button (`cogs/_help_view.py`) still built an Administrator (`permissions=8`)
  invite; it now uses the single source of truth in `clanklib/permissions.py`
  like `.invite`/`.about`/`.setup`, so every invite asks only for the
  permissions the bot uses (`permissions=1099780156598`). The manifest now
  declares this set under `discord` in `auren.json` so the Auren platform's
  Invite button matches, and a new test pins the manifest value to
  `required_bot_permissions()` so they can never drift. Fixed the deployment
  docs, which wrongly claimed Administrator was required (copied from Recycler).

## [clanksimus-prime] -- 2026-06-01

### Changes
- **Brand the mod bot consistently as "Clanksimus Prime."** The split kept the
  `Clanksimus Prime` name in the help/about UI but had switched the manifest to
  `Clanktank`; the manifest `slug`/`name` are now `clanksimus-prime` /
  `Clanksimus Prime` (matching the displayed name and the repo), and the
  `main.py` fallback app name follows. The `.clank` "tank" feature naming
  (channels, settings keys, DB columns, mod-log category) is unchanged -- only
  the bot's product identity was aligned.
- **Fix stale feature copy.** The `.help` header and `.about` panel still
  advertised the server-tool features (backups, templates, chatlogs, sync) that
  moved to Recycler. They now describe this bot's actual surface: moderation,
  audit logging, guided setup and `.clank` containment.

## [clanktank build] -- 2026-06-01

### Changes
- **Split into a containment + moderation bot ("Clanktank").** This build keeps
  the `.clank` containment system, the `mod` command set, the `modlog` audit
  suite, settings and the `.init` wizard. The server-tool features -- backups,
  templates, chatlog, sync and import/export -- and their data-plane repos,
  serializer, migrations and REST routes have been removed; they live in a
  separate bot. The invite no longer requests Manage Webhooks.

## [main] -- 2026-06-01

### New Features
- **Per-guild settings API**: A key-protected REST surface (`/api/v2/guilds/{id}/settings`, with a `/schema` endpoint) to read and update each server's config (prefix, log + containment channels) from outside Discord, sharing one validated schema with the `.set` command.
- **Live settings**: Configuration changes made in the Auren control panel
  now take effect immediately, without a redeploy. Cogs read runtime config
  (prefix, backup cap, API key, client id) from the live settings layer rather
  than a boot-time environment snapshot, so the Auren control link's
  per-heartbeat sync actually reaches the commands.
- **Server backups**: Create full, restorable guild snapshots (settings, roles,
  categories, channels, permission overwrites and optional recent messages),
  manually or on a recurring interval with rolling retention.
- **Community templates**: Publish and apply structure-only server blueprints;
  free for everyone, browsable and searchable.
- **Chatlog archives**: Save a channel's recent messages and replay them into
  any channel via webhooks.
- **Sync**: Mirror messages between channels and propagate bans/unbans between
  guilds.
- **Import / Export**: Download a backup as portable JSON and import one back.
- **Settings**: Per-guild configuration (prefix, log channel, containment
  channels) shown and edited in a Components V2 panel.
- **`.clank` containment**: Ported the full Discoin clanktank subset (scam/bot
  containment, evidence, account-linking, escape room) under the `.clank`
  command group (alias `.clanker`).
- **REST API**: Embedded FastAPI app with a public `/health` endpoint and
  key-protected `/api/v2` reads for backups and templates.

### Documentation
- Thick, end-to-end deployment guide (`docs/deployment.md`) covering all four
  pathways - local/bare-metal, Docker, Railway and Auren - with
  prerequisites, post-deploy verification, upgrade/rollback and troubleshooting.
- Installation quick-start, full configuration reference and a complete command
  reference.

### Reliability
- **`.init` now actually applies permissions**: the wizard makes the Clanker
  role a real jail role -- it denies the role View Channel on every existing
  channel (paced) and grants it only in the tank, so a clanked user (stripped to
  @everyone + Clanker) can no longer see the rest of the server. Revert undoes
  the lockdown along with everything else it created.
- **Bulk ban + ban-sync backfill** on the paced runner: `massban <ids> [reason]`
  (hierarchy-checked, deduped, capped, with a live progress message) and
  `sync backfill <link_id>` to apply a source guild's existing bans to the
  target -- both paced so they can't trigger a rate-limit ban.
- **Paced mass actions**: bursting hundreds of clanks/bans/timeouts at once
  earns a multi-hour Cloudflare 429 (we hit a 2h ban cleaving 500 accounts). A
  new `BulkRunner` (`clanklib/ratelimit.py`) serializes and paces big batches,
  backs off on 429, and aborts the run after a few consecutive rate limits (or a
  long global retry-after) instead of escalating into a longer ban. Wired into
  cluster cleave (with a live progress message), clutch mass-clank, and clad
  bulk-timeout.

### Guided setup
- **`.init`**: a one-command guided setup. Creates a **Clanktank** category
  (the tank, its escape-room thread, and a staff-only clank-logs channel) with
  the Clanker role locked out of every other channel, and a mod/admin-only
  **Mod Logs** category with one auto-routed channel per log category
  (security, moderation, member, message, ...). Pick which categories to
  provision, Confirm before anything is created, then **Keep** or **Revert** --
  Revert deletes exactly what the run created (never anything pre-existing) and
  clears the settings it wrote. A mid-run failure auto-rolls back. Does not
  touch the scam-report/hunter channel. Uses Manage Roles + Manage Channels; no
  Administrator.

### Moderation commands
- **Mod command set** (`cogs/mod.py`), Components V2 native: `ban`, `unban`,
  `softban`, `kick`, `timeout`/`mute`, `untimeout`/`unmute`, `warn`,
  `warnings`, `delwarn`, `purge`, `slowmode`, `lock`, `unlock`.
- **Locked down**: every command requires the matching guild permission AND the
  matching bot permission, and every member action passes a role-hierarchy
  guard (you cannot action the server owner, the bot, yourself, anyone at or
  above your top role, or anyone above the bot). Durations accept `10m`, `1h`,
  `2d`; timeouts clamp to Discord's 28-day max; purge caps at 200.
- Warnings persist (`mod_warnings`), can be listed and individually removed, and
  every action is recorded through the central mod log.

### Moderation logging
- **Comprehensive mod log**: A new centralized logger (`bot.modlog`) records
  every tracked event under one standardized schema (short event id, UTC
  timestamp, category, severity, actor, target, channel, summary, metadata)
  and renders it as a Components V2 panel. Categories: security, moderation,
  member, message, role, channel, command, configuration, AI, infrastructure,
  clanktank, analytics.
- **Event coverage**: member join/leave, bans/unbans, timeouts, role and
  nickname changes, role create/delete/update (with permission deltas),
  channel create/delete, and message delete/edit/bulk-delete are all logged,
  with the acting moderator attributed from the audit log where available.
- **Configuration changes are now logged**: every `.set` change (and the
  `.modlog` routing changes) records a configuration event, so channel/role
  config edits are auditable.
- **Operator controls**: `.modlog` shows the current routing and 24h stats;
  `.modlog channel`, `.modlog route <category> #ch`, `.modlog mute/unmute
  <category>`, `.modlog timeline [@user]`, `.modlog stats [hours]`,
  `.modlog prune <days>` and `.modlog test`. Muted categories are still
  recorded for the timeline.
- Per-category log routing and a default mod log channel, configurable in both
  Discord and the Auren web UI.
- **Tamper-evident audit chain**: every event stores the hash of the previous
  event plus its own; `.modlog verify` walks the chain and reports the first
  break (an altered or deleted row).
- **Realtime alerts**: ALERT/CRITICAL events (and everything during an incident)
  are mirrored to a configurable alert channel with an optional role ping
  (`.modlog alert channel/role`).
- **Anomaly detection**: join floods (raids), mass-ban bursts and message-purge
  bursts trip a CRITICAL security event automatically.
- **Incident mode** (`.modlog incident on/off`): unmutes every category and
  mirrors all events to the alert channel for the duration of a situation.
- **Invite attribution**: member-join events record the invite code and who
  invited them (best-effort, needs Manage Server).

### Changes
- Components V2 is the default UI across every command.
- Templated for Auren via `auren.json` (validated in CI).
- **Role-based clanker hunters**: The scam-hunter system now keys off a
  configurable hunter role instead of a per-user whitelist. Anyone wearing the
  role can report in the hunter channel and is immune to automatic clanking;
  the old `.clank hunter add/remove` user commands are replaced by
  `.clank hunter role @role`.
- **Full config surface, Discord and web**: Every containment/moderation option
  is now editable both in Discord (`.set ...`) and in the Auren web UI, and
  the two surfaces write the same per-guild keys. New/exposed options: clanker
  role, clanker category, clanktank channel, clanker log channel, escape-room
  thread, reflection period, hunter role, hunter channel, mod log channel.
- **Reflection period default is now 5 minutes** (was 8), and is configurable
  per server (`.set reflection <minutes>` or the web UI).

### Fixes
- **Web-UI settings now actually apply**: settings pushed from the Auren
  control plane used manifest env-style keys (`CLANK_ESCAPE_THREAD_ID`) while
  the bot read canonical lowercase keys (`clank_escape_thread`); the two
  namespaces never met, so values set in the web UI silently no-op'd. The DB
  layer now normalises control-plane keys onto the canonical keys.
