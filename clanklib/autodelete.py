"""clanklib/autodelete.py -- per-guild auto-deletion of command output.

Two opt-in tiers (``0`` = keep forever, the default):

  * ``autodelete_replies`` -- short-lived command / mod-command reply panels
    (e.g. "Containment applied", error notices). The chatty ones.
  * ``autodelete_info`` -- informational panels (``.help`` / ``.about`` /
    ``.settings``); kept longer than replies.

Escape-room messages are **never** auto-deleted: callers there simply don't run
their output through this helper.

``send_v2`` returns the sent message, and discord.py's ``Message.delete(delay=)``
schedules a background deletion, so ``send_auto`` is a drop-in for the common
"send a panel and let it expire" case.
"""
from __future__ import annotations

from typing import Any


def _ttl(s: dict, key: str) -> float | None:
    try:
        v = int(s.get(key) or 0)
    except (TypeError, ValueError):
        return None
    return float(v) if v > 0 else None


def reply_ttl(s: dict) -> float | None:
    """Seconds before a command/mod reply self-deletes, or None to keep it."""
    return _ttl(s, "autodelete_replies")


def info_ttl(s: dict) -> float | None:
    """Seconds before an informational panel self-deletes, or None to keep it."""
    return _ttl(s, "autodelete_info")


async def expire(msg: Any, ttl: float | None) -> None:
    """Schedule a sent message to delete after ``ttl`` seconds (no-op if None)."""
    if msg is None or not ttl:
        return
    try:
        await msg.delete(delay=ttl)
    except Exception:
        pass
