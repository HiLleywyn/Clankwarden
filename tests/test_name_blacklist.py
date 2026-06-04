"""Unit tests for the join-name blacklist, multi-channel coercion, and the
Clankermax role hierarchy preflight (pure helpers in cogs.clank)."""
from __future__ import annotations

import pytest

# clank.py imports the framework + discord; skip on the dependency-light CI job.
pytest.importorskip("core.framework.ui")

import cogs.clank as clank  # noqa: E402


# -- _name_blacklist_hit ------------------------------------------------------

def test_blacklist_matches_substring_case_insensitive():
    assert clank._name_blacklist_hit({"SupportScammer"}, ["scammer"]) == "scammer"
    assert clank._name_blacklist_hit({"FooBar"}, ["BAR"]) == "bar"


def test_blacklist_ignores_punctuation_and_spacing():
    # "s.c.a.m" / "S c a m" still match the pattern "scam" via the stripped form.
    assert clank._name_blacklist_hit({"s.c.a.m support"}, ["scam"]) == "scam"
    assert clank._name_blacklist_hit({"S c a m"}, ["scam"]) == "scam"


def test_blacklist_tolerates_wildcards_and_blank_patterns():
    assert clank._name_blacklist_hit({"airdropbot"}, ["*airdrop*"]) == "airdrop"
    assert clank._name_blacklist_hit({"anyone"}, ["   ", "*", ""]) is None


def test_blacklist_no_match_and_empty():
    assert clank._name_blacklist_hit({"GenuineUser"}, ["scam", "airdrop"]) is None
    assert clank._name_blacklist_hit({"anything"}, []) is None
    assert clank._name_blacklist_hit(set(), ["scam"]) is None


# -- _as_str_list / _as_int_list ---------------------------------------------

def test_as_str_list_accepts_list_and_string():
    assert clank._as_str_list(["a", " b ", "a", ""]) == ["a", "b"]
    assert clank._as_str_list("x, y\nz, x") == ["x", "y", "z"]
    assert clank._as_str_list(None) == []
    assert clank._as_str_list("") == []


def test_as_int_list_dedupes_and_coerces():
    assert clank._as_int_list([1, 2, 2, 0, "3"]) == [1, 2, 3]
    assert clank._as_int_list("10 20, 30") == [10, 20, 30]
    assert clank._as_int_list(42) == [42]
    assert clank._as_int_list(None) == []
    assert clank._as_int_list("not-ids") == []
