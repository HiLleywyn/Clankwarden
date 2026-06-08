"""Unit tests for the 5-level clanktank depth model (pure helpers in cogs.clank)."""
from __future__ import annotations

import asyncio
import types
from datetime import datetime, timedelta, timezone

import discord  # noqa: F401  -- Components V2 era discord.py
import pytest

# clank.py imports the framework; skip on the dependency-light CI job.
pytest.importorskip("core.framework.ui")

import cogs.clank as clank  # noqa: E402


def test_levels_cover_one_through_five():
    assert set(clank._ER_LEVEL_STATIONS) == {1, 2, 3, 4, 5}


def test_every_station_key_has_a_builder_and_display_name():
    # Builders are bound in _rebuild; the display-name map is the stable registry.
    known = set(clank._STATION_DISPLAY_NAMES)
    for level, stations in clank._ER_LEVEL_STATIONS.items():
        for key in stations:
            assert key in known, f"L{level} station {key!r} has no display name"


def test_level_1_is_a_short_education_gate():
    assert clank._ER_LEVEL_STATIONS[1] == ("education",)


def test_deeper_levels_are_at_least_as_long():
    lengths = [len(clank._ER_LEVEL_STATIONS[lv]) for lv in range(1, 6)]
    assert lengths == sorted(lengths), "deeper levels must not be shorter"
    assert lengths[-1] == 8  # L5 is the full gauntlet


def test_clamp_level_bounds():
    assert clank._clamp_level(0) == 1
    assert clank._clamp_level(-3) == 1
    assert clank._clamp_level(99) == 5
    assert clank._clamp_level(3) == 3
    assert clank._clamp_level("notanumber") == 1
    assert clank._clamp_level(None) == 1


def test_reflect_wait_scales_with_level_and_rust_and_clamps():
    # base 5: L1 -> 5, L3 -> 15, plus rust on top.
    assert clank._reflect_wait(5, 1, 0) == 5
    assert clank._reflect_wait(5, 3, 0) == 15
    assert clank._reflect_wait(5, 3, 2) == 17
    # never below 1, never above 120.
    assert clank._reflect_wait(0, 1, 0) == 1
    assert clank._reflect_wait(100, 5, 50) == 120


def test_hint_is_level_and_station_aware():
    h = clank._er_hint(3, 0)
    assert "Level 3/5" in h and "Station 1/" in h
    # past the last station -> cleared message
    cleared = clank._er_hint(1, 5)
    assert "cleared" in cleared.lower()


def test_depth_ladder_marks_current_level():
    assert "[3]" in clank._depth_ladder(3)
    assert "[1]" in clank._depth_ladder(1)


def test_level_5_is_the_highest_tier_clankermax():
    # L5 is the highest/worst tier; L1 is the lowest. The gauge must list L5 at
    # the top (max containment) and L1 at the bottom (release).
    assert clank._LEVEL_NAMES[5] == "CLANKERMAX"
    gauge = clank._depth_gauge(3)
    lines = [ln for ln in gauge.splitlines() if ln.strip().startswith("L")]
    # First level line is L5, last is L1.
    assert lines[0].strip().startswith("L5")
    assert lines[-1].strip().startswith("L1")
    # Ladder reads release (low) -> Clankermax (high).
    ladder = clank._depth_ladder(3)
    assert ladder.index("release") < ladder.index("Clankermax")


def test_level_stations_helper_matches_table():
    assert clank._level_stations(4) == clank._ER_LEVEL_STATIONS[4]
    # out-of-range falls back to the deepest level
    assert clank._level_stations(99) == clank._ER_LEVEL_STATIONS[5]


# -- Admin immunity in _do_clank ---------------------------------------------

def _fake_member(*, member_id: int, admin: bool, owner_id: int):
    guild = types.SimpleNamespace(id=99, owner_id=owner_id)
    perms = types.SimpleNamespace(administrator=admin)
    return types.SimpleNamespace(
        id=member_id, guild=guild, guild_permissions=perms,
        # __str__ via SimpleNamespace falls back to repr; that is fine for the
        # error message assertions below.
    )


def test_do_clank_refuses_an_administrator():
    cog = clank.Clanktank.__new__(clank.Clanktank)
    member = _fake_member(member_id=2, admin=True, owner_id=1)
    with pytest.raises(ValueError) as exc:
        asyncio.run(cog._do_clank(member, member, "spam", None))
    assert "administrator" in str(exc.value).lower()


