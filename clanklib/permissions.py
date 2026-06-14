"""clanklib/permissions.py -- the bot's permission *registry*.

A bot's permission model has two parts:

  * a per-bot **registry** of features and the Discord permissions each needs
    (so the invite link requests the minimum, never blanket Administrator), and
  * the shared **engine** that turns that registry into an invite scope, a
    readiness audit, and a moderator-gated cog base.

The engine is bot-agnostic and lives in the framework
(``core.framework.guildtools.permissions``). This module owns Clankwarden's
``FEATURES`` registry -- the one piece that is genuinely product policy -- binds
it to the engine once, and re-exports the no-argument convenience API the cogs
already call (``required_bot_permissions()``, ``invite_url(client_id)``,
``audit_permissions(me)``, ``ModCog`` ...). Keeping the invite, the gating, and
the setup advice driven from this single registry means they can never disagree
about what the bot requires.
"""
from __future__ import annotations

import discord

from core.framework.guildtools.permissions import (
    MOD_PERMISSION,
    FeaturePerm,
    ModCog,
    PermAudit,
    audit_permissions as _audit_permissions,
    invite_url as _invite_url,
    pretty_perm,
    required_bot_permissions as _required_bot_permissions,
)

__all__ = [
    "MOD_PERMISSION",
    "FeaturePerm",
    "ModCog",
    "PermAudit",
    "FEATURES",
    "required_bot_permissions",
    "invite_url",
    "audit_permissions",
    "pretty_perm",
]


# What the bot needs, per feature. These drive both the invite scope and the
# setup audit. They are deliberately scoped: only containment/setup need the
# heavy role/channel management, and nothing needs Administrator. This tuple is
# Clankwarden-specific policy; the engine that consumes it is shared.
FEATURES: tuple[FeaturePerm, ...] = (
    FeaturePerm(
        "core", "Core",
        ("view_channel", "send_messages", "send_messages_in_threads",
         "embed_links", "attach_files", "add_reactions",
         "read_message_history"),
        "Read and reply in channels and threads, react to confirmation "
        "prompts, and post rich panels and chart images.",
    ),
    FeaturePerm(
        "containment", "Containment (clank)",
        ("manage_roles", "manage_messages", "moderate_members", "ban_members",
         "create_private_threads", "manage_threads"),
        "Apply the Clanker role, clean up messages, open the private cloister "
        "review thread and pull the contained user into it, and act on "
        "offenders. (Adding a member to a private, non-invitable thread needs "
        "Manage Threads.)",
    ),
    FeaturePerm(
        "moderation", "Moderation commands",
        ("kick_members", "ban_members", "moderate_members", "manage_messages",
         "manage_channels", "manage_nicknames"),
        "Run ban/kick/timeout/purge/lock plus the smart-dehoist rename of "
        "impersonators, and the rest of the mod command set.",
    ),
    FeaturePerm(
        "setup", "Server setup (.setup)",
        ("manage_channels", "manage_roles", "create_public_threads"),
        "Create the containment role, category and log channels, and the "
        "public escape-room thread during the setup wizard.",
    ),
    FeaturePerm(
        "logging", "Mod logging and audit",
        ("view_audit_log", "manage_guild"),
        "Attribute events to the acting moderator and resolve which invite a "
        "member joined with.",
    ),
)


def required_bot_permissions() -> discord.Permissions:
    """The union of every feature's bot permissions -- the exact set the invite
    link requests. No Administrator."""
    return _required_bot_permissions(FEATURES)


def invite_url(client_id: int | str) -> str:
    """An OAuth invite that asks for exactly the permissions the bot uses."""
    return _invite_url(client_id, FEATURES)


def audit_permissions(me: discord.Member) -> list[PermAudit]:
    """Compare the bot's current guild permissions against every feature's
    needs. Administrator short-circuits everything to OK."""
    return _audit_permissions(me, FEATURES)
