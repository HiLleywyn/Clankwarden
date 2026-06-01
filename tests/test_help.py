"""Tests for the dynamic help model (clanklib.help)."""
from __future__ import annotations

from clanklib import help as H


class _FakeCmd:
    def __init__(self, name, short_doc="", subs=None, hidden=False):
        self.name = name
        self.short_doc = short_doc
        self.hidden = hidden
        self.commands = subs or []


class _FakeBot:
    def __init__(self, cmds):
        self._cmds = {c.name: c for c in cmds}

    def get_command(self, name):
        return self._cmds.get(name)


def test_sections_have_stable_unique_keys():
    keys = [s.key for s in H.SECTIONS]
    assert len(keys) == len(set(keys))
    assert "containment" in keys and "moderation" in keys


def test_command_lines_expands_group_subcommands():
    clank = _FakeCmd("clank", subs=[
        _FakeCmd("add", "Contain a user."),
        _FakeCmd("remove", "Release a user."),
    ])
    bot = _FakeBot([clank])
    sec = H.SECTIONS_BY_KEY["containment"]
    lines = H.command_lines(bot, sec, ".")
    assert lines == ["`.clank add` -- Contain a user.",
                     "`.clank remove` -- Release a user."]


def test_command_lines_skips_hidden_and_missing():
    grp = _FakeCmd("clank", subs=[
        _FakeCmd("add", "ok"),
        _FakeCmd("secret", "hidden one", hidden=True),
    ])
    bot = _FakeBot([grp])  # other section commands absent -> skipped, no crash
    sec = H.SECTIONS_BY_KEY["containment"]
    lines = H.command_lines(bot, sec, ".")
    assert lines == ["`.clank add` -- ok"]


def test_plain_command_lists_itself():
    ban = _FakeCmd("ban", "Ban a member or user id.")
    bot = _FakeBot([ban])
    sec = H.SECTIONS_BY_KEY["moderation"]
    lines = H.command_lines(bot, sec, ".")
    assert "`.ban` -- Ban a member or user id." in lines


def test_selected_sections_preserves_order_and_filters():
    chosen = H.selected_sections({"containment", "moderation", "bogus"})
    keys = [s.key for s in chosen]
    # SECTIONS order preserved (moderation before containment), unknown dropped
    assert keys == ["moderation", "containment"]
