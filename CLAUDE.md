# Clanktank -- Claude Code Guidelines

> This branch is the **Clanktank** build: the `.clank` scammer/bot containment
> system plus the full moderation + audit-logging suite, with the server-tool
> features (backups, templates, chatlogs, sync, import/export) split out into a
> separate bot. If you are looking at backups/templates/etc., you are on the
> wrong branch.

Clanktank is a Discord containment + moderation bot (the `.clank` group, the
`mod` command set, the `modlog` audit system, `.set` settings and the `.init`
setup wizard), built on the shared **bot framework** (`hilleywyn/framework`)
and templated for the **Sojourns** platform via `sojourns.json`.

## Default UI -- Components V2 (hard rule)

**Components V2 is the default UI.** Build every user-facing message with
`core.framework.components` (`Container().text(...).section(...).separator()...`
sent via `send_v2` / `edit_v2`), never with `discord.Embed`. The cogs in this
repo are the reference pattern -- match them. Reach for an embed only if a
feature genuinely requires one (there are currently none). This needs
`discord.py>=2.6`, which the Dockerfile and `requirements.txt` pin.

## Git & commits -- hard rules

- **Author AND committer are always `HiLleywyn <lleywyn@proton.me>`.** Never
  commit as `Claude` / `noreply@anthropic.com`. Use
  `git -c user.name="HiLleywyn" -c user.email="lleywyn@proton.me" commit`.
- **Never** put `https://claude.ai/code/session_*` links in any committed
  artifact (commit messages, PR bodies, comments).
- **Never** put a model identifier (`claude-*`, "Claude", versions) in committed
  code, docs, or commit messages.
- Develop on a feature branch; open a PR to `main` ready for review.
- Update `CHANGELOG.md` in the same commit as any user-visible change.

## Architecture

- `main.py` -- three lines: `run_manifest()` boots from `sojourns.json`
  (its `features` is the cog list) with a fallback cog list.
- `sojourns.json` -- the manifest. Source of truth for cogs + settings; the
  Sojourns control plane reads the same file. Validate with
  `python -m core.framework.manifest sojourns.json`.
- `cogs/` -- features. `clank.py` is the containment subset (group `.clank`,
  alias `.clanker`); `mod.py` is the moderation command set; `modlog.py` is the
  comprehensive mod-logging cog (gateway listeners + `.modlog` controls);
  `setupwiz.py` is the `.init` wizard; `settings.py`/`meta.py` are config + help.
  (The backups/templates/chatlog/sync/import-export cogs live on a separate bot.)
- `clanklib/modlog.py` -- the centralized `ModLogger` (hung off the bot as
  `bot.modlog`). Every cog routes events through it (`modlog.security(...)`,
  `.mod(...)`, `.member(...)`, `.config(...)` ...); it persists to
  `mod_log_events` and renders Components V2 panels. New event sources should
  go through it, not ad-hoc channel sends.
- `database/` -- a **slim** data plane (no economy): `database.py`
  (`PgDatabase`: pool, file migration runner, query helpers, the in-process
  guild-settings cache, and the `guilds` repo), `base.py` (`PgBaseRepo`), and
  `migrations/*.sql` (`0001` guild settings + the `0285+` clank tables + the
  `0300+` mod-log tables). The framework imports `database.Database` lazily.
- `api/v2/main.py` -- `create_app(bot)` FastAPI app the framework auto-mounts
  on `API_PORT`; `/health` is public, the `/api/v2/guilds/*` settings routes
  need `X-API-Key`.

## Conventions

- Plain ASCII in source -- no em/en dashes or Unicode minus signs.
- Use the framework, don't reimplement it: colors and `fmt_*` come from
  `core.framework.ui`; UI from `core.framework.components`; cog bases from
  `core.framework.cogs` (`GuildCog` for guild-only features).
- `log = logging.getLogger(__name__)` -- never `print()`.
- Management commands require a guild permission (`manage_guild` /
  `administrator` / `manage_webhooks`) and the matching `bot_has_guild_permissions`.
- No premium gating anywhere. Any caps are
  abuse-prevention only and configurable.

## Don't break the clank port

`cogs/clank.py` and `database/migrations/02*.sql` were ported from Discoin and
expect `db.get_guild_settings`, `db.update_guild_setting`, `db.fetch_*`,
`db.execute(_many)` and the `clanker_*` / `clank_*` tables. Keep those method
names and the migration set intact.
