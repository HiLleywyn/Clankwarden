"""Unit tests for the centralized mod logger's pure logic (no bot/db)."""
from __future__ import annotations

import discord  # noqa: F401  -- Components V2 era discord.py
import pytest

# modlog imports the framework UI palette; skip on the dependency-light CI job.
pytest.importorskip("core.framework.ui")

from clanklib import modlog  # noqa: E402
from clanklib.modlog import Category, LogEvent, Severity  # noqa: E402


def test_severity_rank_is_monotonic() -> None:
    order = [Severity.INFO, Severity.NOTICE, Severity.WARNING, Severity.ALERT, Severity.CRITICAL]
    ranks = [s.rank for s in order]
    assert ranks == sorted(ranks)
    assert len(set(ranks)) == len(ranks)


def test_every_severity_has_a_colour() -> None:
    for s in Severity:
        assert isinstance(s.color, int)


def test_category_names_cover_the_enum() -> None:
    assert set(modlog.CATEGORY_NAMES) == {c.value for c in Category}
    # the categories the user asked for are all present
    for needed in ("security", "moderation", "member", "message", "role",
                   "channel", "command", "ai", "infrastructure", "clanktank", "analytics"):
        assert needed in modlog.CATEGORY_NAMES


def test_event_id_is_prefixed_and_unique() -> None:
    a = LogEvent(category=Category.SECURITY, event_type="x", guild_id=1)
    b = LogEvent(category=Category.SECURITY, event_type="x", guild_id=1)
    assert a.event_id.startswith("evt_")
    assert a.event_id != b.event_id


def test_id_coercion_from_objects_and_ints() -> None:
    class _Obj:
        id = 42
    ev = LogEvent(category=Category.MEMBER, event_type="t", guild_id=7,
                  actor=_Obj(), target=99, channel=None)
    assert ev.actor_id == 42
    assert ev.target_id == 99
    assert ev.channel_id is None


def test_metadata_formatting_truncates_and_titlecases() -> None:
    out = modlog._format_metadata({"some_key": "value", "empty": ""})
    assert "**Some Key**" in out
    assert "value" in out
    # empty values are dropped
    assert "Empty" not in out


def test_humanize_type_is_readable() -> None:
    assert modlog._humanize_type("member.role_update") == "Member - Role Update"


def test_event_hash_is_deterministic_and_chains() -> None:
    logger = modlog.ModLogger(bot=None)
    ev = LogEvent(category=Category.MODERATION, event_type="member.ban", guild_id=1,
                  severity=Severity.ALERT, target=42, summary="x")
    h1 = logger._event_hash("", ev)
    h2 = logger._event_hash("", ev)
    assert h1 == h2 and len(h1) == 64
    # A different previous hash yields a different digest (chaining).
    assert logger._event_hash("deadbeef", ev) != h1


def test_anomaly_window_fires_once_at_threshold() -> None:
    logger = modlog.ModLogger(bot=None)
    key = (1, "join")
    counts = [logger._bump_window(key, 60.0) for _ in range(5)]
    assert counts == [1, 2, 3, 4, 5]
    # The detector fires exactly when n == threshold, so callers compare to it.


def test_render_tolerates_float_created_at() -> None:
    # The data plane returns timestamps as epoch floats; rebuilding an event
    # from a row (e.g. .modlog case) must not assume a datetime.
    logger = modlog.ModLogger(bot=None)
    ev = LogEvent(category=Category.MODERATION, event_type="member.ban", guild_id=1,
                  severity=Severity.ALERT, target=42, summary="x",
                  created_at=1_700_000_000.0)
    view = logger.render(ev)  # must not raise
    assert isinstance(view, discord.ui.LayoutView)
    # Hash + epoch helpers also tolerate the float.
    assert modlog._epoch(1_700_000_000.0) == 1_700_000_000
    assert len(logger._event_hash("", ev)) == 64


def test_ignored_channel_ids_coerces_and_drops_junk() -> None:
    # JSONB round-trips can yield ints or strings; both coerce, junk is dropped.
    assert modlog._ignored_channel_ids({"modlog_ignored_channels": [1, "2", "x", None]}) == {1, 2}
    # missing / empty / wrong-typed settings yield an empty set, never raise.
    assert modlog._ignored_channel_ids({}) == set()
    assert modlog._ignored_channel_ids({"modlog_ignored_channels": None}) == set()
    assert modlog._ignored_channel_ids({"modlog_ignored_channels": "nope"}) == set()


def test_ignored_user_and_role_id_parsers() -> None:
    assert modlog._ignored_user_ids({"modlog_ignored_users": ["7", 8]}) == {7, 8}
    assert modlog._ignored_role_ids({"modlog_ignored_roles": [9]}) == {9}
    assert modlog._ignored_user_ids({}) == set()
    assert modlog._ignored_role_ids({}) == set()


class _FakeRole:
    def __init__(self, rid): self.id = rid


class _FakeActor:
    def __init__(self, *, bot=False, roles=()):
        self.bot = bot
        self.roles = [_FakeRole(r) for r in roles]


def test_is_ignored_event_bots_users_roles_channels() -> None:
    import asyncio
    logger = modlog.ModLogger(bot=None)

    def ev(**kw):
        return LogEvent(category=Category.MEMBER, event_type="t", guild_id=1, **kw)

    def ignored(event, settings) -> bool:
        return asyncio.run(logger.is_ignored_event(event, settings))

    # ignore-bots: a bot actor is suppressed, a human is not.
    assert ignored(ev(actor=_FakeActor(bot=True)), {"modlog_ignore_bots": True}) is True
    assert ignored(ev(actor=_FakeActor(bot=False)), {"modlog_ignore_bots": True}) is False
    # nothing configured -> never ignored.
    assert ignored(ev(actor=_FakeActor(bot=True)), {}) is False
    # ignored user by id (actor or target).
    assert ignored(ev(target=42), {"modlog_ignored_users": [42]}) is True
    # ignored role: an actor carrying the role is suppressed.
    assert ignored(ev(actor=_FakeActor(roles=[5, 6])), {"modlog_ignored_roles": [6]}) is True
    assert ignored(ev(actor=_FakeActor(roles=[5])), {"modlog_ignored_roles": [6]}) is False
    # ignored channel still works through the unified check.
    assert ignored(ev(channel=99), {"modlog_ignored_channels": [99]}) is True


def test_render_builds_a_layout_view() -> None:
    ev = LogEvent(category=Category.MODERATION, event_type="member.ban", guild_id=1,
                  severity=Severity.ALERT, target=12345,
                  summary="banned", metadata={"reason": "spam"})
    logger = modlog.ModLogger(bot=None)
    view = logger.render(ev)
    assert isinstance(view, discord.ui.LayoutView)
    # exactly one container child
    assert len(view.children) == 1
