"""Tests for clanklib.dehoist -- the pure smart-dehoist detector.

Dependency-free (no discord/numpy/framework), so this runs in the dependency-light
CI job. Covers name normalization (hoist/zalgo/fullwidth/confusables), per-guild
signal scanning, and the tiered classify() behavior.
"""
from __future__ import annotations

from clanklib import dehoist as dh


# --------------------------------------------------------------------------- #
# Normalization + hoist stripping
# --------------------------------------------------------------------------- #

def test_leading_hoist_run():
    assert dh.leading_hoist_run("!!! free nitro") == "!!! "
    assert dh.leading_hoist_run("~|~ Admin") == "~|~ "
    assert dh.leading_hoist_run("normal name") == ""
    assert dh.leading_hoist_run("(((") == "((("


def test_clean_nick_strips_and_caps():
    assert dh.clean_nick("!!! FREE NITRO") == "FREE NITRO"
    assert dh.clean_nick("normal") == "normal"
    # nothing printable left -> placeholder
    assert dh.clean_nick("!!!~~~") == dh._PLACEHOLDER
    # length cap at 32
    assert len(dh.clean_nick("x" * 50)) == 32


def test_normalize_folds_fullwidth_and_confusables():
    # fullwidth "Ｓｕｐｐｏｒｔ" folds to ascii via NFKC
    assert dh.normalize_display("Ｓｕｐｐｏｒｔ") == "support"
    # cyrillic homoglyph "Binаnce" (the 'а' is U+0430) folds to latin
    assert dh.normalize_display("Binаnce") == "binance"


def test_normalize_strips_zalgo():
    zalgo = "À́̂dmĩn"
    assert dh.normalize_display(zalgo) == "admin"


# --------------------------------------------------------------------------- #
# Guild signal scanning
# --------------------------------------------------------------------------- #

class _Role:
    def __init__(self, name): self.name = name

class _Chan:
    def __init__(self, name, topic=""): self.name = name; self.topic = topic

class _Guild:
    def __init__(self, name="", description="", roles=(), text_channels=()):
        self.name = name
        self.description = description
        self.roles = list(roles)
        self.text_channels = list(text_channels)


def _crypto_guild():
    return _Guild(
        name="DeFi Degens",
        description="A community for crypto trading and investing.",
        roles=[_Role("@everyone"), _Role("Admin"), _Role("Moderator"), _Role("Member")],
        text_channels=[_Chan("general"), _Chan("price-talk", topic="ethereum and bitcoin ta"),
                       _Chan("support")],
    )


def test_scan_detects_topics_and_builds_lexicon():
    sig = dh.scan_guild_signals(_crypto_guild())
    assert "crypto" in sig.topics
    # brand terms picked from the server vocabulary
    assert {"crypto", "bitcoin", "ethereum"} & sig.brand_terms
    # synthesized impersonation phrases
    assert "crypto support" in sig.lexicon or "bitcoin support" in sig.lexicon
    # staff terms harvested from role names
    assert any("admin" in t or "moderator" in t for t in sig.staff_terms)


def test_scan_empty_guild_has_solo_authority_only():
    sig = dh.scan_guild_signals(_Guild(name="just vibes", roles=[_Role("@everyone")]))
    assert not sig.topics
    # solo-authority lexicon is always present
    assert "support" in sig.lexicon and "admin" in sig.lexicon


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #

def test_clean_name_returns_none():
    sig = dh.scan_guild_signals(_crypto_guild())
    assert dh.classify({"jenny", "Jenny"}, sig) is None


def test_pure_hoist_is_low_tier():
    sig = dh.scan_guild_signals(_crypto_guild())
    det = dh.classify({"!!! lol degen", "!!! lol degen"}, sig, display_name="!!! lol degen")
    assert det is not None
    assert det.kind == "hoist"
    assert det.tier == "low"
    assert det.clean_nick == "lol degen"


def test_brand_impersonation_is_high_tier():
    sig = dh.scan_guild_signals(_crypto_guild())
    det = dh.classify({"Binance Support", "binance_support"}, sig)
    assert det is not None
    assert det.is_impersonation and det.tier == "high"


def test_confusable_impersonation_still_caught():
    sig = dh.scan_guild_signals(_crypto_guild())
    # cyrillic 'а' in Binаnce
    det = dh.classify({"Binаnce Support"}, sig)
    assert det is not None and det.is_impersonation


def test_solo_authority_impersonation():
    sig = dh.scan_guild_signals(_crypto_guild())
    assert dh.classify({"Support"}, sig).is_impersonation
    assert dh.classify({"Moderator"}, sig).is_impersonation


def test_no_false_positive_on_substring():
    # "moderation" must not trip the solo "mod"/"moderator" token rule
    sig = dh.scan_guild_signals(_crypto_guild())
    assert dh.classify({"moderation enjoyer"}, sig) is None


def test_both_kind_when_hoisted_impersonator():
    sig = dh.scan_guild_signals(_crypto_guild())
    det = dh.classify({"!! Binance Support"}, sig, display_name="!! Binance Support")
    assert det is not None and det.kind == "both"
    assert det.confidence >= 0.95
    assert det.clean_nick == "Binance Support"
