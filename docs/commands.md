# Command reference

Default prefix is `.` (configurable with `.set prefix`). Most management commands
require a server permission, and the bot needs the matching permission itself
(run `.setup` to audit). A handful of act-fast commands are also available as
slash commands.

## General

| Command | What it does |
|---|---|
| `.help` (`/help`, `.commands`, `.h`) | Open the command help hub. |
| `.about` (`.info`) | What Clankwarden is, with an invite link. |
| `.ping` (`.latency`) | Gateway latency and uptime. |
| `.invite` | The bot's least-privilege invite link. |
| `.setup` (`.permissions`, `.perms`, `.diagnose`) | Audit the bot's permissions in this server and show what to fix. |
| `.init` (`.setupwizard`) | Guided one-command setup (roles, tank, hunter channel, mod-log). |

## Moderation

Each needs the matching permission (e.g. Ban Members for `.ban`).

| Command | What it does |
|---|---|
| `.ban <user> [reason]` (`/ban`) | Ban a member or a user id. |
| `.unban <user_id> [reason]` | Lift a ban. |
| `.softban <member> [reason]` | Ban + immediately unban to clear recent messages. |
| `.massban <ids...>` (`.banmany`, `.mban`) | Ban many ids, paced to avoid rate limits. |
| `.kick <member> [reason]` (`/kick`) | Kick a member. |
| `.timeout <member> <duration> [reason]` (`/timeout`, `.mute`) | Timeout for e.g. `10m`, `1h`, `2d` (max 28d). |
| `.untimeout <member> [reason]` (`/untimeout`, `.unmute`) | Remove a timeout. |
| `.warn <member> [reason]` | Warn a member and record it. |
| `.warnings <member>` (`.warns`) | List a member's warnings. |
| `.delwarn <id>` (`.unwarn`) | Delete a warning. |
| `.purge <amount> [member]` (`.clear`, `.prune`) | Bulk-delete messages. |
| `.slowmode <duration>` (`.slow`) | Set channel slowmode (`0` to disable). |
| `.lock` / `.unlock` | Lock or unlock the current channel. |

## Containment (`.clank`, `.clanker`)

The scammer/bot-account containment system. `.clank` actions need Manage Roles;
`/clank` and `/unclank` are limited to the mod team and the clanker-hunter role.
**Administrators and the server owner are immune** -- no clank path (automatic or
manual) will contain them.

| Command | What it does |
|---|---|
| `.clank @user [reason] [duration]` (`/clank`) | Clank a user: strip their roles, confine them to the tank. Optional duration like `30m`, `2h`, `7d`. |
| `.clank pausedms on\|off` | Auto-arm Discord's "Pause DMs" security action and keep it on (re-arms the 24h window). No argument shows the state. Needs Manage Server. |
| `.unclank @user` (`.release`, `.free`, `/unclank`, `.clank unclank`) | Release a user and restore their roles. |
| `.clank list` | Active clankers. |
| `.clank info @user` | A clanker's full record. |
| `.clank case <n>` | Look up a case by number. |
| `.clank evidence @user` | Stored message evidence. |
| `.clank logs [@user]` | Recent containment audit events. |
| `.clank scan <roleA> <roleB>` | Scan members in a role band for scam signals. |
| `.clank sync` | Reconcile the in-memory cache with the database. |
| `.clank tree @user` / `.clank links @user` | Account-linkage tree / connections. |
| `.clank stats` (`.clank chart`) | Containment analytics chart. |
| `.clank clusters` / `.clank cluster <id> ...` | Linked-account clusters (label / add / remove / cleave). |
| `.clank hunter channel\|role\|list` | Configure the scammer-report (hunter) channel and role. |
| `.clank guard ...` (`.clank clamp`) | Ambient tank guards: `clean` (urls/addresses/scams), `automod` (auto mute/delete), `sweep`, `isolate`, `quiet`, `check`. |
| `.clank taunt` | Post an anonymous, reformulated message into the tank. |
| `.clank escape` | (Clankers) get your escape-room link. |
| `.clank er status\|reload\|reset\|purge\|info\|setthread\|clear` | Escape-room administration. |

