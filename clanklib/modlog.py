"""clanklib/modlog.py -- the centralized moderation logger.

One place that every subsystem routes events through, so the bot has a single,
consistent, categorized audit trail rendered in Components V2 and persisted for
the ``.modlog`` timeline, per-actor/target history, and retention pruning.

Design
------
* A :class:`LogEvent` is a standardized record: a short public ``event_id``, a
  UTC timestamp, a :class:`Category`, a :class:`Severity`, an ``event_type``, the
  ``actor`` and ``target`` (when applicable), the originating ``channel``, a
  one-line ``summary`` and a free-form ``metadata`` dict.
* :class:`ModLogger` is constructed once and hung off the bot as ``bot.modlog``.
  Cogs call the convenience methods (``security``, ``mod``, ``member``,
  ``message``, ``role``, ``channel_event``, ``command``, ``config``, ``ai``,
  ``system``, ``clanktank``) which build a :class:`LogEvent` and dispatch it.
* Dispatch is best-effort and never raises into the caller: it persists the row
  (when a data plane is present) and renders a Components V2 panel into the
  resolved channel. A category with no resolved channel and no default simply
  is not posted, but is still persisted for the timeline.

Channel routing per guild (read from ``guild_settings``):
* ``mod_log_channel``         -- the default destination for every category;
* ``modlog_routes``           -- ``{category: channel_id}`` per-category overrides;
* ``modlog_muted``            -- a list of category names to never post (still
  persisted), so an operator can keep, say, ``message`` events out of the channel
  while retaining them for the timeline.

All ASCII; no embeds.
"""
from __future__ import annotations

import json
import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

import discord

from core.framework.ui import (
    C_AMBER, C_ERROR, C_INFO, C_NAVY, C_NEUTRAL, C_PURPLE,
    C_SUCCESS, C_TEAL, C_WARNING,
)

log = logging.getLogger(__name__)

# Hard caps so a hostile or oversized event can never break a Discord send or
# bloat the row. Discord's message component text limit is generous; we stay
# well under it.
_SUMMARY_MAX = 500
_FIELD_VALUE_MAX = 800
_METADATA_BYTES_MAX = 6000


class Severity(Enum):
    """How loud an event is. Drives colour, label and realtime alerting."""

    INFO = "info"
    NOTICE = "notice"
    WARNING = "warning"
    ALERT = "alert"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return {"info": 0, "notice": 1, "warning": 2, "alert": 3, "critical": 4}[self.value]

    @property
    def color(self) -> int:
        return {
            "info": C_INFO,
            "notice": C_TEAL,
            "warning": C_WARNING,
            "alert": C_AMBER,
            "critical": C_ERROR,
        }[self.value]

    @property
    def label(self) -> str:
        return self.value.upper()


class Category(Enum):
    """The subsystem an event belongs to. Drives routing + the panel tag."""

    SECURITY = "security"
    MODERATION = "moderation"
    MEMBER = "member"
    MESSAGE = "message"
    ROLE = "role"
    CHANNEL = "channel"
    COMMAND = "command"
    CONFIG = "config"
    AI = "ai"
    INFRASTRUCTURE = "infrastructure"
    CLANKTANK = "clanktank"
    ANALYTICS = "analytics"

    @property
    def label(self) -> str:
        return {
            "security": "Security",
            "moderation": "Moderation",
            "member": "Member",
            "message": "Message",
            "role": "Role",
            "channel": "Channel",
            "command": "Command",
            "config": "Configuration",
            "ai": "AI",
            "infrastructure": "Infrastructure",
            "clanktank": "Clanktank",
            "analytics": "Analytics",
        }[self.value]


# The canonical category set, for validation in commands.
CATEGORY_NAMES: tuple[str, ...] = tuple(c.value for c in Category)


def _short_id() -> str:
    """A short, unguessable, sortable-ish public event id."""
    return "evt_" + secrets.token_hex(5)


