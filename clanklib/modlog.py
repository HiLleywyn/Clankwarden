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
* ``modlog_ignored_channels`` -- a global list of channel ids whose activity is
  excluded from the log: any event originating in one of these channels is never
  posted (and never escalates), regardless of category or routing.
* ``modlog_ignore_bots``      -- when true, any event whose actor or target is a
  bot is excluded (stops the log being flooded by other bots).
* ``modlog_ignored_users``    -- a list of user ids excluded as actor or target.
* ``modlog_ignored_roles``    -- a list of role ids; any actor/target member who
  has one of these roles is excluded.

All ASCII; no embeds.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import time
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

# Categories that currently have a live event producer -- a gateway listener,
# command, or detector actually emits them at runtime. The remaining categories
# (command, ai, clanktank, analytics) expose convenience methods on ModLogger
# but nothing calls them yet, so a routed channel for them would never receive a
# single event. `.init` provisions a log channel only for producing categories
# so an operator never ends up staring at channels that stay permanently empty.
# Keep this in sync with the emitters in cogs/modlog.py and the anomaly detector.
PRODUCING_CATEGORY_NAMES: tuple[str, ...] = (
    "security", "moderation", "member", "message",
    "role", "channel", "config", "infrastructure",
)
PRODUCING_CATEGORIES: tuple["Category", ...] = tuple(
    Category(name) for name in PRODUCING_CATEGORY_NAMES
)


def _id_set(raw: Any) -> set[int]:
    """Coerce a stored list of ids into a set of ints.

    The ignore lists live in the ``features`` JSONB, so after a round-trip their
    ids may come back as ints or strings; coerce both and silently drop anything
    non-numeric so a malformed entry can never raise."""
    if not isinstance(raw, (list, tuple, set)):
        return set()
    out: set[int] = set()
    for item in raw:
        try:
            out.add(int(item))
        except (TypeError, ValueError):
            continue
    return out


def _ignored_channel_ids(settings: dict) -> set[int]:
    """The per-guild set of channel ids excluded from the mod log."""
    return _id_set(settings.get("modlog_ignored_channels"))


def _ignored_user_ids(settings: dict) -> set[int]:
    """The per-guild set of user ids excluded from the mod log."""
    return _id_set(settings.get("modlog_ignored_users"))


def _ignored_role_ids(settings: dict) -> set[int]:
    """The per-guild set of role ids whose members are excluded from the mod log."""
    return _id_set(settings.get("modlog_ignored_roles"))


def _short_id() -> str:
    """A short, unguessable, sortable-ish public event id."""
    return "evt_" + secrets.token_hex(5)


