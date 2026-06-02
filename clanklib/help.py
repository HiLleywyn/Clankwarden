"""clanklib/help.py -- the dynamic help model behind the modern .help hub.

Help is presented as ONE bot. Commands are grouped into sections; the help UI
offers a Components V2 multi-select so a user can pick one or more sections and
their commands combine into a single seamless list. The command lines are
generated from the bot's *live* command tree (not hand-maintained text), so the
help can never drift from what the bot actually exposes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class Section:
    key: str            # stable id used by the select
    label: str          # shown in the select + as the group heading
    emoji: str          # select option emoji
    blurb: str          # one-line description
    commands: tuple[str, ...]  # top-level command names that belong here


# The feature map. ``commands`` are top-level command/group names; subcommands
# are discovered live from each group. Keep keys stable (used as select values).
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

SECTIONS_BY_KEY: dict[str, Section] = {s.key: s for s in SECTIONS}


def _walk_subcommands(cmd: Any) -> list[Any]:
    """Return a command's direct subcommands (for a group), else []."""
    subs = getattr(cmd, "commands", None)
    if not subs:
        return []
    # de-dupe (aliases share the object) and sort by name
    seen: dict[str, Any] = {}
    for c in subs:
        seen[c.name] = c
    return [seen[n] for n in sorted(seen)]


def command_lines(bot: Any, section: Section, prefix: str) -> list[str]:
    """Render the command lines for one section from the live command tree.

    A group lists its subcommands (``backup create``, ``backup load`` ...); a
    plain command lists itself. Hidden commands are skipped. Short help text is
    appended when present."""
    lines: list[str] = []
    for name in section.commands:
        cmd = bot.get_command(name)
        if cmd is None or getattr(cmd, "hidden", False):
            continue
        subs = _walk_subcommands(cmd)
        if subs:
            # If the group itself does something when invoked without a
            # subcommand (e.g. `.clank @user`), show that line first.
            gdoc = (cmd.short_doc or "").strip()
            if gdoc:
                lines.append(f"`{prefix}{cmd.name}` -- {gdoc}")
            for sub in subs:
                if getattr(sub, "hidden", False):
                    continue
                short = (sub.short_doc or "").strip()
                line = f"`{prefix}{cmd.name} {sub.name}`"
                lines.append(line + (f" -- {short}" if short else ""))
        else:
            short = (cmd.short_doc or "").strip()
            line = f"`{prefix}{cmd.name}`"
            lines.append(line + (f" -- {short}" if short else ""))
    return lines


def selected_sections(values: Iterable[str]) -> list[Section]:
    """Resolve select values to Sections, preserving SECTIONS order."""
    chosen = set(values)
    return [s for s in SECTIONS if s.key in chosen]
