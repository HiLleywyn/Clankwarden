"""Unit tests for the serializer's pure logic (no live Discord objects)."""
from __future__ import annotations

import discord  # noqa: F401  -- ensures the runtime has Components V2 era discord.py

from services import serializer


def test_restore_options_defaults_are_safe() -> None:
    opts = serializer.RestoreOptions()
    assert opts.delete_roles is True
    assert opts.delete_channels is True
    assert opts.restore_settings is True
    # Messages are heavy + webhook-driven, so off unless explicitly requested.
    assert opts.restore_messages is False


def test_restore_stats_summary_mentions_counts() -> None:
    stats = serializer.RestoreStats(roles_created=3, channels_created=5, messages_sent=12)
    summary = stats.summary()
    assert "3 roles" in summary
    assert "5 channels" in summary
    assert "12 messages" in summary


def test_message_limits_are_sane() -> None:
    assert serializer.DEFAULT_MESSAGE_LIMIT >= 1
    assert serializer.MAX_MESSAGE_LIMIT >= serializer.DEFAULT_MESSAGE_LIMIT
    assert serializer.SCHEMA_VERSION >= 1
