"""clanklib/help.py -- Clankwarden's dynamic-help *registry*.

Help is presented as ONE bot. Commands are grouped into sections; the help UI
offers a Components V2 multi-select so a user can pick one or more sections and
their commands combine into a single seamless list. The command lines are
generated from the bot's *live* command tree (not hand-maintained text), so the
help can never drift from what the bot actually exposes.

The renderers (live command-tree walking, section resolution) are bot-agnostic
and live in the framework (``core.framework.guildtools.help``). This module owns
Clankwarden's ``SECTIONS`` registry, binds it to that engine, and re-exports the
no-``sections``-argument convenience API the help view already calls.
"""
from __future__ import annotations

from typing import Iterable

from core.framework.guildtools.help import (
    Section,
    command_lines,
    sections_by_key,
    selected_sections as _selected_sections,
)

__all__ = [
    "Section",
    "command_lines",
    "SECTIONS",
    "SECTIONS_BY_KEY",
    "selected_sections",
]


# The feature map. ``commands`` are top-level command/group names; subcommands
# are discovered live from each group. Keep keys stable (used as select values).
# This registry is Clankwarden policy; the renderers that consume it are shared.
SECTIONS: tuple[Section, ...] = (
    Section("general", "General", "\U00002139️",
            "Help, about, ping, the invite link and the permission check.",
            ("help", "about", "ping", "invite", "setup")),
    Section("moderation", "Moderation", "\U0001F528",
            "Ban, kick, timeout, warn, purge, lock and more.",
            ("ban", "unban", "softban", "massban", "kick", "timeout", "untimeout",
             "warn", "warnings", "delwarn", "purge", "slowmode", "lock", "unlock")),
    Section("modlog", "Mod Log", "\U0001F4CB",
            "Categorized audit logging, timeline and case lookup.",
            ("modlog",)),
    Section("settings", "Settings", "\U00002699️",
            "Per-server configuration.",
            ("settings", "set")),
    Section("setup", "Setup", "\U0001F680",
            "One-command guided server setup.",
            ("init",)),
    Section("containment", "Containment", "\U0001F6E1️",
            "The .clank account-containment system: clank, unclank and the tank.",
            ("clank", "unclank")),
    Section("dehoist", "Smart Dehoist", "\U0001F9F9",
            "Server-aware impersonation/hoist detection and auto-containment.",
            ("dehoist",)),
)

SECTIONS_BY_KEY: dict[str, Section] = sections_by_key(SECTIONS)


def selected_sections(values: Iterable[str]) -> list[Section]:
    """Resolve select values to Sections, preserving SECTIONS order."""
    return _selected_sections(values, SECTIONS)
