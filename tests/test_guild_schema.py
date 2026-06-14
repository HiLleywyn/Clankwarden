"""Tests for clanklib.guild_schema -- the per-guild settings contract shared by
the REST API and the in-Discord ``.set`` command."""
from __future__ import annotations

import pytest

# clanklib.guild_schema now re-exports the framework's validation engine; skip
# when the framework isn't installed (the deps-only CI job), like the cog tests.
pytest.importorskip("core.framework.guildtools.guild_schema")

from clanklib import guild_schema as gs


def test_schema_lists_editable_fields():
    schema = gs.schema_json()
    keys = {f["key"] for f in schema["fields"]}
    assert {"prefix", "log_channel", "clanktank_channel"} <= keys
    # every field declares a type the UI understands
    for f in schema["fields"]:
        assert f["type"] in {"string", "bool", "number", "discord_channel", "discord_role"}


def test_security_pause_dms_is_a_bool_field():
    fld = gs.FIELDS_BY_KEY["security_pause_dms"]
    assert fld.type == "bool"
    assert gs.coerce_guild_value(fld, "on") is True
    assert gs.coerce_guild_value(fld, "off") is False
    assert gs.coerce_guild_value(fld, True) is True


def test_coerce_string_respects_max_len():
    fld = gs.FIELDS_BY_KEY["prefix"]
    assert gs.coerce_guild_value(fld, "!") == "!"
    assert gs.coerce_guild_value(fld, "  ") is None        # blank clears
    with pytest.raises(gs.GuildSettingError):
        gs.coerce_guild_value(fld, "toolong")              # > 5 chars


def test_coerce_channel_accepts_id_and_mention():
    fld = gs.FIELDS_BY_KEY["clanktank_channel"]
    assert gs.coerce_guild_value(fld, "123456789012345678") == 123456789012345678
    assert gs.coerce_guild_value(fld, "<#123456789012345678>") == 123456789012345678
    assert gs.coerce_guild_value(fld, "none") is None      # clears
    with pytest.raises(gs.GuildSettingError):
        gs.coerce_guild_value(fld, "not-an-id")


def test_validate_reports_unknown_keys():
    coerced, errors = gs.validate_guild_settings({"prefix": "?", "bogus": "x"})
    assert coerced == {"prefix": "?"}
    assert any("unknown setting" in e for e in errors)


def test_validate_aggregates_field_errors():
    _, errors = gs.validate_guild_settings({"prefix": "waytoolong", "clanktank_channel": "nope"})
    assert len(errors) == 2


def test_public_view_only_exposes_editable_fields():
    row = {
        "guild_id": 42, "prefix": "!", "log_channel": 5, "clanktank_channel": 6,
        "clanktank_log_channel": None, "clank_escape_thread": None,
        "features": {"secret": "internal"}, "updated_at": 123,  # must NOT leak
    }
    view = gs.public_view(row)
    assert view["guild_id"] == 42 and view["prefix"] == "!"
    assert "features" not in view and "updated_at" not in view
