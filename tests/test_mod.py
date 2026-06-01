"""Unit tests for the moderation cog's pure helpers (no bot/db)."""
from __future__ import annotations

import discord  # noqa: F401
import pytest

# mod.py imports the framework; skip on the dependency-light CI job.
pytest.importorskip("core.framework.cogs")

from cogs.mod import _fmt_duration, _parse_duration  # noqa: E402


def test_parse_compact_durations() -> None:
    assert _parse_duration("10m") == 600
    assert _parse_duration("1h") == 3600
    assert _parse_duration("2d") == 172800
    assert _parse_duration("1w") == 604800
    assert _parse_duration("30s") == 30


def test_parse_compound_duration() -> None:
    assert _parse_duration("1h30m") == 3600 + 1800
    assert _parse_duration("1d12h") == 86400 + 12 * 3600


def test_bare_number_is_minutes() -> None:
    assert _parse_duration("15") == 900


def test_invalid_duration_is_none() -> None:
    assert _parse_duration("soon") is None
    assert _parse_duration("") is None


def test_format_duration_roundtrips_units() -> None:
    assert _fmt_duration(0) == "0s"
    assert _fmt_duration(90) == "1m 30s"
    assert _fmt_duration(3661) == "1h 1m 1s"
    assert _fmt_duration(172800) == "2d"
