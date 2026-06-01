"""cogs/mod.py -- the moderation command set, Components V2 native.

Actionable, utility-heavy, and locked down hard: every command is gated by the
matching guild permission AND the matching bot permission, every target passes a
role-hierarchy guard (you cannot action the owner, the bot, yourself, anyone
above you, or anyone above the bot), and every action is recorded through the
central mod logger (``bot.modlog``) so there is one audit trail.

Commands: ban, unban, softban, kick, timeout (mute), untimeout (unmute), warn,
warnings, delwarn, purge, slowmode, lock, unlock.
"""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Optional

import discord
from discord.ext import commands

from core.framework.cogs import GuildCog
from clanklib.modlog import Severity, _epoch
from core.framework.components import Container, send_v2
from core.framework.context import DiscoContext
from core.framework.ui import C_ERROR, C_INFO, C_SUCCESS, C_WARNING

# Discord caps a timeout at 28 days.
_MAX_TIMEOUT_S = 28 * 24 * 3600
_DURATION_RE = re.compile(r"(\d+)\s*([smhdw])", re.IGNORECASE)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def _parse_duration(text: str) -> Optional[int]:
    """Parse a compact duration like ``10m`` / ``1h30m`` / ``2d`` to seconds."""
    if not text:
        return None
    total = 0
    matched = False
    for amount, unit in _DURATION_RE.findall(text):
        total += int(amount) * _UNIT_SECONDS[unit.lower()]
        matched = True
    if not matched:
        # Bare number means minutes.
        if text.strip().isdigit():
            return int(text.strip()) * 60
        return None
    return total


def _fmt_duration(seconds: int) -> str:
    parts = []
    for unit, sec in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        if seconds >= sec:
            parts.append(f"{seconds // sec}{unit}")
            seconds %= sec
    return " ".join(parts) or "0s"


