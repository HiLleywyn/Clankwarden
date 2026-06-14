"""clanklib/guild_schema.py -- Clankwarden's per-guild settings *registry*.

One declarative description of which guild settings an operator can edit, what
type each is, and how to validate/coerce a submitted value. Shared by:

* the guild-settings REST API (``api/v2``) -- so a web panel renders + validates
  the same fields, and
* the in-Discord ``.set`` command -- so both surfaces agree.

The validate/coerce/project *engine* is bot-agnostic and lives in the framework
(``core.framework.guildtools.guild_schema``). This module owns the
Clankwarden-specific ``GUILD_FIELDS`` registry, binds it to that engine, and
re-exports the no-``fields``-argument convenience API the cogs and API already
call. A field maps to either a real ``guild_settings`` column or a key inside the
``features`` JSONB (the DB layer routes it by name).
"""
from __future__ import annotations

from typing import Any

from core.framework.guildtools.guild_schema import (
    GuildField,
    GuildSettingError,
    coerce_guild_value,
    fields_by_key,
    public_view as _public_view,
    schema_json as _schema_json,
    validate_guild_settings as _validate_guild_settings,
)

__all__ = [
    "GuildField",
    "GuildSettingError",
    "coerce_guild_value",
    "GUILD_FIELDS",
    "FIELDS_BY_KEY",
    "validate_guild_settings",
    "schema_json",
    "public_view",
]


# The editable per-guild surface. Keep this aligned with the columns the DB
# layer recognises (database.database._GUILD_SETTING_COLUMNS) plus any JSONB
# feature keys we want exposed. This registry is Clankwarden policy; the engine
# that validates/coerces/projects it is shared by every framework bot.
GUILD_FIELDS: tuple[GuildField, ...] = (
    GuildField("prefix", "string", "Command prefix", "1-5 chars; overrides the global prefix in this server.", max_len=5),
    GuildField("log_channel", "discord_channel", "Log channel", "Where the bot posts general audit/event logs."),
    GuildField("mod_log_channel", "discord_channel", "Mod log channel", "Comprehensive moderation log for every tracked event."),
    GuildField("clanker_role", "discord_role", "Clanker role", "The containment role applied to clanked users."),
    GuildField("clank_category", "discord_channel", "Clanker category", "Category the containment channels live under."),
    GuildField("clanktank_channel", "discord_channel", "Clanktank channel", "The .clank tank channel clankers are confined to."),
    GuildField("clanktank_log_channel", "discord_channel", "Clanker log channel", "Log channel for containment (clank/escape) events."),
    GuildField("clank_escape_thread", "discord_channel", "Escape-room thread", "Shared .clank escape-room thread."),
    GuildField("clank_escape_wait_minutes", "number", "Reflection period (minutes)", "Mandatory reflection wait before an escape unlocks.", min=1, max=120),
    GuildField("scam_hunter_role", "discord_role", "Clanker hunter role", "Members of this role can report scammers in the hunter channel."),
    GuildField("scam_report_channel", "discord_channel", "Clanker hunter channel", "Channel where hunters post reports that auto-clank flagged users."),
    GuildField("clankermax_role", "discord_role", "Clankermax role", "Added when a clanker sinks to depth L5 (confirmed scammer); removed when they rise above L5."),
    GuildField("name_blacklist", "string", "Join-name blacklist", "Comma-separated name patterns. Anyone who joins with a name containing one is auto-clanked to L5 (Clankermax). Case-insensitive substring match.", max_len=2000),
    GuildField("clank_default_role", "discord_role", "Default restore role", "Safe baseline role handed back when a legacy clanker (no stored roles) is released."),
    GuildField("clank_tank_board", "bool", "Tank Board + animations", "Public depth board in the clanktank channel plus descent/ascent animations on level changes."),
    GuildField("security_pause_dms", "bool", "Auto Pause DMs (Security Action)", "Keep Discord's server-wide 'Pause DMs' security action permanently on. Discord caps each pause at 24h; the bot re-arms it automatically so you enable it once and forget it. Requires the bot to have Manage Server."),
    GuildField("dehoist_enabled", "bool", "Smart dehoist enabled", "Scan members against this server's impersonation signals and act automatically."),
    GuildField("dehoist_mode", "string", "Dehoist mode", "off, warn, rename, or rename_clank (rename the impersonator AND auto-clank them).", max_len=12),
    GuildField("dehoist_floor_role", "discord_role", "Dehoist floor role", "Only members at or below this role are eligible; staff/trusted roles are never touched."),
    GuildField("dehoist_log_channel", "discord_channel", "Dehoist log channel", "Where dehoist alerts post. Falls back to the Clanker log channel."),
    GuildField("dehoist_triggers", "string", "Dehoist triggers", "Comma list of events that run the check: join, message, update.", max_len=40),
    GuildField("dehoist_topics", "string", "Dehoist extra topics", "Optional comma list of extra topic keywords to seed the impersonation lexicon.", max_len=200),
    GuildField("autodelete_replies", "number", "Auto-delete command replies (seconds)", "Self-delete command/mod-command reply panels after N seconds. 0 = keep them.", min=0, max=3600),
    GuildField("autodelete_info", "number", "Auto-delete info panels (seconds)", "Self-delete informational panels (.help/.about/.settings) after N seconds. 0 = keep them. Escape-room messages are never deleted.", min=0, max=86400),
)

FIELDS_BY_KEY: dict[str, GuildField] = fields_by_key(GUILD_FIELDS)


def validate_guild_settings(submitted: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Validate a dict of ``{key: value}`` against :data:`GUILD_FIELDS`."""
    return _validate_guild_settings(submitted, GUILD_FIELDS)


def schema_json() -> dict[str, Any]:
    """Render the editable fields for a web UI."""
    return _schema_json(GUILD_FIELDS)


def public_view(row: dict[str, Any]) -> dict[str, Any]:
    """Project a ``get_guild_settings`` row down to just the editable fields."""
    return _public_view(row, GUILD_FIELDS)