def _coerce_id(obj: Any) -> Optional[int]:
    """Pull a Discord snowflake id out of a member/user/role/channel/int."""
    if obj is None:
        return None
    if isinstance(obj, int):
        return obj
    rid = getattr(obj, "id", None)
    try:
        return int(rid) if rid is not None else None
    except (TypeError, ValueError):
        return None


def _name_of(obj: Any) -> str:
    """A human label for an actor/target object (best-effort)."""
    if obj is None:
        return ""
    if isinstance(obj, int):
        return f"`{obj}`"
    name = getattr(obj, "display_name", None) or getattr(obj, "name", None)
    rid = _coerce_id(obj)
    if name and rid:
        return f"{name} (`{rid}`)"
    if name:
        return str(name)
    return f"`{rid}`" if rid else str(obj)


@dataclass
class LogEvent:
    """A single standardized moderation-log record."""

    category: Category
    event_type: str
    guild_id: int
    severity: Severity = Severity.INFO
    actor: Any = None
    target: Any = None
    channel: Any = None
    summary: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=_short_id)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Resolved ids (filled lazily for persistence).
    @property
    def actor_id(self) -> Optional[int]:
        return _coerce_id(self.actor)

    @property
    def target_id(self) -> Optional[int]:
        return _coerce_id(self.target)

    @property
    def channel_id(self) -> Optional[int]:
        return _coerce_id(self.channel)


