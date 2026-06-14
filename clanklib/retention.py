"""clanklib/retention.py -- Clankwarden's data-deletion registry.

Declares which tables hold this bot's guild/user data and binds them to the
framework's scoped-deletion engine (``core.framework.guildtools.retention``).
Cogs call :func:`purge_guild_data` from ``on_guild_remove`` (so a server's data
goes when the bot is removed) and :func:`purge_user_data` to forget one user on
request -- both Discord Developer Policy expectations.

The table list is Clankwarden policy; the delete-and-count mechanic is shared.
Keep this aligned with ``database/migrations/*.sql``.
"""
from __future__ import annotations

from typing import Any

from core.framework.guildtools.retention import (
    ScopedTable,
    purge_guild as _purge_guild,
    purge_user as _purge_user,
    total_rows,
)

__all__ = ["CLANK_TABLES", "purge_guild_data", "purge_user_data", "total_rows"]


# Every persisted table that holds guild-scoped (and, where noted, user-scoped)
# data. ``clanker_cluster_members`` is listed before ``clanker_clusters`` so a
# guild purge reports exact counts (deleting the parent cascades to members).
CLANK_TABLES: tuple[ScopedTable, ...] = (
    ScopedTable("clanker_evidence", "guild_id", ("user_id",)),           # stored message content
    ScopedTable("clanker_connections", "guild_id", ("user_id_a", "user_id_b")),
    ScopedTable("clanker_audit_log", "guild_id", ("user_id",)),
    ScopedTable("clanker_cluster_members", "guild_id", ("user_id",)),
    ScopedTable("clanker_clusters", "guild_id"),
    ScopedTable("clanker_patterns", "guild_id"),
    ScopedTable("clanker_history", "guild_id", ("user_id",)),
    ScopedTable("clank_escape", "guild_id", ("user_id",)),
    ScopedTable("clank_case_counter", "guild_id"),
    ScopedTable("clanker_records", "guild_id", ("user_id",)),
    ScopedTable("mod_warnings", "guild_id", ("user_id",)),
    ScopedTable("mod_log_events", "guild_id", ("actor_id", "target_id")),
    ScopedTable("dehoist_events", "guild_id", ("user_id",)),
    ScopedTable("dehoist_signals", "guild_id"),
    ScopedTable("guild_settings", "guild_id"),
)


# ``mod_log_events`` is a tamper-evident HMAC chain: deleting individual rows
# from the middle would break ``.modlog verify``. A per-user forget therefore
# retains the audit log by default (a legitimate-interest record of moderation
# actions) and only erases the user's containment/evidence/profile data. A full
# guild purge on removal deletes everything, the chain included.
_AUDIT_TABLES: frozenset[str] = frozenset({"mod_log_events"})
USER_FORGET_TABLES: tuple[ScopedTable, ...] = tuple(
    t for t in CLANK_TABLES if t.table not in _AUDIT_TABLES
)


async def purge_guild_data(db: Any, guild_id: int) -> dict[str, int]:
    """Delete all of one guild's stored data. Returns ``{table: rows_deleted}``."""
    return await _purge_guild(db, CLANK_TABLES, guild_id)


async def purge_user_data(
    db: Any, user_id: int, guild_id: int | None = None, *, include_audit: bool = False
) -> dict[str, int]:
    """Delete one user's data (in ``guild_id`` when given, else everywhere).

    By default the tamper-evident mod-log audit chain is retained; pass
    ``include_audit=True`` to erase the user's audit rows too (which breaks
    chain verification from that point)."""
    tables = CLANK_TABLES if include_audit else USER_FORGET_TABLES
    return await _purge_user(db, tables, user_id, guild_id)
