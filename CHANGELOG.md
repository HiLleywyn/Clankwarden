# Changelog

## [main] -- 2026-06-01

### New Features
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
- Installation guide (local / Docker / Railway / Sojourns), full configuration
  reference and a complete command reference.

### Changes
- Components V2 is the default UI across every command.
- Templated for Sojourns via `sojourns.json` (validated in CI).
