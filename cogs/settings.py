"""cogs/settings.py -- per-guild configuration, shown and edited in Components V2.

Every option here is also editable from the Sojourns web UI; the two surfaces
write the same per-guild keys (the DB layer normalises the manifest's env-style
keys onto these), so a value set in Discord and a value set on the web are the
same value. Keep this in step with ``clanklib.guild_schema.GUILD_FIELDS`` and the
``containment`` / ``moderation`` groups in ``sojourns.json``.
"""
from __future__ import annotations

import discord
from discord.ext import commands

from clanklib.permissions import ModCog
from core.framework.components import Container, send_v2
from core.framework.context import DiscoContext
from core.framework.ui import C_ERROR, C_INFO, C_SUCCESS
from clanklib.settings import prefix as _prefix


def _chan(guild: discord.Guild, cid) -> str:
    if not cid:
        return "_not set_"
    ch = guild.get_channel(int(cid))
    return ch.mention if ch else f"`{cid}` (missing)"


def _role(guild: discord.Guild, rid) -> str:
    if not rid:
        return "_not set_"
    r = guild.get_role(int(rid))
    return r.mention if r else f"`{rid}` (missing)"


class Settings(ModCog):
    @commands.command(name="settings", aliases=["config", "cfg"])
    @commands.has_guild_permissions(manage_guild=True)
    async def settings_cmd(self, ctx: DiscoContext) -> None:
        s = await self.db.get_guild_settings(ctx.guild.id)
        p = _prefix(self.bot)
        g = ctx.guild
        panel = (
            Container(accent_color=C_INFO)
            .text(f"## Settings for {g.name}")
            .text(
                f"**Prefix**  `{s.get('prefix') or p}`\n"
                f"**Log channel**  {_chan(g, s.get('log_channel'))}\n"
                f"**Mod log channel**  {_chan(g, s.get('mod_log_channel'))}"
            )
            .separator()
            .text(
                "### Containment\n"
                f"**Clanker role**  {_role(g, s.get('clanker_role'))}\n"
                f"**Clanker category**  {_chan(g, s.get('clank_category'))}\n"
                f"**Clanktank channel**  {_chan(g, s.get('clanktank_channel'))}\n"
                f"**Clanker log channel**  {_chan(g, s.get('clanktank_log_channel'))}\n"
                f"**Escape-room thread**  {_chan(g, s.get('clank_escape_thread'))}\n"
                f"**Reflection period**  `{s.get('clank_escape_wait_minutes') or 5} min`"
            )
            .separator()
            .text(
                "### Clanker hunters\n"
                f"**Hunter role**  {_role(g, s.get('scam_hunter_role'))}\n"
                f"**Hunter channel**  {_chan(g, s.get('scam_report_channel'))}"
            )
            .separator()
            .text(
                f"-# Edit with `{p}set <option> <value>` -- options: `prefix`, `log`, "
                f"`modlog`, `clankerrole`, `category`, `tank`, `clankerlog`, "
                f"`escapethread`, `reflection`, `hunterrole`, `hunterchannel`. "
                f"Use `none` to clear. Everything here is also editable in the web UI."
            )
        )
        await send_v2(ctx, panel)

    @commands.group(name="set", invoke_without_command=True)
    @commands.has_guild_permissions(manage_guild=True)
    async def set_grp(self, ctx: DiscoContext) -> None:
        await self.settings_cmd(ctx)

    # -- prefix ---------------------------------------------------------------

    @set_grp.command(name="prefix")
    async def set_prefix(self, ctx: DiscoContext, prefix: str) -> None:
        if len(prefix) > 5:
            await send_v2(ctx, Container(accent_color=C_ERROR).text(
                "Prefix must be 5 characters or fewer."))
            return
        await self.db.update_guild_setting(ctx.guild.id, "prefix", prefix)
        await send_v2(ctx, Container(accent_color=C_SUCCESS).text(
            f"Prefix set to `{prefix}`."))

    # -- channels -------------------------------------------------------------

    @set_grp.command(name="log", aliases=["logchannel"])
    async def set_log(self, ctx: DiscoContext, channel: str) -> None:
        await self._set_channel(ctx, "log_channel", channel, "Log channel")

    @set_grp.command(name="modlog", aliases=["modlogchannel"])
    async def set_modlog(self, ctx: DiscoContext, channel: str) -> None:
        await self._set_channel(ctx, "mod_log_channel", channel, "Mod log channel")

    @set_grp.command(name="category", aliases=["clankercategory"])
    async def set_category(self, ctx: DiscoContext, category: str) -> None:
        await self._set_channel(ctx, "clank_category", category, "Clanker category", category=True)

    @set_grp.command(name="tank", aliases=["containment", "clanktank", "clankerchannel"])
    async def set_containment(self, ctx: DiscoContext, channel: str) -> None:
        await self._set_channel(ctx, "clanktank_channel", channel, "Clanktank channel")

    @set_grp.command(name="clankerlog", aliases=["containmentlog", "clanktanklog", "tanklog"])
    async def set_containmentlog(self, ctx: DiscoContext, channel: str) -> None:
        await self._set_channel(ctx, "clanktank_log_channel", channel, "Clanker log channel")

    @set_grp.command(name="escapethread", aliases=["escape", "thread"])
    async def set_escapethread(self, ctx: DiscoContext, channel: str) -> None:
        await self._set_channel(ctx, "clank_escape_thread", channel, "Escape-room thread", thread=True)

    @set_grp.command(name="hunterchannel", aliases=["reportchannel"])
    async def set_hunterchannel(self, ctx: DiscoContext, channel: str) -> None:
        await self._set_channel(ctx, "scam_report_channel", channel, "Hunter channel")

    # -- roles ----------------------------------------------------------------

    @set_grp.command(name="clankerrole", aliases=["role"])
    async def set_clankerrole(self, ctx: DiscoContext, role: str) -> None:
        await self._set_role(ctx, "clanker_role", role, "Clanker role")

    @set_grp.command(name="hunterrole")
    async def set_hunterrole(self, ctx: DiscoContext, role: str) -> None:
        await self._set_role(ctx, "scam_hunter_role", role, "Clanker hunter role")

    # -- numbers --------------------------------------------------------------

    @set_grp.command(name="reflection", aliases=["reflectionperiod", "wait"])
    async def set_reflection(self, ctx: DiscoContext, minutes: str) -> None:
        if minutes.lower() in ("none", "off", "clear", "unset", "default"):
            await self.db.update_guild_setting(ctx.guild.id, "clank_escape_wait_minutes", None)
            await send_v2(ctx, Container(accent_color=C_SUCCESS).text(
                "Reflection period reset to the default (5 minutes)."))
            return
        try:
            n = int(minutes)
        except ValueError:
            await send_v2(ctx, Container(accent_color=C_ERROR).text(
                "Give a whole number of minutes (1-120)."))
            return
        if not (1 <= n <= 120):
            await send_v2(ctx, Container(accent_color=C_ERROR).text(
                "Reflection period must be between 1 and 120 minutes."))
            return
        await self.db.update_guild_setting(ctx.guild.id, "clank_escape_wait_minutes", n)
        await send_v2(ctx, Container(accent_color=C_SUCCESS).text(
            f"Reflection period set to {n} minute(s)."))

    # -- helpers --------------------------------------------------------------

    async def _set_channel(self, ctx: DiscoContext, key: str, value: str, label: str,
                           category: bool = False, thread: bool = False) -> None:
        if value.lower() in ("none", "off", "clear", "unset"):
            await self.db.update_guild_setting(ctx.guild.id, key, None)
            await send_v2(ctx, Container(accent_color=C_SUCCESS).text(f"{label} cleared."))
            return
        target = None
        try:
            if category:
                target = await commands.CategoryChannelConverter().convert(ctx, value)
            elif thread:
                target = await commands.ThreadConverter().convert(ctx, value)
            else:
                target = await commands.TextChannelConverter().convert(ctx, value)
        except commands.BadArgument:
            # Fall back to a raw id so an operator can paste an id for a channel
            # the bot cannot resolve yet (e.g. a thread it has not seen).
            digits = "".join(c for c in value if c.isdigit())
            if not digits:
                kind = "category" if category else ("thread" if thread else "channel")
                await send_v2(ctx, Container(accent_color=C_ERROR).text(
                    f"Couldn't find that {kind}. Mention it or paste its id."))
                return
            await self.db.update_guild_setting(ctx.guild.id, key, int(digits))
            await send_v2(ctx, Container(accent_color=C_SUCCESS).text(
                f"{label} set to `{digits}`."))
            return
        await self.db.update_guild_setting(ctx.guild.id, key, target.id)
        await send_v2(ctx, Container(accent_color=C_SUCCESS).text(
            f"{label} set to {target.mention}."))

    async def _set_role(self, ctx: DiscoContext, key: str, value: str, label: str) -> None:
        if value.lower() in ("none", "off", "clear", "unset"):
            await self.db.update_guild_setting(ctx.guild.id, key, None)
            await send_v2(ctx, Container(accent_color=C_SUCCESS).text(f"{label} cleared."))
            return
        try:
            role = await commands.RoleConverter().convert(ctx, value)
        except commands.BadArgument:
            digits = "".join(c for c in value if c.isdigit())
            if not digits:
                await send_v2(ctx, Container(accent_color=C_ERROR).text(
                    "Couldn't find that role. Mention it or paste its id."))
                return
            await self.db.update_guild_setting(ctx.guild.id, key, int(digits))
            await send_v2(ctx, Container(accent_color=C_SUCCESS).text(
                f"{label} set to `{digits}`."))
            return
        await self.db.update_guild_setting(ctx.guild.id, key, role.id)
        await send_v2(ctx, Container(accent_color=C_SUCCESS).text(
            f"{label} set to {role.mention}."))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Settings(bot))
