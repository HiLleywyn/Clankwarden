"""clanklib/guild_schema.py -- the per-guild settings contract.

One declarative description of which guild settings an operator can edit, what
type each is, and how to validate/coerce a submitted value. Shared by:

* the guild-settings REST API (``api/v2``) -- so a web panel renders + validates
  the same fields, and
* the in-Discord ``.set`` command -- so both surfaces agree.

A field maps to either a real ``guild_settings`` column or a key inside the
``features`` JSONB (the DB layer routes it by name). Channel/role types are
stored as ``BIGINT`` ids; ``string``/``bool`` are stored as-is.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class GuildField:
    key: str
    type: str            # string | bool | discord_channel | discord_role | number
    label: str
    help: str = ""
    max_len: Optional[int] = None
    min: Optional[int] = None
    max: Optional[int] = None


# The editable per-guild surface. Keep this aligned with the columns the DB
# layer recognises (database.database._GUILD_SETTING_COLUMNS) plus any JSONB
# feature keys we want exposed.
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
    GuildField("clank_default_role", "discord_role", "Default restore role", "Safe baseline role handed back when a legacy clanker (no stored roles) is released."),
    GuildField("clank_tank_board", "bool", "Tank Board + animations", "Public depth board in the clanktank channel plus descent/ascent animations on level changes."),
    GuildField("dehoist_enabled", "bool", "Smart dehoist enabled", "Scan members against this server's impersonation signals and act automatically."),
    GuildField("dehoist_mode", "string", "Dehoist mode", "off, warn, rename, or rename_clank (rename the impersonator AND auto-clank them).", max_len=12),
    GuildField("dehoist_floor_role", "discord_role", "Dehoist floor role", "Only members at or below this role are eligible; staff/trusted roles are never touched."),
    GuildField("dehoist_log_channel", "discord_channel", "Dehoist log channel", "Where dehoist alerts post. Falls back to the Clanker log channel."),
    GuildField("dehoist_triggers", "string", "Dehoist triggers", "Comma list of events that run the check: join, message, update.", max_len=40),
    GuildField("dehoist_topics", "string", "Dehoist extra topics", "Optional comma list of extra topic keywords to seed the impersonation lexicon.", max_len=200),
    GuildField("autodelete_replies", "number", "Auto-delete command replies (seconds)", "Self-delete command/mod-command reply panels after N seconds. 0 = keep them.", min=0, max=3600),
    GuildField("autodelete_info", "number", "Auto-delete info panels (seconds)", "Self-delete informational panels (.help/.about/.settings) after N seconds. 0 = keep them. Escape-room messages are never deleted.", min=0, max=86400),
)

FIELDS_BY_KEY: dict[str, GuildField] = {f.key: f for f in GUILD_FIELDS}


class GuildSettingError(ValueError):
    """A submitted guild-setting value failed validation."""


def coerce_guild_value(field: GuildField, raw: Any) -> Any:
    """Validate + coerce one value for ``field``. ``None``/empty clears it.

    Raises :class:`GuildSettingError` with a human message on a bad value."""
    if raw is None:
        return None
    if field.type == "string":
        s = str(raw).strip()
        if s == "":
            return None
        if field.max_len and len(s) > field.max_len:
            raise GuildSettingError(f"{field.label}: must be {field.max_len} characters or fewer.")
        return s
    if field.type == "bool":
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}
    if field.type == "number":
        try:
            n = int(raw)
        except (TypeError, ValueError):
            raise GuildSettingError(f"{field.label}: must be a whole number.")
        if field.min is not None and n < field.min:
            raise GuildSettingError(f"{field.label}: must be >= {field.min}.")
        if field.max is not None and n > field.max:
            raise GuildSettingError(f"{field.label}: must be <= {field.max}.")
        return n
    if field.type in ("discord_channel", "discord_role"):
        # Accept a raw id, a <#id>/<@&id> mention, or empty to clear.
        s = str(raw).strip()
        if s in ("", "none", "off", "clear", "unset"):
            return None
        digits = "".join(ch for ch in s if ch.isdigit())
        if not digits:
            raise GuildSettingError(f"{field.label}: give a channel/role id or mention.")
        return int(digits)
    raise GuildSettingError(f"{field.label}: unknown field type {field.type!r}.")


def validate_guild_settings(submitted: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Validate a dict of ``{key: value}`` against :data:`GUILD_FIELDS`.

    Returns ``(coerced, errors)``. Unknown keys are reported as errors so a
    typo fails loudly rather than silently writing into the JSONB blob."""
    coerced: dict[str, Any] = {}
    errors: list[str] = []
    for key, raw in submitted.items():
        field = FIELDS_BY_KEY.get(key)
        if field is None:
            errors.append(f"unknown setting {key!r}")
            continue
        try:
            coerced[key] = coerce_guild_value(field, raw)
        except GuildSettingError as exc:
            errors.append(str(exc))
    return coerced, errors


def schema_json() -> dict[str, Any]:
    """Render the editable fields for a web UI."""
    return {
        "fields": [
            {"key": f.key, "type": f.type, "label": f.label, "help": f.help,
             "max_len": f.max_len, "min": f.min, "max": f.max}
            for f in GUILD_FIELDS
        ]
    }


def public_view(row: dict[str, Any]) -> dict[str, Any]:
    """Project a ``get_guild_settings`` row down to just the editable fields
    (so the API never leaks internal columns)."""
    out: dict[str, Any] = {"guild_id": row.get("guild_id")}
    for f in GUILD_FIELDS:
        out[f.key] = row.get(f.key)
    return out
