"""cogs/meta.py -- help, about and health, rendered with Components V2."""
from __future__ import annotations

import time

import discord
from discord.ext import commands

from core.config import Config
from core.framework.cogs import BaseCog
from core.framework.components import Container, send_v2
from core.framework.context import DiscoContext
from core.framework.ui import C_INFO, C_NEUTRAL, C_SUCCESS
from clanklib.settings import prefix as _prefix, setting

_START = time.time()


class Meta(BaseCog):
    @commands.command(name="help", aliases=["commands", "h"])
    async def help_cmd(self, ctx: DiscoContext, *, topic: str = "") -> None:
        # The modern, dynamic help hub: a single Components V2 surface with a
        # multi-select of feature sections whose commands are generated live
        # from the command tree and combine seamlessly. Presented as one bot.
        from cogs._help_view import send_help
        await send_help(ctx)

    @commands.command(name="about", aliases=["info"])
    async def about_cmd(self, ctx: DiscoContext) -> None:
        guilds = len(self.bot.guilds)
        users = sum(g.member_count or 0 for g in self.bot.guilds)
        panel = (
            Container(accent_color=C_INFO)
            .text("## About Clanksimus Prime")
            .text("A free, open server-management bot built on the shared bot "
                  "framework and Discord's modern Components V2 UI. No premium "
                  "tiers, no paywalls.")
            .separator()
            .section(f"**Servers**  {guilds:,}\n**Members**  {users:,}",
                     accessory=Container.accessory_button(
                         "Add to server", url=self._invite_url()))
        )
        await send_v2(ctx, panel)

    @commands.command(name="ping", aliases=["latency"])
    async def ping_cmd(self, ctx: DiscoContext) -> None:
        latency = round(self.bot.latency * 1000)
        uptime = int(time.time() - _START)
        h, rem = divmod(uptime, 3600)
        m, s = divmod(rem, 60)
        panel = (
            Container(accent_color=C_SUCCESS if latency < 200 else C_NEUTRAL)
            .text("## Pong")
            .text(f"**Gateway**  {latency} ms\n**Uptime**  {h}h {m}m {s}s")
        )
        await send_v2(ctx, panel)

    @commands.command(name="invite")
    async def invite_cmd(self, ctx: DiscoContext) -> None:
        panel = (
            Container(accent_color=C_INFO)
            .text("## Invite Clanksimus Prime")
            .section("Add the bot to your server with the permissions it needs.",
                     accessory=Container.accessory_button("Invite", url=self._invite_url()))
        )
        await send_v2(ctx, panel)

    def _invite_url(self) -> str:
        cid = getattr(self.bot.user, "id", None) or setting(self.bot, "DISCORD_CLIENT_ID", "")
        # Administrator (8) covers create/delete of roles, channels and webhooks.
        return (
            f"https://discord.com/oauth2/authorize?client_id={cid}"
            "&permissions=8&scope=bot%20applications.commands"
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Meta(bot))