def test_do_clank_refuses_the_server_owner_even_without_admin_flag():
    cog = clank.Clanktank.__new__(clank.Clanktank)
    member = _fake_member(member_id=1, admin=False, owner_id=1)
    with pytest.raises(ValueError) as exc:
        asyncio.run(cog._do_clank(member, member, "spam", None))
    assert "immune" in str(exc.value).lower()


def test_do_clank_lets_a_normal_member_past_the_admin_guard():
    # A non-admin, non-owner member must clear the immunity guard. We stub the
    # next step (_clanker_role) so the call fails for a DIFFERENT, later reason,
    # proving the admin guard did not fire.
    cog = clank.Clanktank.__new__(clank.Clanktank)

    async def _no_role(_guild):
        return None

    cog._clanker_role = _no_role
    member = _fake_member(member_id=2, admin=False, owner_id=1)
    with pytest.raises(ValueError) as exc:
        asyncio.run(cog._do_clank(member, member, "spam", None))
    msg = str(exc.value).lower()
    assert "administrator" not in msg and "immune" not in msg


# -- Pause DMs arming contract -----------------------------------------------

def _fake_guild_with_edit(dms_paused_until):
    edits = []

    async def _edit(**kwargs):
        edits.append(kwargs)

    guild = types.SimpleNamespace(
        id=7, dms_paused_until=dms_paused_until, edit=_edit,
    )
    return guild, edits


def test_ensure_dms_paused_is_a_noop_when_already_armed_with_headroom():
    # Regression: an already-armed pause must report success (True) WITHOUT
    # issuing another edit -- previously this returned False and the command
    # mis-reported it as "could not arm".
    cog = clank.Clanktank.__new__(clank.Clanktank)
    future = datetime.now(timezone.utc) + timedelta(hours=20)
    guild, edits = _fake_guild_with_edit(future)
    assert asyncio.run(cog._ensure_dms_paused(guild)) is True
    assert edits == []  # no API call -- still armed


def test_ensure_dms_paused_arms_when_unset():
    cog = clank.Clanktank.__new__(clank.Clanktank)
    guild, edits = _fake_guild_with_edit(None)
    assert asyncio.run(cog._ensure_dms_paused(guild)) is True
    assert len(edits) == 1 and "dms_disabled_until" in edits[0]


def test_ensure_dms_paused_re_arms_when_window_is_nearly_lapsed():
    cog = clank.Clanktank.__new__(clank.Clanktank)
    soon = datetime.now(timezone.utc) + timedelta(hours=1)  # below the 12h floor
    guild, edits = _fake_guild_with_edit(soon)
    assert asyncio.run(cog._ensure_dms_paused(guild)) is True
    assert len(edits) == 1


def test_ensure_dms_paused_remembers_what_it_armed_and_does_not_re_arm():
    # Regression: discord.py clobbers guild.dms_paused_until to None on any
    # unrelated GUILD_UPDATE. After we arm, the next sweep must trust our own
    # remembered expiry and NOT issue a redundant edit just because the gateway
    # value went stale.
    cog = clank.Clanktank.__new__(clank.Clanktank)
    guild, edits = _fake_guild_with_edit(None)
    assert asyncio.run(cog._ensure_dms_paused(guild)) is True  # arms
    assert len(edits) == 1
    guild.dms_paused_until = None  # simulate the gateway clobbering it back
    assert asyncio.run(cog._ensure_dms_paused(guild)) is True  # still armed
    assert len(edits) == 1  # no second edit -- remembered the fresh window


def test_ensure_dms_paused_re_arms_when_remembered_window_lapses():
    # The remembered expiry must still let the window lapse into re-arm range:
    # a stale-but-soon remembered value re-arms just like the gateway one.
    cog = clank.Clanktank.__new__(clank.Clanktank)
    cog._pause_dms_armed_until = {7: datetime.now(timezone.utc) + timedelta(hours=1)}
    guild, edits = _fake_guild_with_edit(None)
    assert asyncio.run(cog._ensure_dms_paused(guild)) is True
    assert len(edits) == 1


def test_ensure_dms_paused_propagates_api_errors():
    cog = clank.Clanktank.__new__(clank.Clanktank)

    async def _boom(**kwargs):
        raise RuntimeError("discord said no")

    guild = types.SimpleNamespace(id=7, dms_paused_until=None, edit=_boom)
    with pytest.raises(RuntimeError):
        asyncio.run(cog._ensure_dms_paused(guild))
