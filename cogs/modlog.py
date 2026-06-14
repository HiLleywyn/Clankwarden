"""cogs/modlog.py -- comprehensive, categorized moderation logging.

Hangs a :class:`clanklib.modlog.ModLogger` off the bot as ``bot.modlog`` so any
cog can record an event, and wires the Discord gateway events (members, roles,
channels, messages, bans, timeouts) through it. Adds the operator surface:
``.modlog`` to view + route + mute categories, a per-actor/target timeline,
24h stats, a test event and retention pruning.

Everything is best-effort: a listener never raises into the gateway, and an
unconfigured log channel simply means events are persisted (for the timeline)
but not posted.
"""
from __future__ import annotations

import logging

import discord
from discord.ext import commands, tasks

from typing import Union

from clanklib.modlog import (
    CATEGORY_NAMES, Category, LogEvent, ModLogger, Severity, _epoch,
    _ignored_channel_ids, _ignored_role_ids, _ignored_user_ids, _name_of,
)

# What `.modlog ignore` accepts as a target: a channel, a member/user, or a
# role. Channels and roles disambiguate by mention; a bare user mention resolves
# to a Member when they're in the guild (so we can see their roles) else a User.
_IgnoreTarget = Union[
    discord.TextChannel, discord.Thread, discord.Role, discord.Member, discord.User
]
from clanklib.permissions import ModCog
from core.framework.components import Container, send_v2
from core.framework.context import DiscoContext
from core.framework.ui import C_ERROR, C_INFO, C_NAVY, C_SUCCESS

log = logging.getLogger(__name__)


def _chan(guild: discord.Guild, cid) -> str:
    if not cid:
        return "_not set_"
    # get_channel_or_thread resolves threads too (get_channel does not).
    ch = guild.get_channel_or_thread(int(cid))
    if ch:
        return ch.mention
    return f"<#{int(cid)}>"


