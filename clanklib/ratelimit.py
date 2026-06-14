"""clanklib/ratelimit.py -- paced execution for mass moderation actions.

Bursting hundreds of bans / role edits / clanks at once is what earns a
Cloudflare-level 429 (the multi-hour ban we hit cleaving 500 accounts). Pacing a
batch, backing off on 429, and aborting after a few consecutive rate limits is a
generic Discord concern, so the implementation lives in the framework at
``core.framework.guildtools.ratelimit``. This module re-exports it unchanged so
the clank port keeps importing ``from clanklib.ratelimit import BulkRunner``.

Usage::

    runner = BulkRunner()
    result = await runner.run(members, do_one, progress=update_msg)
"""
from __future__ import annotations

from core.framework.guildtools.ratelimit import (
    BulkResult,
    BulkRunner,
    _retry_after,
)

__all__ = ["BulkResult", "BulkRunner", "_retry_after"]
