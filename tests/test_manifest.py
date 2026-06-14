"""Validate auren.json without importing the (discord-dependent) framework.

These checks mirror the framework's manifest contract and additionally assert
that every declared feature maps to a real cog module in this repo, so a typo
in the manifest fails CI rather than at boot.
"""
from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "auren.json"

ALLOWED_TYPES = {
    "string", "number", "boolean", "select", "secret",
    "discord_channel", "discord_role",
}


def _load() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_manifest_is_present_and_versioned() -> None:
    data = _load()
    assert data["manifest_version"] == "1"
    for key in ("slug", "name", "version"):
        assert data["bot"][key], f"bot.{key} must be set"


def test_features_map_to_real_cog_modules() -> None:
    data = _load()
    assert isinstance(data["features"], list) and data["features"]
    for feature in data["features"]:
        assert feature.startswith("cogs."), feature
        module_path = ROOT / (feature.replace(".", "/") + ".py")
        assert module_path.exists(), f"feature {feature} has no module at {module_path}"


def test_setting_field_types_are_valid() -> None:
    data = _load()
    seen: set[str] = set()
    for group in data.get("settings", {}).get("groups", []):
        for field in group.get("fields", []):
            assert field["key"], "every field needs a key"
            assert field["type"] in ALLOWED_TYPES, field["type"]
            assert field["key"] not in seen, f"duplicate field key {field['key']}"
            seen.add(field["key"])


def test_credentials_declare_the_token() -> None:
    data = _load()
    keys = {c["key"] for c in data.get("credentials", [])}
    assert "DISCORD_TOKEN" in keys


# The least-privilege gateway set the bot actually uses (audited against every
# on_* listener: modlog, clank, dehoist). Declared so the bot drops the
# framework's broad moderation-bot default instead of inheriting it.
_EXPECTED_INTENTS = {
    "guilds", "members", "moderation", "guild_messages",
    "message_content", "invites", "auto_moderation_execution",
}


def test_declares_least_privilege_intents() -> None:
    data = _load()
    declared = data.get("framework", {}).get("intents")
    assert isinstance(declared, list) and declared, (
        "auren.json must declare framework.intents explicitly (least privilege)"
    )
    assert set(declared) == _EXPECTED_INTENTS, (
        f"intent set drift: {set(declared) ^ _EXPECTED_INTENTS}"
    )


def test_no_unused_privileged_or_unlistened_intents() -> None:
    # The bot has zero presence/voice/reaction/typing listeners; requesting any
    # of these (presences especially -- it is privileged) would over-ask and
    # hurt App Directory verification.
    declared = set(_load().get("framework", {}).get("intents", []))
    for forbidden in ("presences", "voice_states", "guild_reactions",
                      "guild_typing", "dm_messages", "dm_reactions"):
        assert forbidden not in declared, f"{forbidden} is unused; do not request it"
