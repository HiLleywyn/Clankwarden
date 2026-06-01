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

import discord
from discord.ext import commands

from clanklib.modlog import (
    CATEGORY_NAMES, Category, LogEvent, ModLogger, Severity, _name_of,
)
from clanklib.permissions import ModCog
from core.framework.components import Container, send_v2
from core.framework.context import DiscoContext
from core.framework.ui import C_ERROR, C_INFO, C_NAVY, C_SUCCESS


def _chan(guild: discord.Guild, cid) -> str:
    if not cid:
        return "_not set_"
    ch = guild.get_channel(int(cid))
    return ch.mention if ch else f"`{cid}` (missing)"


class ModLog(ModCog):
    def __init__(self, bot) -> None:
        super().__init__(bot)
        self.logger = ModLogger(bot)
        # Expose for every other cog (settings, clank, mod commands).
        bot.modlog = self.logger

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
        await self.logger.member(
            "member.join", member.guild.id, target=member,
            summary=f"{member.mention} joined the server.",
            metadata={"account_created": f"<t:{created}:R>",
                      "bot": "yes" if member.bot else "no"},
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

    # -- message events -------------------------------------------------------

    async def _is_log_channel(self, guild_id: int, channel_id: int) -> bool:
        """Avoid logging activity in the mod-log channel itself (no feedback loop)."""
        try:
            s = await self.bot.db.get_guild_settings(guild_id)
        except Exception:  # noqa: BLE001
            return False
        watch = {s.get("mod_log_channel"), s.get("clanktank_log_channel"), s.get("log_channel")}
        return channel_id in {int(x) for x in watch if x}

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        if await self._is_log_channel(message.guild.id, message.channel.id):
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
        if await self._is_log_channel(after.guild.id, after.channel.id):
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
        await self.logger.message(
            "message.bulk_delete", first.guild.id, severity=Severity.WARNING,
            channel=first.channel,
            summary=f"{len(messages)} messages bulk-deleted in {first.channel.mention}.",
        )

    # -- command surface ------------------------------------------------------

    @commands.group(name="modlog", aliases=["mlog"], invoke_without_command=True)
    @commands.has_guild_permissions(manage_guild=True)
    async def modlog_grp(self, ctx: DiscoContext) -> None:
        s = await self.db.get_guild_settings(ctx.guild.id)
        routes = s.get("modlog_routes") or {}
        muted = s.get("modlog_muted") or []
        rows = await self.logger.stats(ctx.guild.id, hours=24)
        stat_line = ", ".join(f"{r['category']}: {r['n']}" for r in rows) or "no events in 24h"
        route_lines = "\n".join(
            f"- `{cat}` -> {_chan(ctx.guild, cid)}" for cat, cid in routes.items()
        ) or "_none (all use the default channel)_"
        panel = (
            Container(accent_color=C_NAVY)
            .text("## Moderation Log")
            .text(
                f"**Default channel**  {_chan(ctx.guild, s.get('mod_log_channel'))}\n"
                f"**Muted categories**  {', '.join(muted) if muted else 'none'}"
            )
            .separator()
            .text("### Per-category routes\n" + route_lines)
            .separator()
            .text(f"### Last 24h\n{stat_line}")
            .separator()
            .text(
                f"-# `{ctx.prefix}modlog channel #ch` set default  *  "
                f"`route <category> #ch`  *  `mute/unmute <category>`  *  "
                f"`timeline [@user]`  *  `stats [hours]`  *  `prune <days>`  *  `test`\n"
                f"-# Categories: {', '.join(CATEGORY_NAMES)}"
            )
        )
        await send_v2(ctx, panel)

    @modlog_grp.command(name="channel")
    async def modlog_channel(self, ctx: DiscoContext, channel: discord.TextChannel | None = None) -> None:
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

    @modlog_grp.command(name="mute")
    async def modlog_mute(self, ctx: DiscoContext, category: str) -> None:
        await self._toggle_mute(ctx, category, mute=True)

    @modlog_grp.command(name="unmute")
    async def modlog_unmute(self, ctx: DiscoContext, category: str) -> None:
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
        rows = await self.logger.timeline(
            ctx.guild.id, target_id=member.id if member else None, limit=15)
        if not rows:
            await send_v2(ctx, Container(accent_color=C_INFO).text(
                "No matching events recorded yet."))
            return
        lines = []
        for r in rows:
            ts = int(r["created_at"].timestamp()) if r.get("created_at") else 0
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

    @modlog_grp.command(name="stats")
    async def modlog_stats(self, ctx: DiscoContext, hours: int = 24) -> None:
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

    @modlog_grp.command(name="test")
    async def modlog_test(self, ctx: DiscoContext) -> None:
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
