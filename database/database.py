"""database/database.py -- Clanksimus Prime's slim data plane.

The framework instantiates ``database.Database`` lazily (see
``core.framework.bot.FrameworkBot._resolve_db_factory``) and calls
``connect()`` once at boot. Unlike Disco's economy data plane, this one is
deliberately small: a connection pool, a file-based migration runner, the
query helpers every cog/repo uses, per-guild settings, and a handful of
feature repos (backups, templates, chatlog, sync) reached as attributes
(``bot.db.backups`` ...).

JSONB columns are stored as text + ``::jsonb`` cast on write and decoded
with ``json.loads`` on read, because the pool installs no JSON codec.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import asyncpg

from core.database import _coerce, _row, _rows, create_pool

log = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


class PgDatabase:
    """asyncpg-backed data plane for Clanksimus Prime."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None
        self._repos: dict[str, Any] = {}

    # -- lifecycle -------------------------------------------------------------

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("Database.connect() has not been called yet")
        return self._pool

    async def connect(self) -> None:
        self._pool = await create_pool(self._dsn)
        await self._ensure_migrations_table()
        await self.run_migrations()

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    # -- query helpers (mirror PgBaseRepo so cogs can call db.* directly) ------

    async def fetch_one(self, query: str, *args: Any) -> dict | None:
        async with self.pool.acquire() as conn:
            return _row(await conn.fetchrow(query, *args))

    async def fetch_all(self, query: str, *args: Any) -> list[dict]:
        async with self.pool.acquire() as conn:
            return _rows(await conn.fetch(query, *args))

    async def fetch_val(self, query: str, *args: Any) -> Any:
        async with self.pool.acquire() as conn:
            return _coerce(await conn.fetchval(query, *args))

    async def execute(self, query: str, *args: Any) -> str:
        async with self.pool.acquire() as conn:
            return await conn.execute(query, *args)

    async def executemany(self, query: str, args_list: list[tuple]) -> None:
        async with self.pool.acquire() as conn:
            await conn.executemany(query, args_list)

    # Some callers (the ported clank cog) use the underscored name.
    execute_many = executemany

    # -- migrations ------------------------------------------------------------

    async def _ensure_migrations_table(self) -> None:
        await self.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "  filename   TEXT PRIMARY KEY,"
            "  applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
            ")"
        )

    async def run_migrations(self) -> int:
        """Apply every ``database/migrations/*.sql`` not yet recorded, in
        filename order, each in its own transaction. Returns the count
        applied."""
        rows = await self.fetch_all("SELECT filename FROM schema_migrations")
        applied = {r["filename"] for r in rows}
        files = sorted(_MIGRATIONS_DIR.glob("*.sql"), key=lambda p: p.name)
        n = 0
        for path in files:
            if path.name in applied:
                continue
            sql = path.read_text(encoding="utf-8")
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(sql)
                    await conn.execute(
                        "INSERT INTO schema_migrations (filename) VALUES ($1)",
                        path.name,
                    )
            n += 1
            log.info("applied migration %s", path.name)
        if n:
            log.info("ran %d migration(s)", n)
        return n

    # -- guild settings --------------------------------------------------------

    async def get_guild_settings(self, guild_id: int) -> dict:
        """Return a guild's settings row as a flat dict.

        The ``features`` JSONB is decoded and its keys are folded up to the
        top level so ``settings.get("welcome")`` works alongside real columns
        (``prefix``, ``bot_channels`` ...). Clank's containment channels fall
        back to the matching env vars when unset, matching Disco's behaviour.
        """
        gid = int(guild_id)
        row = await self.fetch_one("SELECT * FROM guild_settings WHERE guild_id = $1", gid)
        if row is None:
            await self.execute(
                "INSERT INTO guild_settings (guild_id) VALUES ($1) ON CONFLICT DO NOTHING",
                gid,
            )
            row = await self.fetch_one(
                "SELECT * FROM guild_settings WHERE guild_id = $1", gid
            ) or {"guild_id": gid}

        d: dict[str, Any] = dict(row)
        feats = d.get("features")
        if isinstance(feats, str):
            try:
                feats = json.loads(feats)
            except (TypeError, ValueError):
                feats = {}
        if isinstance(feats, dict):
            d["features"] = feats
            for k, v in feats.items():
                d.setdefault(k, v)
        else:
            d["features"] = {}

        for col, env in (
            ("clanktank_channel", "CLANKTANK_CHANNEL_ID"),
            ("clanktank_log_channel", "CLANKTANK_LOG_CHANNEL_ID"),
        ):
            if not d.get(col):
                d[col] = int(os.getenv(env) or 0) or None
        return d

    async def update_guild_setting(self, guild_id: int, key: str, value: Any) -> None:
        """Set a real column (when ``key`` is one) or a ``features`` JSONB key."""
        gid = int(guild_id)
        await self.execute(
            "INSERT INTO guild_settings (guild_id) VALUES ($1) ON CONFLICT DO NOTHING", gid
        )
        if key in _GUILD_SETTING_COLUMNS:
            await self.execute(
                f"UPDATE guild_settings SET {key} = $2, updated_at = NOW() WHERE guild_id = $1",
                gid, value,
            )
        else:
            await self.execute(
                "UPDATE guild_settings "
                "SET features = jsonb_set(COALESCE(features, '{}'::jsonb), $2, $3::jsonb), "
                "    updated_at = NOW() "
                "WHERE guild_id = $1",
                gid, [key], json.dumps(value),
            )

    # -- repo accessors --------------------------------------------------------

    def _repo(self, name: str, factory):  # type: ignore[no-untyped-def]
        repo = self._repos.get(name)
        if repo is None:
            repo = factory(self.pool)
            self._repos[name] = repo
        return repo

    @property
    def guilds(self):  # type: ignore[no-untyped-def]
        from database.guilds import GuildRepo
        return self._repo("guilds", GuildRepo)

    @property
    def backups(self):  # type: ignore[no-untyped-def]
        from database.backups import BackupRepo
        return self._repo("backups", BackupRepo)

    @property
    def templates(self):  # type: ignore[no-untyped-def]
        from database.templates import TemplateRepo
        return self._repo("templates", TemplateRepo)

    @property
    def chatlog(self):  # type: ignore[no-untyped-def]
        from database.chatlog import ChatlogRepo
        return self._repo("chatlog", ChatlogRepo)

    @property
    def sync(self):  # type: ignore[no-untyped-def]
        from database.sync import SyncRepo
        return self._repo("sync", SyncRepo)


# Real columns on guild_settings (everything else goes into the features JSONB).
_GUILD_SETTING_COLUMNS: frozenset[str] = frozenset({
    "prefix", "bot_channels", "log_channel",
    "clanktank_channel", "clanktank_log_channel", "clank_escape_thread",
})


# Module-level singleton for the API layer (the bot uses its own instance).
_DB: PgDatabase | None = None


def get_database(dsn: str | None = None) -> PgDatabase:
    """Return a process-wide :class:`PgDatabase` (used by the API layer)."""
    global _DB
    if _DB is None:
        from core.config import Config
        _DB = PgDatabase(dsn or Config.DATABASE_URL)
    return _DB