def _epoch(value: Any) -> int:
    """Coerce a timestamp (datetime, epoch float/int, or None) to epoch seconds.

    The data plane returns TIMESTAMPTZ columns as epoch floats, so anything that
    rebuilds an event from a row must not assume a datetime."""
    if value is None:
        return int(time.time())
    if isinstance(value, (int, float)):
        return int(value)
    ts = getattr(value, "timestamp", None)
    if callable(ts):
        try:
            return int(value.timestamp())
        except Exception:  # noqa: BLE001
            pass
    return int(time.time())


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
        # Tamper-evident hash chain: the latest hash per guild, plus a per-guild
        # lock so concurrent persists chain in a defined order.
        self._chain_tip: dict[int, str] = {}
        self._chain_lock: dict[int, "asyncio.Lock"] = {}
        # Anomaly detection: sliding windows of recent event timestamps, keyed by
        # (guild_id, bucket). Bucket is a coarse signal like "join" or
        # "mod:<actor_id>"; a burst trips a security anomaly + alert.
        self._windows: dict[tuple, list[float]] = {}

    def _lock_for(self, guild_id: int) -> "asyncio.Lock":
        lock = self._chain_lock.get(guild_id)
        if lock is None:
            import asyncio as _asyncio
            lock = _asyncio.Lock()
            self._chain_lock[guild_id] = lock
        return lock

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
        # Anomaly detection runs after the event is recorded so the anomaly
        # itself is anchored after its triggering events in the chain.
        try:
            await self._detect_anomaly(event)
        except Exception:  # noqa: BLE001
            log.debug("modlog anomaly check failed", exc_info=True)
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

    def _event_hash(self, prev: str, event: LogEvent) -> str:
        payload = "|".join(str(x) for x in (
            prev, event.event_id, _epoch(event.created_at),
            event.category.value, event.severity.value, event.event_type,
            event.actor_id or 0, event.target_id or 0, event.summary,
        ))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def _chain_tip_for(self, db: Any, guild_id: int) -> str:
        """The latest hash in a guild's chain (cached; seeded from the DB)."""
        if guild_id in self._chain_tip:
            return self._chain_tip[guild_id]
        try:
            row = await db.fetch_one(
                "SELECT hash FROM mod_log_events WHERE guild_id=$1 AND hash IS NOT NULL "
                "ORDER BY id DESC LIMIT 1", guild_id)
            tip = (row or {}).get("hash") or ""
        except Exception:  # noqa: BLE001
            tip = ""
        self._chain_tip[guild_id] = tip
        return tip

    async def _persist(self, event: LogEvent) -> None:
        db = self.db
        if db is None:
            return
        async with self._lock_for(event.guild_id):
            try:
                prev = await self._chain_tip_for(db, event.guild_id)
                digest = self._event_hash(prev, event)
                meta = json.dumps(event.metadata)[:_METADATA_BYTES_MAX]
                await db.execute(
                    "INSERT INTO mod_log_events "
                    "(event_id, guild_id, created_at, category, severity, event_type, "
                    " actor_id, target_id, channel_id, summary, metadata, prev_hash, hash) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12,$13) "
                    "ON CONFLICT (guild_id, event_id) DO NOTHING",
                    event.event_id, event.guild_id, event.created_at,
                    event.category.value, event.severity.value, event.event_type,
                    event.actor_id, event.target_id, event.channel_id,
                    event.summary, meta, prev, digest,
                )
                self._chain_tip[event.guild_id] = digest
            except Exception:  # noqa: BLE001
                log.debug("modlog persist failed", exc_info=True)

    async def verify_chain(self, guild_id: int, limit: int = 5000) -> dict:
        """Walk the hash chain in order and report the first break, if any."""
        db = self.db
        if db is None:
            return {"ok": True, "checked": 0, "broken_at": None}
        rows = await db.fetch_all(
            "SELECT id, event_id, created_at, category, severity, event_type, "
            "actor_id, target_id, summary, prev_hash, hash "
            "FROM mod_log_events WHERE guild_id=$1 AND hash IS NOT NULL "
            "ORDER BY id ASC LIMIT $2", int(guild_id), int(limit))
        prev = ""
        checked = 0
        for r in rows:
            ev = LogEvent(
                category=Category(r["category"]), event_type=r["event_type"],
                guild_id=int(guild_id), severity=Severity(r["severity"]),
                actor=int(r["actor_id"]) if r.get("actor_id") else None,
                target=int(r["target_id"]) if r.get("target_id") else None,
                summary=r.get("summary") or "", event_id=r["event_id"],
                created_at=r["created_at"],
            )
            expected = self._event_hash(prev, ev)
            if r.get("prev_hash") != prev or r.get("hash") != expected:
                return {"ok": False, "checked": checked,
                        "broken_at": r["event_id"], "row_id": r["id"]}
            prev = r["hash"]
            checked += 1
        return {"ok": True, "checked": checked, "broken_at": None}

    async def _dispatch(self, event: LogEvent) -> None:
        try:
            s = await self._guild_settings(event.guild_id)
            # Anything on the global ignore list (channel, bot, user, role) is
            # excluded entirely: the event is still persisted (for the timeline +
            # hash chain) but is never posted to the log and never escalates.
            if await self.is_ignored_event(event, s):
                return
            incident = bool(s.get("modlog_incident"))
            # Incident mode forces every category through (nothing stays muted)
            # so the full picture is visible during an active situation.
            if not incident and await self._is_muted(event.guild_id, event.category):
                pass  # still falls through to alert escalation below
            else:
                channel = await self._resolve_channel(event.guild_id, event.category)
                if channel is not None:
                    try:
                        await channel.send(view=self.render(event))
                    except discord.Forbidden:
                        log.debug("modlog: missing perms in log channel g=%s", event.guild_id)
            # Realtime alert escalation: ALERT+ (always during an incident) is
            # mirrored to the alert channel with an optional role ping.
            if event.severity.rank >= Severity.ALERT.rank or incident:
                await self._escalate(event, s)
        except Exception:  # noqa: BLE001
            log.debug("modlog dispatch failed", exc_info=True)

    async def _escalate(self, event: LogEvent, settings: dict) -> None:
        cid = settings.get("modlog_alert_channel") or settings.get("mod_log_channel")
        if not cid:
            return
        try:
            cid = int(cid)
        except (TypeError, ValueError):
            return
        ch = self.bot.get_channel(cid)
        if ch is None or not isinstance(ch, (discord.TextChannel, discord.Thread)):
            return
        role_id = settings.get("modlog_alert_role")
        content = None
        allowed = discord.AllowedMentions.none()
        if role_id:
            content = f"<@&{int(role_id)}>"
            allowed = discord.AllowedMentions(roles=True)
        try:
            await ch.send(content=content, view=self.render(event), allowed_mentions=allowed)
        except discord.Forbidden:
            log.debug("modlog: missing perms in alert channel g=%s", event.guild_id)

    # -- anomaly detection ----------------------------------------------------

    # (bucket, window seconds, threshold, message) -- a burst trips a security
    # event. Tuned conservatively so normal activity never trips it.
    _ANOMALY_RULES = {
        "join": (60.0, 10, "Possible raid: {n} joins in {w}s."),
        "ban": (60.0, 5, "Mass-ban burst: {n} bans in {w}s."),
        "message.delete": (30.0, 20, "Message-purge burst: {n} deletions in {w}s."),
    }

    def _bump_window(self, key: tuple, window_s: float) -> int:
        now = time.monotonic()
        bucket = self._windows.setdefault(key, [])
        cutoff = now - window_s
        bucket[:] = [t for t in bucket if t >= cutoff]
        bucket.append(now)
        return len(bucket)

    async def _detect_anomaly(self, event: LogEvent) -> None:
        # Map an event to a coarse anomaly bucket.
        bucket = None
        if event.event_type == "member.join":
            bucket = "join"
        elif event.event_type == "member.ban":
            bucket = "ban"
        elif event.event_type in ("message.delete", "message.bulk_delete", "message.purge"):
            bucket = "message.delete"
        if bucket is None or bucket not in self._ANOMALY_RULES:
            return
        window_s, threshold, template = self._ANOMALY_RULES[bucket]
        n = self._bump_window((event.guild_id, bucket), window_s)
        if n != threshold:  # fire once, exactly when the threshold is crossed
            return
        # Avoid recursing into anomaly detection for the anomaly event itself by
        # persisting + dispatching directly.
        anomaly = LogEvent(
            category=Category.SECURITY, event_type=f"anomaly.{bucket}",
            guild_id=event.guild_id, severity=Severity.CRITICAL,
            summary=template.format(n=n, w=int(window_s)),
            metadata={"window_seconds": int(window_s), "count": n,
                      "trigger": event.event_type},
        )
        await self._persist(anomaly)
        await self._dispatch(anomaly)

    async def _guild_settings(self, guild_id: int) -> dict:
        db = self.db
        if db is None:
            return {}
        try:
            return await db.get_guild_settings(int(guild_id)) or {}
        except Exception:  # noqa: BLE001
            return {}

    async def is_ignored_channel(self, guild_id: int, channel_id: Optional[int]) -> bool:
        """Whether a channel is on the guild's global mod-log ignore list."""
        if not channel_id:
            return False
        s = await self._guild_settings(guild_id)
        return int(channel_id) in _ignored_channel_ids(s)

    async def _bot_and_roles(self, guild_id: int, who: Any,
                             wid: Optional[int]) -> tuple[bool, set[int]]:
        """Best-effort: is ``who`` a bot, and what role ids does it carry?

        ``who`` may already be a Member/User (the listeners pass objects) or just
        an id (rebuilt events); resolve from the guild when we only have an id.
        Never raises -- a failed lookup just yields what we know."""
        is_bot = bool(getattr(who, "bot", False))
        role_objs = getattr(who, "roles", None)
        if role_objs is None and wid and self.bot is not None:
            try:
                guild = self.bot.get_guild(int(guild_id))
                member = guild.get_member(int(wid)) if guild else None
            except Exception:  # noqa: BLE001
                member = None
            if member is not None:
                is_bot = is_bot or bool(getattr(member, "bot", False))
                role_objs = getattr(member, "roles", None)
            else:
                try:
                    user = self.bot.get_user(int(wid))
                except Exception:  # noqa: BLE001
                    user = None
                if user is not None:
                    is_bot = is_bot or bool(getattr(user, "bot", False))
        role_ids: set[int] = set()
        if role_objs:
            for r in role_objs:
                rid = getattr(r, "id", None)
                try:
                    if rid is not None:
                        role_ids.add(int(rid))
                except (TypeError, ValueError):
                    continue
        return is_bot, role_ids

    async def is_ignored_event(self, event: LogEvent, settings: dict) -> bool:
        """Whether an event is excluded by the guild's ignore lists.

        Covers ignored channels, the ignore-bots toggle, ignored users and
        ignored roles. Checked at dispatch so every event source is filtered
        from one place. Never raises -- on any error the event is NOT ignored
        (fail open, so we under-suppress rather than silently drop audit data)."""
        try:
            if event.channel_id is not None and event.channel_id in _ignored_channel_ids(settings):
                return True
            ignore_bots = bool(settings.get("modlog_ignore_bots"))
            users = _ignored_user_ids(settings)
            roles = _ignored_role_ids(settings)
            if not (ignore_bots or users or roles):
                return False
            for who in (event.actor, event.target):
                if who is None:
                    continue
                wid = _coerce_id(who)
                if wid is not None and wid in users:
                    return True
                if ignore_bots or roles:
                    is_bot, role_ids = await self._bot_and_roles(event.guild_id, who, wid)
                    if ignore_bots and is_bot:
                        return True
                    if roles and (role_ids & roles):
                        return True
        except Exception:  # noqa: BLE001
            log.debug("modlog ignore check failed", exc_info=True)
        return False

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
        ts = _epoch(event.created_at)
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
