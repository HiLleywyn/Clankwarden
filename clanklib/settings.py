"""clanklib/settings.py -- live runtime-config resolution for every cog.

The resolution engine (per-guild override -> live ``bot.settings`` -> env ->
default) is bot-agnostic and lives in the framework at
``core.framework.guildtools.settings``. This module is a thin re-export so the
bot's cogs keep importing ``from clanklib.settings import cfg, setting`` while
there is exactly one implementation shared with every other framework bot.

Precedence, highest first:

1. Per-guild override stored in ``guild_settings`` (set via ``.set ...``).
2. The bot's live ``Settings`` object (``bot.settings``), refreshed by the Auren
   control link on every heartbeat.
3. The matching environment variable.
4. The caller's default.
"""
from __future__ import annotations

from core.framework.guildtools.settings import (
    GuildConfig,
    cfg,
    prefix,
    setting,
    setting_int,
)

__all__ = ["GuildConfig", "cfg", "prefix", "setting", "setting_int"]