class ModLogger:
    """Centralized event sink: persist + render + dispatch to Discord."""

    def __init__(self, bot: Any) -> None:
        self.bot = bot

    @property
    def db(self) -> Any:
        return getattr(self.bot, "db", None)

    # -- public convenience methods -------------------------------------------

    async def security(self, event_type: str, guild_id: int, **kw: Any) -> LogEvent:
        return await self._emit(Category.SECURITY, event_type, guild_id,
                                kw.pop("severity", Severity.ALERT), **kw)

    async def mod(self, event_type: str, guild_id: int, **kw: Any) -> LogEvent:
        return await self._emit(Category.MODERATION, event_type, guild_id,
                                kw.pop("severity", Severity.NOTICE), **kw)

    async def member(self, event_type: str, guild_id: int, **kw: Any) -> LogEvent:
        return await self._emit(Category.MEMBER, event_type, guild_id,
                                kw.pop("severity", Severity.INFO), **kw)

    async def message(self, event_type: str, guild_id: int, **kw: Any) -> LogEvent:
        return await self._emit(Category.MESSAGE, event_type, guild_id,
                                kw.pop("severity", Severity.INFO), **kw)

    async def role(self, event_type: str, guild_id: int, **kw: Any) -> LogEvent:
        return await self._emit(Category.ROLE, event_type, guild_id,
                                kw.pop("severity", Severity.NOTICE), **kw)

    async def channel_event(self, event_type: str, guild_id: int, **kw: Any) -> LogEvent:
        return await self._emit(Category.CHANNEL, event_type, guild_id,
                                kw.pop("severity", Severity.NOTICE), **kw)

    async def command(self, event_type: str, guild_id: int, **kw: Any) -> LogEvent:
        return await self._emit(Category.COMMAND, event_type, guild_id,
                                kw.pop("severity", Severity.INFO), **kw)

    async def config(self, event_type: str, guild_id: int, **kw: Any) -> LogEvent:
        return await self._emit(Category.CONFIG, event_type, guild_id,
                                kw.pop("severity", Severity.NOTICE), **kw)

    async def ai(self, event_type: str, guild_id: int, **kw: Any) -> LogEvent:
        return await self._emit(Category.AI, event_type, guild_id,
                                kw.pop("severity", Severity.INFO), **kw)

    async def system(self, event_type: str, guild_id: int, **kw: Any) -> LogEvent:
        return await self._emit(Category.INFRASTRUCTURE, event_type, guild_id,
                                kw.pop("severity", Severity.INFO), **kw)

    async def clanktank(self, event_type: str, guild_id: int, **kw: Any) -> LogEvent:
        return await self._emit(Category.CLANKTANK, event_type, guild_id,
                                kw.pop("severity", Severity.NOTICE), **kw)

    async def emit(self, event: LogEvent) -> LogEvent:
        """Dispatch a pre-built event (persist + post). Never raises."""
        await self._persist(event)
        await self._dispatch(event)
        return event

    # -- internals ------------------------------------------------------------

    async def _emit(self, category: Category, event_type: str, guild_id: int,
                    severity: Severity, **kw: Any) -> LogEvent:
        event = LogEvent(
            category=category,
            event_type=str(event_type),
            guild_id=int(guild_id),
            severity=severity if isinstance(severity, Severity) else Severity.INFO,
            actor=kw.get("actor"),
            target=kw.get("target"),
            channel=kw.get("channel"),
            summary=str(kw.get("summary", ""))[:_SUMMARY_MAX],
            metadata=dict(kw.get("metadata") or {}),
        )
        return await self.emit(event)

    async def _persist(self, event: LogEvent) -> None:
        db = self.db
        if db is None:
            return
        try:
            meta = json.dumps(event.metadata)[:_METADATA_BYTES_MAX]
            await db.execute(
                "INSERT INTO mod_log_events "
                "(event_id, guild_id, created_at, category, severity, event_type, "
                " actor_id, target_id, channel_id, summary, metadata) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb) "
                "ON CONFLICT (guild_id, event_id) DO NOTHING",
                event.event_id, event.guild_id, event.created_at,
                event.category.value, event.severity.value, event.event_type,
                event.actor_id, event.target_id, event.channel_id,
                event.summary, meta,
            )
        except Exception:  # noqa: BLE001
            log.debug("modlog persist failed", exc_info=True)

    async def _dispatch(self, event: LogEvent) -> None:
        try:
            if await self._is_muted(event.guild_id, event.category):
                return
            channel = await self._resolve_channel(event.guild_id, event.category)
            if channel is None:
                return
            view = self.render(event)
            await channel.send(view=view)
        except discord.Forbidden:
            log.debug("modlog: missing permission to post in log channel g=%s", event.guild_id)
        except Exception:  # noqa: BLE001
            log.debug("modlog dispatch failed", exc_info=True)

    async def _guild_settings(self, guild_id: int) -> dict:
        db = self.db
        if db is None:
            return {}
        try:
            return await db.get_guild_settings(int(guild_id)) or {}
        except Exception:  # noqa: BLE001
            return {}

    async def _is_muted(self, guild_id: int, category: Category) -> bool:
        s = await self._guild_settings(guild_id)
        muted = s.get("modlog_muted") or []
        try:
            return category.value in {str(x) for x in muted}
        except TypeError:
            return False

    async def _resolve_channel(self, guild_id: int, category: Category):
        s = await self._guild_settings(guild_id)
        routes = s.get("modlog_routes") or {}
        cid = None
        if isinstance(routes, dict):
            cid = routes.get(category.value)
        if not cid:
            cid = s.get("mod_log_channel")
        if not cid:
            return None
        try:
            cid = int(cid)
        except (TypeError, ValueError):
            return None
        ch = self.bot.get_channel(cid)
        if ch is None:
            try:
                ch = await self.bot.fetch_channel(cid)
            except Exception:  # noqa: BLE001
                return None
        if isinstance(ch, (discord.TextChannel, discord.Thread)):
            return ch
        return None

    # -- rendering ------------------------------------------------------------

    def render(self, event: LogEvent) -> discord.ui.LayoutView:
        """Render a Components V2 panel for an event."""
        sev = event.severity
        ts = int(event.created_at.timestamp())
        header = (
            f"## {sev.label} | {event.category.label}\n"
            f"-# {_humanize_type(event.event_type)}  *  <t:{ts}:f>  *  `{event.event_id}`"
        )
        rows: list[discord.ui.Item] = [discord.ui.TextDisplay(header)]
        rows.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))

        if event.summary:
            rows.append(discord.ui.TextDisplay(event.summary[:_FIELD_VALUE_MAX]))

        line_bits: list[str] = []
        if event.actor is not None:
            line_bits.append(f"**Actor** {_name_of(event.actor)}")
        if event.target is not None:
            line_bits.append(f"**Target** {_name_of(event.target)}")
        if event.channel is not None:
            cid = event.channel_id
            line_bits.append(f"**Channel** {'<#%d>' % cid if cid else _name_of(event.channel)}")
        if line_bits:
            rows.append(discord.ui.TextDisplay("\n".join(line_bits)))

        meta_lines = _format_metadata(event.metadata)
        if meta_lines:
            rows.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))
            rows.append(discord.ui.TextDisplay(meta_lines))

        container = discord.ui.Container(*rows, accent_color=sev.color)
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(container)
        return view

    # -- queries (timeline / stats / retention) -------------------------------

    async def timeline(self, guild_id: int, *, category: Optional[str] = None,
                       actor_id: Optional[int] = None, target_id: Optional[int] = None,
                       min_severity: Optional[str] = None, limit: int = 50) -> list[dict]:
        db = self.db
        if db is None:
            return []
        clauses = ["guild_id = $1"]
        args: list[Any] = [int(guild_id)]
        if category:
            args.append(category)
            clauses.append(f"category = ${len(args)}")
        if actor_id:
            args.append(int(actor_id))
            clauses.append(f"actor_id = ${len(args)}")
        if target_id:
            args.append(int(target_id))
            clauses.append(f"target_id = ${len(args)}")
        if min_severity:
            ranks = [s.value for s in Severity if s.rank >= Severity(min_severity).rank] \
                if min_severity in {s.value for s in Severity} else None
            if ranks:
                args.append(ranks)
                clauses.append(f"severity = ANY(${len(args)})")
        args.append(max(1, min(200, int(limit))))
        sql = (
            "SELECT * FROM mod_log_events WHERE " + " AND ".join(clauses)
            + f" ORDER BY created_at DESC, id DESC LIMIT ${len(args)}"
        )
        try:
            return await db.fetch_all(sql, *args)
        except Exception:  # noqa: BLE001
            log.debug("modlog timeline query failed", exc_info=True)
            return []

    async def stats(self, guild_id: int, *, hours: int = 24) -> list[dict]:
        db = self.db
        if db is None:
            return []
        try:
            return await db.fetch_all(
                "SELECT category, COUNT(*) AS n FROM mod_log_events "
                "WHERE guild_id = $1 AND created_at > NOW() - ($2 || ' hours')::interval "
                "GROUP BY category ORDER BY n DESC",
                int(guild_id), str(int(hours)),
            )
        except Exception:  # noqa: BLE001
            return []

    async def prune(self, guild_id: int, keep_days: int) -> int:
        db = self.db
        if db is None or keep_days <= 0:
            return 0
        try:
            res = await db.execute(
                "DELETE FROM mod_log_events WHERE guild_id = $1 "
                "AND created_at < NOW() - ($2 || ' days')::interval",
                int(guild_id), str(int(keep_days)),
            )
            # asyncpg returns e.g. "DELETE 12"
            return int(str(res).split()[-1]) if res else 0
        except Exception:  # noqa: BLE001
            return 0


def _humanize_type(event_type: str) -> str:
    return event_type.replace("_", " ").replace(".", " - ").strip().title()


def _format_metadata(metadata: dict[str, Any]) -> str:
    if not metadata:
        return ""
    lines: list[str] = []
    for k, v in list(metadata.items())[:12]:
        key = str(k).replace("_", " ").title()
        val = v if isinstance(v, str) else json.dumps(v, default=str)
        val = val.strip()
        if len(val) > _FIELD_VALUE_MAX:
            val = val[:_FIELD_VALUE_MAX] + " ..."
        if not val:
            continue
        lines.append(f"**{key}** {val}")
    return "\n".join(lines)