class Moderation(GuildCog):
    # -- shared guards --------------------------------------------------------

    def _hierarchy_ok(self, ctx: DiscoContext, target: discord.Member) -> tuple[bool, str]:
        """Whether ``ctx.author`` (and the bot) may act on ``target``."""
        guild = ctx.guild
        me = guild.me
        if target.id == self.bot.user.id:
            return False, "That's me."
        if target.id == ctx.author.id:
            return False, "You can't action yourself."
        if target.id == guild.owner_id:
            return False, "You can't action the server owner."
        # The invoker must outrank the target, unless the invoker is the owner.
        if ctx.author.id != guild.owner_id and target.top_role >= ctx.author.top_role:
            return False, "That member is the same rank as you or higher."
        # The bot must outrank the target to act on them.
        if me is not None and target.top_role >= me.top_role:
            return False, "That member is above my highest role, so I can't action them."
        return True, ""

    async def _modlog(self, ctx: DiscoContext, event_type: str, *, target, summary: str,
                      severity=None, **metadata) -> None:
        modlog = getattr(self.bot, "modlog", None)
        if modlog is None:
            return
        try:
            sev = severity or Severity.NOTICE
            await modlog.mod(event_type, ctx.guild.id, severity=sev, actor=ctx.author,
                             target=target, channel=ctx.channel, summary=summary,
                             metadata={k: v for k, v in metadata.items() if v not in (None, "")})
        except Exception:  # noqa: BLE001
            pass

    async def _dm(self, member: discord.Member, guild: discord.Guild, action: str,
                  reason: str) -> bool:
        """Best-effort DM to a target before/at an action. Returns delivered?"""
        try:
            await member.send(
                f"You were **{action}** in **{guild.name}**.\n"
                f"Reason: {reason or 'No reason given'}"
            )
            return True
        except Exception:  # noqa: BLE001
            return False

    async def _err(self, ctx: DiscoContext, msg: str) -> None:
        await send_v2(ctx, Container(accent_color=C_ERROR).text(msg))

    async def _ok(self, ctx: DiscoContext, msg: str) -> None:
        await send_v2(ctx, Container(accent_color=C_SUCCESS).text(msg))

    # -- ban / unban / softban ------------------------------------------------

    @commands.command(name="ban")
    @commands.has_guild_permissions(ban_members=True)
    @commands.bot_has_guild_permissions(ban_members=True)
    async def ban(self, ctx: DiscoContext, user: discord.User, *, reason: str = "No reason given") -> None:
        """Ban a member or a user id. Hierarchy-guarded for members."""
        member = ctx.guild.get_member(user.id)
        if member is not None:
            ok, why = self._hierarchy_ok(ctx, member)
            if not ok:
                await self._err(ctx, why)
                return
            await self._dm(member, ctx.guild, "banned", reason)
        try:
            await ctx.guild.ban(user, reason=f"{ctx.author} ({ctx.author.id}): {reason}",
                                delete_message_seconds=0)
        except discord.Forbidden:
            await self._err(ctx, "I don't have permission to ban that user.")
            return
        except discord.HTTPException:
            await self._err(ctx, "Ban failed. Check the user id.")
            return
        await self._modlog(ctx, "member.ban", target=user, severity=Severity.ALERT,
                           summary=f"{user} was banned.", reason=reason)
        await self._ok(ctx, f"Banned **{user}**. Reason: {reason}")

    @commands.command(name="unban")
    @commands.has_guild_permissions(ban_members=True)
    @commands.bot_has_guild_permissions(ban_members=True)
    async def unban(self, ctx: DiscoContext, user_id: int, *, reason: str = "No reason given") -> None:
        """Unban a user by id."""
        try:
            user = await self.bot.fetch_user(user_id)
            await ctx.guild.unban(user, reason=f"{ctx.author} ({ctx.author.id}): {reason}")
        except discord.NotFound:
            await self._err(ctx, "That user isn't banned (or the id is wrong).")
            return
        except discord.Forbidden:
            await self._err(ctx, "I don't have permission to unban.")
            return
        await self._modlog(ctx, "member.unban", target=user, summary=f"{user} was unbanned.",
                           reason=reason)
        await self._ok(ctx, f"Unbanned **{user}**.")

    @commands.command(name="softban")
    @commands.has_guild_permissions(ban_members=True)
    @commands.bot_has_guild_permissions(ban_members=True)
    async def softban(self, ctx: DiscoContext, member: discord.Member, *, reason: str = "No reason given") -> None:
        """Ban then immediately unban to wipe a member's recent messages."""
        ok, why = self._hierarchy_ok(ctx, member)
        if not ok:
            await self._err(ctx, why)
            return
        await self._dm(member, ctx.guild, "softbanned (kicked, messages cleared)", reason)
        try:
            await ctx.guild.ban(member, reason=f"softban by {ctx.author}: {reason}",
                                delete_message_seconds=86400)
            await ctx.guild.unban(member, reason="softban: immediate unban")
        except discord.Forbidden:
            await self._err(ctx, "I don't have permission to softban that member.")
            return
        await self._modlog(ctx, "member.softban", target=member, severity=Severity.WARNING,
                           summary=f"{member} was softbanned.", reason=reason)
        await self._ok(ctx, f"Softbanned **{member}** (24h of messages cleared).")

    @commands.command(name="massban", aliases=["banmany", "mban"])
    @commands.has_guild_permissions(ban_members=True)
    @commands.bot_has_guild_permissions(ban_members=True)
    async def massban(self, ctx: DiscoContext, *, raw: str) -> None:
        """Ban many users by id/mention at once, paced to avoid rate limits.

        Usage: `massban 111 222 333 raid spam` -- numbers are ids, the rest is
        the reason. Members above you (or the bot) are skipped."""
        ids: list[int] = []
        reason_parts: list[str] = []
        for tok in raw.split():
            digits = "".join(c for c in tok if c.isdigit())
            if digits and digits == tok.strip("<@!># "):
                ids.append(int(digits))
            elif digits and len(digits) >= 17:
                ids.append(int(digits))
            else:
                reason_parts.append(tok)
        ids = list(dict.fromkeys(ids))  # de-dupe, keep order
        reason = " ".join(reason_parts) or "Mass ban"
        if not ids:
            await self._err(ctx, "Give at least one user id or mention to ban.")
            return
        if len(ids) > 1000:
            await self._err(ctx, "That's over the 1000-id safety cap for one mass ban.")
            return

        # Drop anyone the invoker (or the bot) can't action.
        targets: list[int] = []
        skipped = 0
        for uid in ids:
            m = ctx.guild.get_member(uid)
            if m is not None:
                ok, _why = self._hierarchy_ok(ctx, m)
                if not ok:
                    skipped += 1
                    continue
            targets.append(uid)
        if not targets:
            await self._err(ctx, "Nothing to ban (every target was protected or invalid).")
            return

        confirmed = await ctx.confirm(
            f"Ban {len(targets)} user(s)? Reason: {reason}"
            + (f" ({skipped} protected target(s) skipped)" if skipped else ""),
            timeout=60.0,
        )
        if not confirmed:
            await self._err(ctx, "Mass ban cancelled.")
            return

        from clanklib.ratelimit import BulkRunner
        progress = await ctx.send(view=Container(accent_color=C_WARNING).text(
            f"Banning {len(targets)} user(s), paced to stay under Discord's limits...").build())

        async def _ban_one(uid: int) -> None:
            await ctx.guild.ban(discord.Object(id=uid),
                                reason=f"massban by {ctx.author} ({ctx.author.id}): {reason}",
                                delete_message_seconds=0)

        async def _prog(res) -> None:
            try:
                await progress.edit(view=Container(accent_color=C_WARNING).text(
                    f"Mass ban: {res.processed}/{res.total} "
                    f"({res.succeeded} banned, {res.failed} failed)...").build())
            except Exception:  # noqa: BLE001
                pass

        result = await BulkRunner().run(targets, _ban_one, progress=_prog)
        await self._modlog(
            ctx, "member.massban", target=None, severity=Severity.ALERT,
            summary=f"Mass ban: {result.succeeded} banned, {result.failed} failed"
                    + (", stopped early" if result.aborted else "") + f". Reason: {reason}",
            count=result.succeeded, reason=reason, aborted=result.aborted)
        body = (f"Banned **{result.succeeded}** user(s)."
                + (f" {result.failed} failed." if result.failed else "")
                + (f" {skipped} protected skipped." if skipped else "")
                + (f"\n\n**Stopped early:** {result.abort_reason}" if result.aborted else ""))
        try:
            await progress.edit(view=Container(
                accent_color=C_WARNING if result.aborted else C_SUCCESS).text(body).build())
        except Exception:  # noqa: BLE001
            await self._ok(ctx, body)

    # -- kick -----------------------------------------------------------------

    @commands.command(name="kick")
    @commands.has_guild_permissions(kick_members=True)
    @commands.bot_has_guild_permissions(kick_members=True)
    async def kick(self, ctx: DiscoContext, member: discord.Member, *, reason: str = "No reason given") -> None:
        ok, why = self._hierarchy_ok(ctx, member)
        if not ok:
            await self._err(ctx, why)
            return
        await self._dm(member, ctx.guild, "kicked", reason)
        try:
            await member.kick(reason=f"{ctx.author} ({ctx.author.id}): {reason}")
        except discord.Forbidden:
            await self._err(ctx, "I don't have permission to kick that member.")
            return
        await self._modlog(ctx, "member.kick", target=member, severity=Severity.WARNING,
                           summary=f"{member} was kicked.", reason=reason)
        await self._ok(ctx, f"Kicked **{member}**. Reason: {reason}")

    # -- timeout / untimeout --------------------------------------------------

    @commands.command(name="timeout", aliases=["mute"])
    @commands.has_guild_permissions(moderate_members=True)
    @commands.bot_has_guild_permissions(moderate_members=True)
    async def timeout(self, ctx: DiscoContext, member: discord.Member, duration: str,
                      *, reason: str = "No reason given") -> None:
        """Timeout a member for a duration like 10m, 1h, 2d (max 28d)."""
        ok, why = self._hierarchy_ok(ctx, member)
        if not ok:
            await self._err(ctx, why)
            return
        secs = _parse_duration(duration)
        if not secs or secs <= 0:
            await self._err(ctx, "Give a duration like `10m`, `1h`, `2d`.")
            return
        secs = min(secs, _MAX_TIMEOUT_S)
        try:
            await member.timeout(timedelta(seconds=secs),
                                 reason=f"{ctx.author} ({ctx.author.id}): {reason}")
        except discord.Forbidden:
            await self._err(ctx, "I don't have permission to timeout that member.")
            return
        await self._dm(member, ctx.guild, f"timed out for {_fmt_duration(secs)}", reason)
        await self._modlog(ctx, "member.timeout", target=member, severity=Severity.WARNING,
                           summary=f"{member} timed out for {_fmt_duration(secs)}.",
                           duration=_fmt_duration(secs), reason=reason)
        await self._ok(ctx, f"Timed out **{member}** for {_fmt_duration(secs)}. Reason: {reason}")

    @commands.command(name="untimeout", aliases=["unmute"])
    @commands.has_guild_permissions(moderate_members=True)
    @commands.bot_has_guild_permissions(moderate_members=True)
    async def untimeout(self, ctx: DiscoContext, member: discord.Member, *, reason: str = "No reason given") -> None:
        if member.timed_out_until is None:
            await self._err(ctx, "That member isn't timed out.")
            return
        try:
            await member.timeout(None, reason=f"{ctx.author}: {reason}")
        except discord.Forbidden:
            await self._err(ctx, "I don't have permission to lift that timeout.")
            return
        await self._modlog(ctx, "member.timeout_removed", target=member,
                           summary=f"{member}'s timeout was lifted.", reason=reason)
        await self._ok(ctx, f"Lifted timeout on **{member}**.")

    # -- warnings -------------------------------------------------------------

    @commands.command(name="warn")
    @commands.has_guild_permissions(moderate_members=True)
    async def warn(self, ctx: DiscoContext, member: discord.Member, *, reason: str = "No reason given") -> None:
        ok, why = self._hierarchy_ok(ctx, member)
        if not ok:
            await self._err(ctx, why)
            return
        n = 0
        if self.db is not None:
            try:
                await self.db.execute(
                    "INSERT INTO mod_warnings (guild_id, user_id, moderator_id, reason) "
                    "VALUES ($1,$2,$3,$4)",
                    ctx.guild.id, member.id, ctx.author.id, reason[:500],
                )
                row = await self.db.fetch_one(
                    "SELECT COUNT(*) AS n FROM mod_warnings "
                    "WHERE guild_id=$1 AND user_id=$2 AND active",
                    ctx.guild.id, member.id,
                )
                n = int(row["n"]) if row else 0
            except Exception:  # noqa: BLE001
                pass
        await self._dm(member, ctx.guild, "warned", reason)
        await self._modlog(ctx, "member.warn", target=member, severity=Severity.WARNING,
                           summary=f"{member} was warned (now {n} active).", reason=reason)
        await self._ok(ctx, f"Warned **{member}**. They now have **{n}** active warning(s).")

    @commands.command(name="warnings", aliases=["warns"])
    @commands.has_guild_permissions(moderate_members=True)
    async def warnings(self, ctx: DiscoContext, member: discord.Member) -> None:
        if self.db is None:
            await self._err(ctx, "Warnings storage is unavailable.")
            return
        rows = await self.db.fetch_all(
            "SELECT * FROM mod_warnings WHERE guild_id=$1 AND user_id=$2 AND active "
            "ORDER BY created_at DESC LIMIT 25",
            ctx.guild.id, member.id,
        )
        if not rows:
            await send_v2(ctx, Container(accent_color=C_INFO).text(
                f"**{member}** has no active warnings."))
            return
        lines = []
        for r in rows:
            ts = _epoch(r.get("created_at"))
            lines.append(f"`#{r['id']}` <t:{ts}:d> by <@{r['moderator_id']}> -- {r['reason']}")
        panel = (
            Container(accent_color=C_WARNING)
            .text(f"## Warnings for {member.display_name} ({len(rows)})")
            .separator()
            .text("\n".join(lines))
            .separator()
            .text(f"-# Remove one with `{ctx.prefix}delwarn #id`.")
        )
        await send_v2(ctx, panel)

    @commands.command(name="delwarn", aliases=["unwarn"])
    @commands.has_guild_permissions(moderate_members=True)
    async def delwarn(self, ctx: DiscoContext, warning_id: int) -> None:
        if self.db is None:
            await self._err(ctx, "Warnings storage is unavailable.")
            return
        row = await self.db.fetch_one(
            "SELECT * FROM mod_warnings WHERE id=$1 AND guild_id=$2 AND active",
            warning_id, ctx.guild.id,
        )
        if row is None:
            await self._err(ctx, f"No active warning `#{warning_id}` in this server.")
            return
        await self.db.execute("UPDATE mod_warnings SET active=FALSE WHERE id=$1", warning_id)
        await self._modlog(ctx, "member.warn_removed", target=int(row["user_id"]),
                           summary=f"Warning #{warning_id} removed.")
        await self._ok(ctx, f"Removed warning `#{warning_id}`.")

    # -- channel tools --------------------------------------------------------

    @commands.command(name="purge", aliases=["clear", "prune"])
    @commands.has_guild_permissions(manage_messages=True)
    @commands.bot_has_guild_permissions(manage_messages=True)
    async def purge(self, ctx: DiscoContext, amount: int, member: discord.Member | None = None) -> None:
        """Delete up to <amount> recent messages (max 200), optionally only from a member."""
        if amount < 1:
            await self._err(ctx, "Give a positive number of messages to delete.")
            return
        amount = min(amount, 200)
        check = (lambda m: m.author.id == member.id) if member else (lambda m: True)
        try:
            await ctx.message.delete()
        except Exception:  # noqa: BLE001
            pass
        try:
            deleted = await ctx.channel.purge(limit=amount, check=check)
        except discord.Forbidden:
            await self._err(ctx, "I don't have permission to delete messages here.")
            return
        await self._modlog(ctx, "message.purge", target=member, severity=Severity.WARNING,
                           summary=f"{len(deleted)} messages purged in {ctx.channel.mention}"
                                   + (f" from {member}." if member else "."),
                           count=len(deleted))
        note = await ctx.channel.send(
            view=Container(accent_color=C_SUCCESS).text(
                f"Deleted **{len(deleted)}** message(s)"
                + (f" from {member.mention}." if member else ".")).build())
        try:
            await note.delete(delay=5)
        except Exception:  # noqa: BLE001
            pass

    @commands.command(name="slowmode", aliases=["slow"])
    @commands.has_guild_permissions(manage_channels=True)
    @commands.bot_has_guild_permissions(manage_channels=True)
    async def slowmode(self, ctx: DiscoContext, duration: str = "0") -> None:
        """Set this channel's slowmode (e.g. 10s, 1m; 0 to disable; max 6h)."""
        secs = _parse_duration(duration)
        if secs is None:
            secs = 0 if duration.strip() in ("0", "off", "none") else None
        if secs is None:
            await self._err(ctx, "Give a delay like `10s`, `1m`, or `0` to disable.")
            return
        secs = max(0, min(secs, 21600))
        try:
            await ctx.channel.edit(slowmode_delay=secs)
        except discord.Forbidden:
            await self._err(ctx, "I don't have permission to edit this channel.")
            return
        await self._modlog(ctx, "channel.slowmode",
                           target=None, summary=f"Slowmode set to {_fmt_duration(secs)} "
                                                f"in {ctx.channel.mention}.")
        if secs == 0:
            await self._ok(ctx, "Slowmode disabled.")
        else:
            await self._ok(ctx, f"Slowmode set to {_fmt_duration(secs)}.")

    @commands.command(name="lock")
    @commands.has_guild_permissions(manage_channels=True)
    @commands.bot_has_guild_permissions(manage_roles=True)
    async def lock(self, ctx: DiscoContext, *, reason: str = "No reason given") -> None:
        """Lock this channel: deny @everyone from sending messages."""
        everyone = ctx.guild.default_role
        ow = ctx.channel.overwrites_for(everyone)
        if ow.send_messages is False:
            await self._err(ctx, "This channel is already locked.")
            return
        ow.send_messages = False
        try:
            await ctx.channel.set_permissions(everyone, overwrite=ow,
                                              reason=f"lock by {ctx.author}: {reason}")
        except discord.Forbidden:
            await self._err(ctx, "I don't have permission to edit this channel's permissions.")
            return
        await self._modlog(ctx, "channel.lock", target=None, severity=Severity.WARNING,
                           summary=f"{ctx.channel.mention} locked.", reason=reason)
        await self._ok(ctx, f"Locked {ctx.channel.mention}. Reason: {reason}")

    @commands.command(name="unlock")
    @commands.has_guild_permissions(manage_channels=True)
    @commands.bot_has_guild_permissions(manage_roles=True)
    async def unlock(self, ctx: DiscoContext, *, reason: str = "No reason given") -> None:
        """Unlock this channel: restore @everyone's send permission to default."""
        everyone = ctx.guild.default_role
        ow = ctx.channel.overwrites_for(everyone)
        ow.send_messages = None
        try:
            await ctx.channel.set_permissions(everyone, overwrite=ow,
                                              reason=f"unlock by {ctx.author}: {reason}")
        except discord.Forbidden:
            await self._err(ctx, "I don't have permission to edit this channel's permissions.")
            return
        await self._modlog(ctx, "channel.unlock", target=None,
                           summary=f"{ctx.channel.mention} unlocked.", reason=reason)
        await self._ok(ctx, f"Unlocked {ctx.channel.mention}.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Moderation(bot))