## Smart Dehoist (`.dehoist`, `.dh`)

Learns this server's impersonation signals and acts on members **below a
configurable floor role** (staff/trusted are never touched).

| Command | What it does |
|---|---|
| `.dehoist` | Open the config panel (enable, mode, floor role, log channel). |
| `.dehoist on` / `.dehoist off` | Enable / disable. |
| `.dehoist mode <off\|warn\|rename\|rename_clank>` | What auto-mode does on a match. |
| `.dehoist floor <role>` | Only members at/below this role are eligible. |
| `.dehoist channel [#ch]` (`.dehoist log`) | Where dehoist alerts post. |
| `.dehoist signals` (`.dehoist intel`) | Show the impersonation lexicon learned from this server. |
| `.dehoist test <name>` | Test a literal name against the signals (no action). |
| `.dehoist scan [limit]` | Preview current matching members (read-only). |
| `.dehoist sweep` | Act on all current matches, paced to avoid rate limits. |
| `.dehoist whitelist <member>` (`.dehoist wl`) | Exempt a member. |
| `.dehoist recent` (`.dehoist events`) | Recent dehoist actions. |
| `/report <user> [reason]` | **Anyone** can report a suspected scammer; mods get an actionable alert (Clank / False-report). |
| `/dehoist <user>` | Mods: dehoist one member immediately. |

## Mod log (`.modlog`, `.mlog`)

Categorized, tamper-evident (hash-chained) audit logging.

| Command | What it does |
|---|---|
| `.modlog` | Show the mod-log configuration. |
| `.modlog channel [#ch]` | Set the default mod-log channel. |
| `.modlog route <category> <#ch>` | Route one category to a channel. |
| `.modlog mute\|unmute <category>` | Stop / resume logging a category. |
| `.modlog timeline [member]` (`.history`) | Recent logged events. |
| `.modlog case <id>` (`.lookup`, `.view`, `.ref`) | Look up one event. |
| `.modlog stats [hours]` | Event counts by category. |
| `.modlog prune <days>` | Delete events older than N days. |
| `.modlog verify` (`.audit`, `.integrity`) | Verify the hash chain. |
| `.modlog alert channel\|role` | Configure high-severity escalation. |
| `.modlog incident` / `.modlog test` | Toggle incident mode / emit a test event. |

## Settings (`.settings`, `.set`)

`.settings` (`.config`, `.cfg`) shows every per-server setting. Edit with
`.set <option> <value>` (use `none` to clear). Everything is also editable in the
Auren web UI.

| Option | Sets |
|---|---|
| `prefix` | Command prefix (max 5 chars). |
| `log` / `modlog` | General log / mod-log channel. |
| `clankerrole` (`role`) | The Clanker containment role. |
| `category` | The Clanktank category. |
| `tank` (`containment`, `clanktank`) | The tank channel. |
| `clankerlog` (`tanklog`) | Containment log channel. |
| `escapethread` (`escape`, `thread`) | Escape-room thread. |
| `reflection` (`wait`) | Escape-room reflection wait (1-120 min). |
| `pausedms` (`dmpause`) | Auto Pause DMs security action on/off (also `.clank pausedms`). |
| `hunterrole` / `hunterchannel` | Clanker-hunter role / report channel. |
| `autodelete` (`ad`) | Auto-delete command/mod replies after N seconds (0 = keep). |
| `autodeleteinfo` (`adinfo`) | Auto-delete info panels after N seconds (0 = keep). |

Dehoist options live under `.dehoist`. Escape-room messages are never
auto-deleted.

## REST API

With `CLANK_API_KEY` set, the per-guild settings routes under `/api/v2/guilds/*`
accept an `X-API-Key` header; `/health` is public. See
[`docs/deployment.md`](deployment.md).