class ModLog(ModCog):
    def __init__(self, bot) -> None:
        super().__init__(bot)
        self.logger = ModLogger(bot)
        # Expose for every other cog (settings, clank, mod commands).
        bot.modlog = self.logger
        # Per-guild snapshot of invite uses, so a join can be attributed to the
        # invite that incremented: {guild_id: {code: uses}}.
        self._invite_cache: dict[int, dict[str, int]] = {}

    async def cog_load(self) -> None:
        self._retention_sweep.start()

    def cog_unload(self) -> None:
        self._retention_sweep.cancel()

    @tasks.loop(hours=24)
    async def _retention_sweep(self) -> None:
        """Enforce the audit-log retention window once a day.

        ``mod_log_events`` records message deletes/edits with the message text
        in its metadata, so the audit log accumulates raw content like
        ``clanker_evidence`` does. ``MOD_LOG_RETENTION_DAYS`` (operator setting,
        0 = keep forever) caps how long it lives; pruning the oldest rows keeps
        the tail of the tamper-evident chain intact for ``.modlog verify``."""
        from clanklib.retention import prune_mod_log
        from clanklib.settings import setting_int
        days = setting_int(self.bot, "MOD_LOG_RETENTION_DAYS", 0)
        if days <= 0:
            return
        try:
            removed = await prune_mod_log(self.bot.db, days)
        except Exception:
            log.warning("modlog: audit retention sweep failed", exc_info=True)
            return
        if removed:
            log.info("modlog: pruned %d audit row(s) older than %d day(s)", removed, days)

    @_retention_sweep.before_loop
    async def _before_retention_sweep(self) -> None:
        await self.bot.wait_until_ready()

    # -- invite tracking ------------------------------------------------------

    async def _snapshot_invites(self, guild: discord.Guild) -> dict[str, int]:
        """Read the guild's current invite uses (needs Manage Guild)."""
        me = guild.me
        if me is None or not me.guild_permissions.manage_guild:
            return {}
        try:
            invites = await guild.invites()
        except Exception:  # noqa: BLE001
            return {}
        snap = {inv.code: (inv.uses or 0) for inv in invites}
        try:
            if "VANITY_URL" in guild.features:
                vanity = await guild.vanity_invite()
                if vanity is not None:
                    snap[vanity.code] = vanity.uses or 0
        except Exception:  # noqa: BLE001
            pass
        return snap

    async def _prime_invites(self, guild: discord.Guild) -> None:
        self._invite_cache[guild.id] = await self._snapshot_invites(guild)

    async def _detect_join_invite(self, guild: discord.Guild) -> tuple[str | None, int | None]:
        """Diff current invite uses against the cached snapshot to find the one
        used for the latest join. Returns ``(code, inviter_id)`` best-effort."""
        before = self._invite_cache.get(guild.id, {})
        me = guild.me
        if me is None or not me.guild_permissions.manage_guild:
            return None, None
        try:
            invites = await guild.invites()
        except Exception:  # noqa: BLE001
            return None, None
        used_code = None
        inviter_id = None
        after: dict[str, int] = {}
        for inv in invites:
            after[inv.code] = inv.uses or 0
            if (inv.uses or 0) > before.get(inv.code, 0) and used_code is None:
                used_code = inv.code
                inviter_id = inv.inviter.id if inv.inviter else None
        # A code that vanished (hit its max uses on this join) also counts.
        if used_code is None:
            for code, uses in before.items():
                if code not in after:
                    used_code = code
                    break
        self._invite_cache[guild.id] = after
        return used_code, inviter_id

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        for guild in self.bot.guilds:
            await self._prime_invites(guild)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self._prime_invites(guild)

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite) -> None:
        if invite.guild is not None:
            await self._prime_invites(invite.guild)  # type: ignore[arg-type]

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite) -> None:
        if invite.guild is not None:
            await self._prime_invites(invite.guild)  # type: ignore[arg-type]

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        # Drop per-guild in-memory state when the bot leaves so a stale invite
        # snapshot can't mis-attribute a later join and locks/chain tips don't
        # accumulate. Persisted rows are purged separately by the clank cog.
        self._invite_cache.pop(guild.id, None)
        self.logger.clear_guild_state(guild.id)

    # -- audit-log actor enrichment -------------------------------------------

    async def _audit_actor(self, guild: discord.Guild, action: discord.AuditLogAction,
                           target_id: int | None) -> discord.abc.User | None:
        """Best-effort: find who performed a gateway action via the audit log.

        Requires View Audit Log; returns None when unavailable so the event is
        still logged, just without an attributed actor."""
        me = guild.me
        if me is None or not me.guild_permissions.view_audit_log:
            return None
        try:
            async for entry in guild.audit_logs(limit=5, action=action):
                if target_id is None or getattr(entry.target, "id", None) == target_id:
                    if entry.user and not entry.user.bot:
                        return entry.user
                    return entry.user
        except Exception:  # noqa: BLE001
            return None
        return None

    # -- member events --------------------------------------------------------

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.guild is None:
            return
        created = int(member.created_at.timestamp())
        code, inviter_id = await self._detect_join_invite(member.guild)
        meta: dict[str, str] = {
            "account_created": f"<t:{created}:R>",
            "bot": "yes" if member.bot else "no",
        }
        if code:
            meta["invite_code"] = f"`{code}`"
        if inviter_id:
            meta["invited_by"] = f"<@{inviter_id}>"
        if code and not inviter_id:
            meta["source"] = "vanity or widget invite"
        elif not code:
            meta["source"] = "unknown (bot lacks Manage Server, or a one-time/expired invite)"
        await self.logger.member(
            "member.join", member.guild.id, target=member,
            summary=f"{member.mention} joined the server.",
            metadata=meta,
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if member.guild is None:
            return
        roles = [r.mention for r in member.roles if r.name != "@everyone"]
        await self.logger.member(
            "member.leave", member.guild.id, target=member,
            summary=f"{member.mention} left the server.",
            metadata={"roles": ", ".join(roles[:20]) or "none"},
        )

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.abc.User) -> None:
        actor = await self._audit_actor(guild, discord.AuditLogAction.ban, user.id)
        await self.logger.mod(
            "member.ban", guild.id, severity=Severity.ALERT, actor=actor, target=user,
            summary=f"{_name_of(user)} was banned.",
        )

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.abc.User) -> None:
        actor = await self._audit_actor(guild, discord.AuditLogAction.unban, user.id)
        await self.logger.mod(
            "member.unban", guild.id, severity=Severity.NOTICE, actor=actor, target=user,
            summary=f"{_name_of(user)} was unbanned.",
        )

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if after.guild is None:
            return
        gid = after.guild.id
        # Timeout (communication disabled) set or lifted -- a moderation action.
        b_to = getattr(before, "timed_out_until", None)
        a_to = getattr(after, "timed_out_until", None)
        if b_to != a_to:
            actor = await self._audit_actor(after.guild,
                                            discord.AuditLogAction.member_update, after.id)
            if a_to is not None:
                await self.logger.mod(
                    "member.timeout", gid, severity=Severity.WARNING, actor=actor, target=after,
                    summary=f"{after.mention} was timed out.",
                    metadata={"until": f"<t:{int(a_to.timestamp())}:f>"},
                )
            else:
                await self.logger.mod(
                    "member.timeout_removed", gid, severity=Severity.NOTICE, actor=actor,
                    target=after, summary=f"{after.mention}'s timeout was lifted.",
                )
        # Role deltas.
        before_roles = {r.id: r for r in before.roles}
        after_roles = {r.id: r for r in after.roles}
        added = [after_roles[i] for i in after_roles.keys() - before_roles.keys()]
        removed = [before_roles[i] for i in before_roles.keys() - after_roles.keys()]
        if added or removed:
            actor = await self._audit_actor(after.guild,
                                            discord.AuditLogAction.member_role_update, after.id)
            meta = {}
            if added:
                meta["added"] = ", ".join(r.mention for r in added[:20])
            if removed:
                meta["removed"] = ", ".join(r.mention for r in removed[:20])
            await self.logger.member(
                "member.roles", gid, severity=Severity.NOTICE, actor=actor, target=after,
                summary=f"{after.mention}'s roles changed.", metadata=meta,
            )
        # Nickname change.
        if before.nick != after.nick:
            await self.logger.member(
                "member.nick", gid, target=after,
                summary=f"{after.mention} changed nickname.",
                metadata={"from": before.nick or "(none)", "to": after.nick or "(none)"},
            )

    # -- role events ----------------------------------------------------------

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        actor = await self._audit_actor(role.guild, discord.AuditLogAction.role_create, role.id)
        await self.logger.role(
            "role.create", role.guild.id, actor=actor,
            summary=f"Role {role.mention} created.",
            metadata={"name": role.name, "id": str(role.id)},
        )

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        actor = await self._audit_actor(role.guild, discord.AuditLogAction.role_delete, role.id)
        await self.logger.role(
            "role.delete", role.guild.id, severity=Severity.WARNING, actor=actor,
            summary=f"Role `{role.name}` deleted.",
            metadata={"id": str(role.id)},
        )

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role) -> None:
        changes: dict[str, str] = {}
        if before.name != after.name:
            changes["name"] = f"{before.name} -> {after.name}"
        if before.permissions != after.permissions:
            gained = [p for p, v in after.permissions if v and not getattr(before.permissions, p)]
            lost = [p for p, v in before.permissions if v and not getattr(after.permissions, p)]
            if gained:
                changes["permissions_gained"] = ", ".join(gained[:15])
            if lost:
                changes["permissions_lost"] = ", ".join(lost[:15])
        if not changes:
            return
        sev = Severity.WARNING if "permissions_gained" in changes else Severity.NOTICE
        actor = await self._audit_actor(after.guild, discord.AuditLogAction.role_update, after.id)
        await self.logger.role(
            "role.update", after.guild.id, severity=sev, actor=actor,
            summary=f"Role {after.mention} updated.", metadata=changes,
        )

    # -- channel events -------------------------------------------------------

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        actor = await self._audit_actor(channel.guild, discord.AuditLogAction.channel_create, channel.id)
        await self.logger.channel_event(
            "channel.create", channel.guild.id, actor=actor, channel=channel,
            summary=f"Channel created: {getattr(channel, 'mention', channel.name)}",
        )

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        actor = await self._audit_actor(channel.guild, discord.AuditLogAction.channel_delete, channel.id)
        await self.logger.channel_event(
            "channel.delete", channel.guild.id, severity=Severity.WARNING, actor=actor,
            summary=f"Channel `{channel.name}` deleted.", metadata={"id": str(channel.id)},
        )
        # Don't leave behind mod-log config pointing at a channel that no longer
        # exists -- prune the deleted channel from routes / ignore list / default.
        await self._prune_deleted_channel(channel.guild.id, channel.id)

    async def _prune_deleted_channel(self, guild_id: int, channel_id: int) -> None:
        try:
            s = await self.bot.db.get_guild_settings(guild_id)
        except Exception:  # noqa: BLE001
            return
        routes = s.get("modlog_routes") or {}
        live = {cat: cid for cat, cid in routes.items() if int(cid) != channel_id}
        if live != routes:
            await self.db.update_guild_setting(guild_id, "modlog_routes", live)
        ignored = list(_ignored_channel_ids(s))
        if channel_id in ignored:
            await self.db.update_guild_setting(
                guild_id, "modlog_ignored_channels",
                [c for c in ignored if c != channel_id])
        for key in ("mod_log_channel", "modlog_alert_channel"):
            try:
                if s.get(key) and int(s[key]) == channel_id:
                    await self.db.update_guild_setting(guild_id, key, None)
            except (TypeError, ValueError):
                pass

    # -- message events -------------------------------------------------------

    async def _skip_channel(self, guild_id: int, channel_id: int) -> bool:
        """Whether message activity in this channel should not be recorded.

        True for a mod-log channel itself (no feedback loop) or any channel on
        the operator's global ignore list -- the latter never even persists the
        message content, so an ignored channel is genuinely private."""
        try:
            s = await self.bot.db.get_guild_settings(guild_id)
        except Exception:  # noqa: BLE001
            return False
        watch = {s.get("mod_log_channel"), s.get("clanktank_log_channel"), s.get("log_channel")}
        if channel_id in {int(x) for x in watch if x}:
            return True
        return channel_id in _ignored_channel_ids(s)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        if await self._skip_channel(message.guild.id, message.channel.id):
            return
        if not (message.content or message.attachments):
            return
        meta = {}
        if message.attachments:
            meta["attachments"] = ", ".join(a.filename for a in message.attachments[:10])
        body = message.content or "(no text)"
        await self.logger.message(
            "message.delete", message.guild.id, actor=message.author,
            channel=message.channel,
            summary=f"Message by {message.author.mention} deleted in {message.channel.mention}.",
            metadata={"content": body, **meta},
        )

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if after.guild is None or after.author.bot:
            return
        if before.content == after.content:
            return
        if await self._skip_channel(after.guild.id, after.channel.id):
            return
        await self.logger.message(
            "message.edit", after.guild.id, actor=after.author, channel=after.channel,
            summary=f"Message by {after.author.mention} edited in {after.channel.mention}.",
            metadata={"before": before.content or "(empty)",
                      "after": after.content or "(empty)",
                      "jump": after.jump_url},
        )

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, messages: list[discord.Message]) -> None:
        if not messages:
            return
        first = messages[0]
        if first.guild is None:
            return
        if await self._skip_channel(first.guild.id, first.channel.id):
            return
        await self.logger.message(
            "message.bulk_delete", first.guild.id, severity=Severity.WARNING,
            channel=first.channel,
            summary=f"{len(messages)} messages bulk-deleted in {first.channel.mention}.",
        )

    # -- command surface ------------------------------------------------------

    @commands.group(name="modlog", aliases=["mlog"], invoke_without_command=True)
    @commands.has_guild_permissions(manage_guild=True)
    async def modlog_grp(self, ctx: DiscoContext) -> None:
        """Show the mod-log configuration."""
        s = await self.db.get_guild_settings(ctx.guild.id)
        routes = s.get("modlog_routes") or {}
        # Drop routes whose channel has since been deleted so the panel never
        # shows stale entries for channels that are not present anymore.
        live_routes = {
            cat: cid for cat, cid in routes.items()
            if ctx.guild.get_channel_or_thread(int(cid)) is not None
        }
        if live_routes != routes:
            await self.db.update_guild_setting(ctx.guild.id, "modlog_routes", live_routes)
            routes = live_routes
        muted = s.get("modlog_muted") or []
        rows = await self.logger.stats(ctx.guild.id, hours=24)
        stat_line = ", ".join(f"{r['category']}: {r['n']}" for r in rows) or "no events in 24h"
        route_lines = "\n".join(
            f"- `{cat}` -> {_chan(ctx.guild, cid)}" for cat, cid in routes.items()
        ) or "_none (all use the default channel)_"
        incident = "ON" if s.get("modlog_incident") else "OFF"
        ign_bits: list[str] = []
        if s.get("modlog_ignore_bots"):
            ign_bits.append("bots")
        for n, label in ((len(_ignored_channel_ids(s)), "channel"),
                         (len(_ignored_user_ids(s)), "user"),
                         (len(_ignored_role_ids(s)), "role")):
            if n:
                ign_bits.append(f"{n} {label}{'s' if n != 1 else ''}")
        ignored_line = ", ".join(ign_bits) if ign_bits else "none"
        panel = (
            Container(accent_color=C_NAVY)
            .text("## Moderation Log")
            .text(
                f"**Default channel**  {_chan(ctx.guild, s.get('mod_log_channel'))}\n"
                f"**Alert channel**  {_chan(ctx.guild, s.get('modlog_alert_channel'))}\n"
                f"**Muted categories**  {', '.join(muted) if muted else 'none'}\n"
                f"**Ignoring**  {ignored_line}\n"
                f"**Incident mode**  {incident}"
            )
            .separator()
            .text("### Per-category routes\n" + route_lines)
            .separator()
            .text(f"### Last 24h\n{stat_line}")
            .separator()
            .text(
                f"-# `{ctx.prefix}modlog channel #ch` set default  *  "
                f"`route <category> #ch`  *  `mute/unmute <category>`  *  "
                f"`ignore bots/#ch/@user/@role`  *  "
                f"`timeline [@user]`  *  `case <evt_id>`  *  `stats [hours]`  *  "
                f"`prune <days>`  *  `verify`  *  `alert channel/role`  *  "
                f"`incident on/off`  *  `test`\n"
                f"-# Categories: {', '.join(CATEGORY_NAMES)}"
            )
        )
        await send_v2(ctx, panel)

    @modlog_grp.command(name="channel")
    async def modlog_channel(self, ctx: DiscoContext, channel: discord.TextChannel | None = None) -> None:
        """Set the default mod-log channel."""
        new_id = channel.id if channel else None
        await self.db.update_guild_setting(ctx.guild.id, "mod_log_channel", new_id)
        await self.logger.config(
            "config.modlog_channel", ctx.guild.id, actor=ctx.author,
            summary=f"Mod log channel {'set to ' + channel.mention if channel else 'cleared'}.",
        )
        if channel:
            await send_v2(ctx, Container(accent_color=C_SUCCESS).text(
                f"Mod log channel set to {channel.mention}."))
        else:
            await send_v2(ctx, Container(accent_color=C_SUCCESS).text("Mod log channel cleared."))

    @modlog_grp.command(name="route")
    async def modlog_route(self, ctx: DiscoContext, category: str,
                           channel: discord.TextChannel | None = None) -> None:
        """Route a log category to a specific channel."""
        category = category.lower().strip()
        if category not in CATEGORY_NAMES:
            await send_v2(ctx, Container(accent_color=C_ERROR).text(
                f"Unknown category `{category}`. One of: {', '.join(CATEGORY_NAMES)}."))
            return
        s = await self.db.get_guild_settings(ctx.guild.id)
        routes = dict(s.get("modlog_routes") or {})
        if channel:
            routes[category] = channel.id
        else:
            routes.pop(category, None)
        await self.db.update_guild_setting(ctx.guild.id, "modlog_routes", routes)
        await self.logger.config(
            "config.modlog_route", ctx.guild.id, actor=ctx.author,
            summary=f"`{category}` events now route to "
                    f"{channel.mention if channel else 'the default channel'}.",
        )
        await send_v2(ctx, Container(accent_color=C_SUCCESS).text(
            f"`{category}` -> {channel.mention if channel else 'default channel'}."))

    @modlog_grp.group(name="ignore", aliases=["ignored", "exclude"], invoke_without_command=True)
    async def modlog_ignore(self, ctx: DiscoContext, *targets: _IgnoreTarget) -> None:
        """Show the ignore list, or ignore the given channels/users/roles.

        `.modlog ignore` shows everything excluded. Pass mentions to add them:
        `.modlog ignore #spam @user @role`. `.modlog ignore bots` toggles
        ignoring every bot; `remove`/`clear` undo."""
        if targets:
            await self._ignore_apply(ctx, targets, add=True)
            return
        await self._ignore_show(ctx)

    @modlog_ignore.command(name="bots", aliases=["bot"])
    async def modlog_ignore_bots(self, ctx: DiscoContext, state: str = "") -> None:
        """Toggle ignoring every bot's events (`bots`, `bots on`, `bots off`)."""
        s = await self.db.get_guild_settings(ctx.guild.id)
        cur = bool(s.get("modlog_ignore_bots"))
        state = state.lower().strip()
        if state in ("on", "yes", "true", "1", "enable", "enabled"):
            new = True
        elif state in ("off", "no", "false", "0", "disable", "disabled"):
            new = False
        else:
            new = not cur  # a bare `.modlog ignore bots` flips it
        await self.db.update_guild_setting(ctx.guild.id, "modlog_ignore_bots", new)
        await self.logger.config(
            "config.modlog_ignore", ctx.guild.id, actor=ctx.author,
            summary=f"Mod-log ignore-bots turned {'ON' if new else 'OFF'}.")
        await send_v2(ctx, Container(accent_color=C_SUCCESS).text(
            f"Bots are **{'now ignored' if new else 'no longer ignored'}** in the mod log."
            + ("" if new else " Their events are logged again.")))

    @modlog_ignore.command(name="add", aliases=["+"])
    async def modlog_ignore_add(self, ctx: DiscoContext, *targets: _IgnoreTarget) -> None:
        """Ignore one or more channels, users, or roles."""
        await self._ignore_apply(ctx, targets, add=True)

    @modlog_ignore.command(name="remove", aliases=["rm", "-", "delete", "del"])
    async def modlog_ignore_remove(self, ctx: DiscoContext, *targets: _IgnoreTarget) -> None:
        """Stop ignoring one or more channels, users, or roles."""
        await self._ignore_apply(ctx, targets, add=False)

    @modlog_ignore.command(name="clear", aliases=["reset"])
    async def modlog_ignore_clear(self, ctx: DiscoContext) -> None:
        """Clear every ignore (channels, users, roles) and stop ignoring bots."""
        s = await self.db.get_guild_settings(ctx.guild.id)
        if not (s.get("modlog_ignore_bots") or _ignored_channel_ids(s)
                or _ignored_user_ids(s) or _ignored_role_ids(s)):
            await send_v2(ctx, Container(accent_color=C_INFO).text(
                "Nothing is being ignored."))
            return
        for key in ("modlog_ignored_channels", "modlog_ignored_users", "modlog_ignored_roles"):
            await self.db.update_guild_setting(ctx.guild.id, key, [])
        await self.db.update_guild_setting(ctx.guild.id, "modlog_ignore_bots", False)
        await self.logger.config(
            "config.modlog_ignore", ctx.guild.id, actor=ctx.author,
            summary="Cleared the mod-log ignore list.")
        await send_v2(ctx, Container(accent_color=C_SUCCESS).text(
            "Ignore list cleared -- the mod log records everything again."))

    # -- ignore helpers -------------------------------------------------------

    async def _ignore_show(self, ctx: DiscoContext) -> None:
        s = await self.db.get_guild_settings(ctx.guild.id)
        chans = sorted(_ignored_channel_ids(s))
        users = sorted(_ignored_user_ids(s))
        roles = sorted(_ignored_role_ids(s))
        await send_v2(ctx, Container(accent_color=C_INFO)
                      .text("## Mod-log ignore list")
                      .text(
            f"**Bots**  {'ON' if s.get('modlog_ignore_bots') else 'OFF'}\n"
            f"**Channels**  {', '.join(_chan(ctx.guild, c) for c in chans) if chans else 'none'}\n"
            f"**Users**  {', '.join(f'<@{u}>' for u in users) if users else 'none'}\n"
            f"**Roles**  {', '.join(f'<@&{r}>' for r in roles) if roles else 'none'}")
                      .separator()
                      .text(
            f"-# Ignored events are still recorded for the timeline but never "
            f"posted to the log (and never escalate).\n"
            f"-# `{ctx.prefix}modlog ignore bots`  *  `ignore #ch @user @role`  *  "
            f"`ignore remove ...`  *  `ignore clear`"))

    async def _ignore_apply(self, ctx: DiscoContext, targets: tuple, add: bool) -> None:
        if not targets:
            verb = "add" if add else "remove"
            await send_v2(ctx, Container(accent_color=C_ERROR).text(
                f"Mention one or more channels, users, or roles, e.g. "
                f"`{ctx.prefix}modlog ignore {verb} #channel @user @role`. "
                f"(To ignore every bot: `{ctx.prefix}modlog ignore bots`.)"))
            return
        s = await self.db.get_guild_settings(ctx.guild.id)
        sets = {
            "modlog_ignored_channels": _ignored_channel_ids(s),
            "modlog_ignored_users": _ignored_user_ids(s),
            "modlog_ignored_roles": _ignored_role_ids(s),
        }
        before = {k: set(v) for k, v in sets.items()}
        labels: list[str] = []
        for t in targets:
            if isinstance(t, discord.Role):
                key = "modlog_ignored_roles"
            elif isinstance(t, (discord.Member, discord.User)):
                key = "modlog_ignored_users"
            else:
                key = "modlog_ignored_channels"
            if add:
                sets[key].add(t.id)
            else:
                sets[key].discard(t.id)
            labels.append(t.mention)
        changed = {k: v for k, v in sets.items() if v != before[k]}
        if not changed:
            await send_v2(ctx, Container(accent_color=C_INFO).text(
                "No change -- those were already "
                + ("ignored." if add else "not on the ignore list.")))
            return
        for key, ids in changed.items():
            await self.db.update_guild_setting(ctx.guild.id, key, sorted(ids))
        names = ", ".join(labels)
        await self.logger.config(
            "config.modlog_ignore", ctx.guild.id, actor=ctx.author,
            summary=f"{'Added to' if add else 'Removed from'} the mod-log ignore list: {names}.")
        total = sum(len(v) for v in sets.values())
        verb = "now ignored" if add else "no longer ignored"
        await send_v2(ctx, Container(accent_color=C_SUCCESS).text(
            f"{names} {verb}. {total} channel/user/role(s) on the ignore list."))

    @modlog_grp.command(name="mute")
    async def modlog_mute(self, ctx: DiscoContext, category: str) -> None:
        """Stop logging a category."""
        await self._toggle_mute(ctx, category, mute=True)

    @modlog_grp.command(name="unmute")
    async def modlog_unmute(self, ctx: DiscoContext, category: str) -> None:
        """Resume logging a category."""
        await self._toggle_mute(ctx, category, mute=False)

    async def _toggle_mute(self, ctx: DiscoContext, category: str, mute: bool) -> None:
        category = category.lower().strip()
        if category not in CATEGORY_NAMES:
            await send_v2(ctx, Container(accent_color=C_ERROR).text(
                f"Unknown category `{category}`. One of: {', '.join(CATEGORY_NAMES)}."))
            return
        s = await self.db.get_guild_settings(ctx.guild.id)
        muted = {str(x) for x in (s.get("modlog_muted") or [])}
        if mute:
            muted.add(category)
        else:
            muted.discard(category)
        await self.db.update_guild_setting(ctx.guild.id, "modlog_muted", sorted(muted))
        verb = "muted" if mute else "unmuted"
        await send_v2(ctx, Container(accent_color=C_SUCCESS).text(
            f"`{category}` events {verb}. They are still recorded for the timeline."))

    @modlog_grp.command(name="timeline", aliases=["history"])
    async def modlog_timeline(self, ctx: DiscoContext, member: discord.Member | None = None) -> None:
        """Show recent logged events (optionally filtered)."""
        rows = await self.logger.timeline(
            ctx.guild.id, target_id=member.id if member else None, limit=15)
        if not rows:
            await send_v2(ctx, Container(accent_color=C_INFO).text(
                "No matching events recorded yet."))
            return
        lines = []
        for r in rows:
            ts = _epoch(r.get("created_at"))
            who = f" <@{r['actor_id']}>" if r.get("actor_id") else ""
            tgt = f" -> <@{r['target_id']}>" if r.get("target_id") else ""
            lines.append(
                f"`{r['severity'][:4].upper()}` **{r['category']}** "
                f"{r['event_type']}{who}{tgt} *  <t:{ts}:R>"
            )
        title = f"Timeline for {member.display_name}" if member else "Recent moderation events"
        panel = (
            Container(accent_color=C_NAVY)
            .text(f"## {title}")
            .separator()
            .text("\n".join(lines))
            .separator()
            .text(f"-# Showing {len(rows)} most recent. Filter by member: `{ctx.prefix}modlog timeline @user`.")
        )
        await send_v2(ctx, panel)

    @modlog_grp.command(name="case", aliases=["lookup", "view", "ref"])
    async def modlog_case(self, ctx: DiscoContext, reference: str) -> None:
        """Look up one event by its reference id (`evt_...`) or row number."""
        if self.db is None:
            await self._err(ctx, "Mod-log storage is unavailable.")
            return
        ref = reference.strip().strip("`#")
        if ref.lower().startswith("evt_"):
            row = await self.db.fetch_one(
                "SELECT * FROM mod_log_events WHERE guild_id=$1 AND event_id=$2",
                ctx.guild.id, ref.lower())
        elif ref.isdigit():
            row = await self.db.fetch_one(
                "SELECT * FROM mod_log_events WHERE guild_id=$1 AND id=$2",
                ctx.guild.id, int(ref))
        else:
            await send_v2(ctx, Container(accent_color=C_ERROR).text(
                "Give an event reference like `evt_ab12cd34` or a row number."))
            return
        if row is None:
            await send_v2(ctx, Container(accent_color=C_ERROR).text(
                f"No event matching `{reference}` in this server."))
            return
        # Rebuild a LogEvent from the row and reuse the standard renderer.
        from clanklib.modlog import Category, LogEvent, Severity
        ev = LogEvent(
            category=Category(row["category"]),
            event_type=row["event_type"], guild_id=int(row["guild_id"]),
            severity=Severity(row["severity"]),
            actor=int(row["actor_id"]) if row.get("actor_id") else None,
            target=int(row["target_id"]) if row.get("target_id") else None,
            channel=int(row["channel_id"]) if row.get("channel_id") else None,
            summary=row.get("summary") or "",
            metadata=self._decode_meta(row.get("metadata")),
            event_id=row["event_id"], created_at=row["created_at"],
        )
        await ctx.reply(view=self.logger.render(ev), mention_author=False)

    @staticmethod
    def _decode_meta(raw) -> dict:
        import json
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except (TypeError, ValueError):
                return {}
        return {}

    async def _err(self, ctx: DiscoContext, msg: str) -> None:
        await send_v2(ctx, Container(accent_color=C_ERROR).text(msg))

    @modlog_grp.command(name="stats")
    async def modlog_stats(self, ctx: DiscoContext, hours: int = 24) -> None:
        """Show event counts by category over the last N hours."""
        hours = max(1, min(720, hours))
        rows = await self.logger.stats(ctx.guild.id, hours=hours)
        if not rows:
            await send_v2(ctx, Container(accent_color=C_INFO).text(
                f"No events recorded in the last {hours}h."))
            return
        total = sum(int(r["n"]) for r in rows)
        body = "\n".join(f"**{r['category']}**  {r['n']}" for r in rows)
        panel = (
            Container(accent_color=C_NAVY)
            .text(f"## Event stats -- last {hours}h")
            .separator()
            .text(body)
            .separator()
            .text(f"-# {total} event(s) total.")
        )
        await send_v2(ctx, panel)

    @modlog_grp.command(name="prune")
    async def modlog_prune(self, ctx: DiscoContext, days: int) -> None:
        """Delete logged events older than N days."""
        if days < 1:
            await send_v2(ctx, Container(accent_color=C_ERROR).text(
                "Give a number of days to keep (1 or more)."))
            return
        n = await self.logger.prune(ctx.guild.id, days)
        await self.logger.config(
            "config.modlog_prune", ctx.guild.id, actor=ctx.author,
            summary=f"Pruned mod-log events older than {days} day(s): {n} removed.",
        )
        await send_v2(ctx, Container(accent_color=C_SUCCESS).text(
            f"Removed {n} event(s) older than {days} day(s)."))

    @modlog_grp.command(name="forget", aliases=["erase", "gdpr"])
    async def modlog_forget(self, ctx: DiscoContext, user: discord.User) -> None:
        """Erase a user's stored data in this server: containment records,
        evidence, warnings, dehoist and case data. Honors a data-deletion
        request. Audit-log entries are kept for chain integrity. Irreversible."""
        from clanklib.retention import purge_user_data, total_rows
        counts = await purge_user_data(self.db, user.id, ctx.guild.id)
        removed = total_rows(counts)
        await self.logger.config(
            "config.modlog_forget", ctx.guild.id, actor=ctx.author,
            summary=f"Erased stored data for user {user.id}: {removed} row(s).",
        )
        await send_v2(ctx, Container(accent_color=C_SUCCESS).text(
            f"## Data erased\n"
            f"Removed **{removed}** stored row(s) for {user.mention} in this server "
            f"(containment, evidence, warnings, dehoist and case data).\n"
            f"-# Audit-log entries are retained for tamper-evident integrity. "
            f"This cannot be undone."))

    @modlog_grp.command(name="verify", aliases=["audit", "integrity"])
    async def modlog_verify(self, ctx: DiscoContext) -> None:
        """Walk the tamper-evident hash chain and report any break."""
        res = await self.logger.verify_chain(ctx.guild.id)
        if res["ok"]:
            await send_v2(ctx, Container(accent_color=C_SUCCESS).text(
                f"## Audit integrity verified\n"
                f"-# {res['checked']} event(s) form an unbroken hash chain -- no "
                f"row was altered or removed by anything without the chain key."))
        else:
            await send_v2(ctx, Container(accent_color=C_ERROR).text(
                f"## Audit integrity BROKEN\n"
                f"The chain breaks at event `{res['broken_at']}` "
                f"(row {res.get('row_id')}) after {res['checked']} good event(s). "
                f"A record was altered or removed outside the bot."))

    @modlog_grp.group(name="alert", invoke_without_command=True)
    async def modlog_alert(self, ctx: DiscoContext) -> None:
        """Show the alert (escalation) configuration."""
        s = await self.db.get_guild_settings(ctx.guild.id)
        role_id = s.get("modlog_alert_role")
        role = ctx.guild.get_role(int(role_id)) if role_id else None
        await send_v2(ctx, Container(accent_color=C_INFO).text(
            "## Realtime alerts\n"
            f"**Alert channel**  {_chan(ctx.guild, s.get('modlog_alert_channel'))}\n"
            f"**Alert role**  {role.mention if role else '_not set_'}\n"
            f"-# ALERT/CRITICAL events (and everything during an incident) are "
            f"mirrored here. Set with `{ctx.prefix}modlog alert channel #ch` and "
            f"`{ctx.prefix}modlog alert role @role`."))

    @modlog_alert.command(name="channel")
    async def modlog_alert_channel(self, ctx: DiscoContext, channel: discord.TextChannel | None = None) -> None:
        """Set the channel for high-severity alerts."""
        await self.db.update_guild_setting(ctx.guild.id, "modlog_alert_channel",
                                           channel.id if channel else None)
        await self._ok(ctx, f"Alert channel {'set to ' + channel.mention if channel else 'cleared'}.")

    @modlog_alert.command(name="role")
    async def modlog_alert_role(self, ctx: DiscoContext, role: discord.Role | None = None) -> None:
        """Set the role pinged on high-severity alerts."""
        await self.db.update_guild_setting(ctx.guild.id, "modlog_alert_role",
                                           role.id if role else None)
        await self._ok(ctx, f"Alert role {'set to ' + role.mention if role else 'cleared'}.")

    @modlog_grp.command(name="incident")
    async def modlog_incident(self, ctx: DiscoContext, state: str = "") -> None:
        """Turn incident mode on/off: unmute every category and mirror all to alerts."""
        state = state.lower().strip()
        if state not in ("on", "off"):
            s = await self.db.get_guild_settings(ctx.guild.id)
            cur = "ON" if s.get("modlog_incident") else "OFF"
            await send_v2(ctx, Container(accent_color=C_INFO).text(
                f"Incident mode is **{cur}**. Use `{ctx.prefix}modlog incident on` or `off`."))
            return
        on = state == "on"
        await self.db.update_guild_setting(ctx.guild.id, "modlog_incident", on)
        from clanklib.modlog import Severity
        await self.logger.config(
            "config.incident", ctx.guild.id, actor=ctx.author,
            severity=Severity.ALERT if on else Severity.NOTICE,
            summary=f"Incident mode turned {'ON' if on else 'OFF'}.")
        await self._ok(ctx, f"Incident mode **{'ON' if on else 'OFF'}**."
                            + (" Every category now mirrors to the alert channel." if on else ""))

    async def _ok(self, ctx: DiscoContext, msg: str) -> None:
        await send_v2(ctx, Container(accent_color=C_SUCCESS).text(msg))

    @modlog_grp.command(name="test")
    async def modlog_test(self, ctx: DiscoContext) -> None:
        """Emit a test event to verify logging works."""
        await self.logger.emit(LogEvent(
            category=Category.INFRASTRUCTURE, event_type="modlog.test",
            guild_id=ctx.guild.id, severity=Severity.NOTICE, actor=ctx.author,
            summary="Test event from `.modlog test`.",
            metadata={"note": "If you see this in the log channel, routing works."},
        ))
        await send_v2(ctx, Container(accent_color=C_SUCCESS).text(
            "Test event emitted. Check the configured mod log channel."))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ModLog(bot))
