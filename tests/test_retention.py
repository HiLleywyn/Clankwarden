"""Tests for clanklib.retention -- Clankwarden's data-deletion registry.

These pin the policy: a guild purge hits every stored table by guild_id; a
per-user forget skips the tamper-evident mod-log audit chain unless explicitly
told to include it. A fake db records SQL without a real Postgres.
"""
from __future__ import annotations

import pytest

# clanklib.retention binds the framework's deletion engine; skip when the
# framework isn't installed (the deps-only CI job), like the cog tests.
pytest.importorskip("core.framework.guildtools.retention")

from clanklib import retention as R


class _FakeDB:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def execute(self, query: str, *args):
        self.calls.append((query, args))
        return "DELETE 1"


def test_registry_covers_every_persisted_table():
    names = {t.table for t in R.CLANK_TABLES}
    # The guild-scoped tables from database/migrations/*.sql.
    assert names == {
        "guild_settings", "clanker_records", "clanker_evidence",
        "clanker_connections", "clanker_audit_log", "clanker_clusters",
        "clanker_cluster_members", "clanker_patterns", "clanker_history",
        "clank_escape", "clank_case_counter", "mod_log_events",
        "mod_warnings", "dehoist_events", "dehoist_signals",
    }


def test_cluster_members_purged_before_parent():
    order = [t.table for t in R.CLANK_TABLES]
    assert order.index("clanker_cluster_members") < order.index("clanker_clusters")


@pytest.mark.asyncio
async def test_guild_purge_scopes_every_table_to_guild():
    db = _FakeDB()
    counts = await R.purge_guild_data(db, 99)
    assert set(counts) == {t.table for t in R.CLANK_TABLES}
    for sql, args in db.calls:
        assert "WHERE guild_id = $1" in sql and args == (99,)


@pytest.mark.asyncio
async def test_prune_evidence_targets_clanker_evidence_by_age():
    db = _FakeDB()
    await R.prune_evidence(db, 90)
    assert len(db.calls) == 1
    sql, args = db.calls[0]
    assert "DELETE FROM clanker_evidence" in sql and "logged_at < NOW()" in sql
    assert args == ("90 days",)


@pytest.mark.asyncio
async def test_prune_evidence_disabled_keeps_everything():
    db = _FakeDB()
    assert await R.prune_evidence(db, 0) == 0      # 0 days = retention disabled
    assert db.calls == []


@pytest.mark.asyncio
async def test_user_forget_retains_audit_chain_by_default():
    db = _FakeDB()
    counts = await R.purge_user_data(db, user_id=5, guild_id=99)
    assert "mod_log_events" not in counts          # audit chain retained
    assert "clanker_evidence" in counts            # message-content evidence erased
    # include_audit=True erases the audit rows too
    db2 = _FakeDB()
    counts2 = await R.purge_user_data(db2, user_id=5, guild_id=99, include_audit=True)
    assert "mod_log_events" in counts2
