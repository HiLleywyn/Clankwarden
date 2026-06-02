# Clankwarden

A free, modern Discord **moderation + anti-scam bot**: the `.clank`
account-**containment** system, a smart server-aware **dehoister**, a full
**moderation** command set, and tamper-evident **audit logging** -- all rendered
in Discord's **Components V2** UI, with **no premium tiers and no paywalls**.
Built on the shared [`bot framework`](https://github.com/hilleywyn/framework)
and templated for the [Auren](https://github.com/hilleywyn/auren) platform.

> The server-management tooling (backups, templates, chatlogs, sync,
> import/export) lives in a **separate** bot,
> [Recycler](https://github.com/hilleywyn/recycler). This repo is moderation and
> containment only.

## Features

| Area | What it does | Commands |
|---|---|---|
| **Moderation** | Ban, kick, timeout, warn, purge, slowmode, channel lock | `.ban` `.kick` `.timeout` `.warn` `.purge` `.slowmode` `.lock` (+ slash `/ban` `/kick` `/timeout` `/untimeout`) |
| **Containment (`.clank`)** | Scammer/bot-account containment: strip a user's roles, confine them to the tank, capture message evidence, link related accounts into clusters, and run them through the escape room | `.clank @user` `.unclank @user` `.clank list` `.clank info` `.clank scan` `.clank stats` (+ slash `/clank` `/unclank`) |
| **Smart Dehoist** | Learns each server's own impersonation signals (roles, staff, channel topics) and, for members below a configurable floor role, renames + auto-clanks impersonators ("Binance Support", "Crypto Team", leading-symbol hoisters) | `.dehoist` `.dehoist scan` `.dehoist signals` `.dehoist sweep` (+ slash `/report` `/dehoist`) |
| **Mod log** | Categorized, hash-chained (tamper-evident) audit logging with per-category routing, timeline, stats and high-severity alerts | `.modlog` `.modlog timeline` `.modlog stats` `.modlog alert` |
| **Setup** | One-command guided setup: creates the Clanker role, the Clanktank category + channels, the escape-room thread, the clanker-hunter role/channel, and routed mod-log channels | `.init` |
| **Settings** | Per-guild configuration in a Components V2 panel (also editable in the Auren web UI) | `.settings` `.set <option> <value>` |
| **Help** | One dynamic help hub, generated live from the command tree | `.help` (+ slash `/help`) |
| **REST API** | Read/update per-guild settings over HTTP | `GET/POST /api/v2/...` (see docs) |

Containment, dehoist renames and `/clank` are restricted to the mod team (and,
for `/clank`/`/unclank`, the configurable clanker-hunter role). The invite asks
only for the permissions the bot actually uses -- **never Administrator**.

## Quick start

```bash
git clone https://github.com/hilleywyn/clankwarden
cd clankwarden
cp .env.example .env          # fill in DISCORD_TOKEN + DATABASE_URL
# install the framework + deps, then run:
pip install "bot-framework @ git+https://github.com/hilleywyn/framework.git@main"
pip install -r requirements.txt
python main.py
```

Or build the container (Railway-ready -- no build args needed):

```bash
docker build -t clankwarden .
docker run --env-file .env -p 8080:8080 clankwarden
```

Once it's running, type `.init` in your server for guided setup, then `.help`
for the full command list.

## Documentation

- **[`docs/deployment.md`](docs/deployment.md)** -- the thick, end-to-end
  deployment guide: prerequisites and all four pathways (local/bare-metal,
  Docker, Railway, Auren), the invite link, post-deploy verification,
  upgrades/rollback and troubleshooting. **Start here.**
- [`docs/configuration.md`](docs/configuration.md) -- every environment
  variable, grouped by feature, with defaults and effects.
- [`docs/commands.md`](docs/commands.md) -- the complete command reference.
- [`docs/install.md`](docs/install.md) -- a condensed install quick-start.

## How it's built

`main.py` boots from `auren.json` through the framework's shared runtime
(`run_manifest`). The manifest's `features` list is the set of cogs to load and
doubles as the deployment contract the Auren control plane reads. The data
plane is a slim, economy-free Postgres layer with a file-based migration runner.
The smart-dehoist detector (`clanklib/dehoist.py`) is pure and unit-tested; the
UI is Components V2 throughout (`core.framework.components`).

See [`CLAUDE.md`](CLAUDE.md) for contributor conventions.

## License

Free to self-host and modify.
