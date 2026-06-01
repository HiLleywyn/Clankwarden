"""cogs/setupwiz.py -- .init, the guided one-command server setup.

Creates the roles, category, channels and escape-room thread the bot needs and
wires every per-guild setting, with three safety rails:

1. a pre-run configuration panel (pick which pieces to provision),
2. an explicit Confirm before anything is created, and
3. a post-run review with **Keep** or **Revert** -- Revert deletes exactly what
   this run created (never anything pre-existing) and clears the settings it
   wrote, returning you to the start.

All creation is tracked so the revert is exact and safe. Nothing here needs
Administrator; it uses Manage Roles + Manage Channels.
"""
from __future__ import annotations

import discord
from discord.ext import commands

from clanklib.modlog import Category as _ModCategory
from clanklib.permissions import ModCog
from core.framework.context import DiscoContext
from core.framework.ui import C_ERROR, C_INFO, C_NAVY, C_SUCCESS

# Each piece the wizard can provision: key -> (label, description).
PIECES: dict[str, tuple[str, str]] = {
    "containment": ("Clanktank",
                    "A Clanktank category holding the tank, its escape-room thread and clank logs; "
                    "plus the Clanker role, locked out of every other channel."),
    "modlogs": ("Mod logs",
                "A Mod Logs category with one mod/admin-only channel per log category "
                "(security, moderation, member, message, ...), each auto-routed."),
}
DEFAULT_PIECES = tuple(PIECES.keys())


class _Created:
    """Tracks what one run made (for an exact revert) and what it reused."""

    def __init__(self) -> None:
        self.roles: list[int] = []
        self.channels: list[int] = []      # includes the category + threads
        self.applied_keys: list[str] = []  # guild settings this run wrote
        self.locked: list[int] = []        # existing channels we denied Clanker on
        self.role_was_created: bool = False


class InitView(discord.ui.LayoutView):
    """The whole wizard: config -> confirm -> running -> review, in one view."""

    def __init__(self, cog: "SetupWizard", ctx: DiscoContext) -> None:
        super().__init__(timeout=600)
        self.cog = cog
        self.ctx = ctx
        self.bot = cog.bot
        self.guild = ctx.guild
        self.author_id = ctx.author.id
        self.phase = "config"
        self.selected: set[str] = set(DEFAULT_PIECES)
        self.created = _Created()
        self.summary_lines: list[str] = []
        self.error: str = ""
        self.running_detail: str = "Creating roles and channels."
        self._interaction: discord.Interaction | None = None
        self._rebuild()

    # -- guards ---------------------------------------------------------------

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This setup isn't yours.", ephemeral=True)
            return False
        return True

    async def update_running(self, detail: str) -> None:
        """Live-update the 'running' panel from inside provisioning (best-effort)."""
        self.running_detail = detail
        if self.phase != "running" or self._interaction is None:
            return
        self._rebuild()
        try:
            await self._interaction.edit_original_response(view=self)
        except Exception:  # noqa: BLE001
            pass

    # -- rendering ------------------------------------------------------------

    def _rebuild(self) -> None:
        self.clear_items()
        if self.phase == "config":
            self._render_config()
        elif self.phase == "running":
            self._render_running()
        elif self.phase == "review":
            self._render_review()
        elif self.phase == "done":
            self._render_done()

    def _render_config(self) -> None:
        chosen = "\n".join(
            f"- **{PIECES[k][0]}** -- {PIECES[k][1]}"
            for k in PIECES if k in self.selected
        ) or "_nothing selected_"
        c = discord.ui.Container(
            discord.ui.TextDisplay(
                "## Guided setup\n"
                "-# Pick what to create, then Confirm. Nothing is created until you do."
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            discord.ui.TextDisplay(f"### Will provision\n{chosen}"),
            accent_color=C_NAVY,
        )
        self.add_item(c)
        options = [
            discord.SelectOption(label=PIECES[k][0], value=k,
                                 description=PIECES[k][1][:100],
                                 default=(k in self.selected))
            for k in PIECES
        ]
        sel = discord.ui.Select(placeholder="Choose what to set up...",
                                min_values=0, max_values=len(options), options=options)
        sel.callback = self._on_select
        self.add_item(discord.ui.ActionRow(sel))
        confirm = discord.ui.Button(label="Confirm and create",
                                    style=discord.ButtonStyle.success, emoji="✅")
        confirm.callback = self._on_confirm
        cancel = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)
        cancel.callback = self._on_cancel
        self.add_item(discord.ui.ActionRow(confirm, cancel))

    def _render_running(self) -> None:
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(f"## Setting things up...\n-# {self.running_detail}"),
            accent_color=C_INFO,
        ))

    def _render_review(self) -> None:
        body = "\n".join(self.summary_lines) or "_nothing was created_"
        c = discord.ui.Container(
            discord.ui.TextDisplay("## Setup complete -- review\n"
                                   "-# Check the new roles/channels. Keep them, or Revert to "
                                   "delete exactly what this run created and start over."),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            discord.ui.TextDisplay(body),
            accent_color=C_SUCCESS,
        )
        self.add_item(c)
        keep = discord.ui.Button(label="Keep it", style=discord.ButtonStyle.success, emoji="\U0001F44D")
        keep.callback = self._on_keep
        revert = discord.ui.Button(label="Revert and restart",
                                   style=discord.ButtonStyle.danger, emoji="♻")
        revert.callback = self._on_revert
        self.add_item(discord.ui.ActionRow(keep, revert))

    def _render_done(self) -> None:
        msg = self.error or "Setup kept. You're ready to go -- run `.help` to explore."
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(("## " + ("Cancelled" if self.error else "All set")) + f"\n{msg}"),
            accent_color=C_ERROR if self.error else C_SUCCESS,
        ))

    # -- interactions ---------------------------------------------------------

    async def _on_select(self, interaction: discord.Interaction) -> None:
        self.selected = set(interaction.data.get("values", []))  # type: ignore[union-attr]
        self._rebuild()
        await interaction.response.edit_message(view=self)

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        self.phase = "done"
        self.error = "No changes were made."
        self._rebuild()
        await interaction.response.edit_message(view=self)
        self.stop()

    async def _on_confirm(self, interaction: discord.Interaction) -> None:
        if not self.selected:
            await interaction.response.send_message("Select at least one piece first.", ephemeral=True)
            return
        self.phase = "running"
        self.running_detail = "Creating roles and channels."
        self._interaction = interaction
        self._rebuild()
        await interaction.response.edit_message(view=self)
        try:
            await self.cog.provision(self)
            self.phase = "review"
        except Exception as exc:  # noqa: BLE001
            # On any failure mid-provision, roll back what we made so far.
            await self.cog.revert(self)
            self.phase = "done"
            self.error = f"Setup failed and was rolled back: {type(exc).__name__}: {exc}"
        self._rebuild()
        await interaction.edit_original_response(view=self)

    async def _on_keep(self, interaction: discord.Interaction) -> None:
        self.phase = "done"
        self._rebuild()
        await interaction.response.edit_message(view=self)
        modlog = getattr(self.bot, "modlog", None)
        if modlog is not None:
            try:
                await modlog.config("config.init", self.guild.id, actor=interaction.user,
                                    summary="Guided setup (.init) completed and kept.")
            except Exception:  # noqa: BLE001
                pass
        self.stop()

    async def _on_revert(self, interaction: discord.Interaction) -> None:
        self.phase = "running"
        self.running_detail = "Reverting -- deleting what this run created."
        self._interaction = interaction
        self._rebuild()
        await interaction.response.edit_message(view=self)
        await self.cog.revert(self)
        # Back to the start with a fresh tracker.
        self.created = _Created()
        self.summary_lines = []
        self.phase = "config"
        self._rebuild()
        await interaction.edit_original_response(view=self)


class SetupWizard(ModCog):
    @commands.command(name="init", aliases=["setupwizard"])
    @commands.has_guild_permissions(manage_guild=True)
    @commands.bot_has_guild_permissions(manage_roles=True, manage_channels=True)
    async def init_cmd(self, ctx: DiscoContext) -> None:
        """Guided one-command setup: creates and wires the bot's roles/channels."""
        # InitView is a raw LayoutView (interactive), so send it directly rather
        # than through send_v2 (which only passes ComponentsV2View through).
        await ctx.send(view=InitView(self, ctx))

    # -- provisioning ---------------------------------------------------------

    async def _set(self, view: InitView, key: str, value) -> None:
        await self.db.update_guild_setting(view.guild.id, key, value)
        view.created.applied_keys.append(key)

    async def provision(self, view: InitView) -> None:
        guild = view.guild
        me = guild.me
        staff_view = discord.PermissionOverwrite(view_channel=True)
        hide = discord.PermissionOverwrite(view_channel=False)
        reason = f"Clanksimus .init by {view.ctx.author}"
        sel = view.selected

        category = None
        clanker_role = None

        if "containment" in sel:
            # Clanker role: a plain containment marker, no privileges.
            clanker_role = discord.utils.get(guild.roles, name="Clanker")
            if clanker_role is None:
                clanker_role = await guild.create_role(
                    name="Clanker", permissions=discord.Permissions.none(),
                    colour=discord.Colour(0x9b59b6), hoist=False, mentionable=False,
                    reason=reason)
                view.created.roles.append(clanker_role.id)
                view.created.role_was_created = True
                view.summary_lines.append(f"Created role {clanker_role.mention}")
            else:
                view.summary_lines.append(f"Reused existing role {clanker_role.mention}")
            await self._set(view, "clanker_role", clanker_role.id)

            # Clanktank category: clankers can't see it; the tank re-grants view.
            cat_overwrites = {clanker_role: hide}
            if me is not None:
                cat_overwrites[me] = staff_view
            category = await guild.create_category(
                "Clanktank", overwrites=cat_overwrites, reason=reason)
            view.created.channels.append(category.id)
            view.summary_lines.append(f"Created category **{category.name}**")
            await self._set(view, "clank_category", category.id)

            tank = await guild.create_text_channel(
                "clanktank", category=category, reason=reason,
                overwrites={
                    clanker_role: discord.PermissionOverwrite(
                        view_channel=True, send_messages=True, read_message_history=True),
                    **({me: staff_view} if me else {}),
                })
            view.created.channels.append(tank.id)
            view.summary_lines.append(f"Created {tank.mention} (the tank)")
            await self._set(view, "clanktank_channel", tank.id)

            clog = await guild.create_text_channel(
                "clank-logs", category=category, reason=reason,
                overwrites={guild.default_role: hide, clanker_role: hide,
                            **({me: staff_view} if me else {})})
            view.created.channels.append(clog.id)
            view.summary_lines.append(f"Created {clog.mention} (clank logs)")
            await self._set(view, "clanktank_log_channel", clog.id)

            # Escape-room thread lives off the tank channel.
            try:
                thread = await tank.create_thread(
                    name="escape-room", type=discord.ChannelType.public_thread,
                    reason=reason)
                view.created.channels.append(thread.id)
                view.summary_lines.append(f"Created escape-room thread {thread.mention}")
                await self._set(view, "clank_escape_thread", thread.id)
            except Exception:  # noqa: BLE001
                view.summary_lines.append("Could not create the escape thread (set it later with .set escapethread)")

            # Make the Clanker role an actual jail role: deny it View Channel on
            # every existing channel so a clanked user (stripped to @everyone +
            # Clanker) can only see the tank. Paced so a big server can't 429 us.
            await self._lock_clanker_out(view, clanker_role, keep_category=category)

        if "modlogs" in sel:
            # Mod Logs category: mod/admin only. Deny @everyone View on the
            # category; child channels are created synced, so they inherit it.
            mod_ov = {guild.default_role: hide}
            if me is not None:
                mod_ov[me] = staff_view
            mod_cat = await guild.create_category(
                "Mod Logs", overwrites=mod_ov, reason=reason)
            view.created.channels.append(mod_cat.id)
            view.summary_lines.append(f"Created category **{mod_cat.name}** (mod/admin only)")

            # One channel per mod-log category, each auto-routed.
            await view.update_running("Creating the per-category mod-log channels...")
            routes: dict[str, int] = {}
            for cat in _ModCategory:
                ch = await guild.create_text_channel(
                    f"log-{cat.value}", category=mod_cat, reason=reason)
                view.created.channels.append(ch.id)
                routes[cat.value] = ch.id
            await self._set(view, "modlog_routes", routes)
            # Default destination for anything not explicitly routed.
            await self._set(view, "mod_log_channel", routes.get("moderation"))
            view.summary_lines.append(
                f"Created {len(routes)} routed mod-log channels under **Mod Logs**")

    async def _lock_clanker_out(self, view: InitView, clanker_role: discord.Role,
                                *, keep_category: discord.CategoryChannel | None) -> None:
        """Deny the Clanker role View Channel on every existing channel except
        the containment category we just made, paced to avoid rate limits."""
        from clanklib.ratelimit import BulkRunner

        keep_ids = set()
        if keep_category is not None:
            keep_ids.add(keep_category.id)
            keep_ids.update(c.id for c in keep_category.channels)

        deny = discord.PermissionOverwrite(view_channel=False)
        reason = f"Clanksimus .init: lock Clanker out (by {view.ctx.author})"

        # Only touch categories and channels that aren't already synced to a
        # category we'll handle -- minimizes API calls on big servers.
        targets: list[discord.abc.GuildChannel] = []
        for ch in view.guild.channels:
            if ch.id in keep_ids:
                continue
            if isinstance(ch, discord.CategoryChannel):
                targets.append(ch)
            elif getattr(ch, "permissions_synced", False):
                continue  # inherits its category's overwrite
            else:
                targets.append(ch)

        async def _lock_one(ch: discord.abc.GuildChannel) -> None:
            await ch.set_permissions(clanker_role, overwrite=deny, reason=reason)
            view.created.locked.append(ch.id)

        total = len(targets)
        await view.update_running(
            f"Locking the Clanker role out of {total} channel(s) (paced ~0.7s each, "
            f"~{max(1, round(total * 0.7 / 60))} min). 0/{total}.")

        async def _prog(res) -> None:
            await view.update_running(
                f"Locking the Clanker role out of channels... "
                f"{res.processed}/{res.total} done.")

        result = await BulkRunner(base_delay=0.7).run(
            targets, _lock_one, progress=_prog, progress_every=15)
        view.summary_lines.append(
            f"Locked the Clanker role out of {result.succeeded} existing channel(s)"
            + (f" (stopped early: {result.abort_reason})" if result.aborted else "")
        )

    async def _lock_clanker_out(self, view: InitView, clanker_role: discord.Role,
                                *, keep_category: discord.CategoryChannel | None) -> None:
        """Deny the Clanker role View Channel on every existing channel except
        the containment category we just made, paced to avoid rate limits."""
        from clanklib.ratelimit import BulkRunner

        keep_ids = set()
        if keep_category is not None:
            keep_ids.add(keep_category.id)
            keep_ids.update(c.id for c in keep_category.channels)

        deny = discord.PermissionOverwrite(view_channel=False)
        reason = f"Clanksimus .init: lock Clanker out (by {view.ctx.author})"

        # Only touch categories and channels that aren't already synced to a
        # category we'll handle -- minimizes API calls on big servers.
        targets: list[discord.abc.GuildChannel] = []
        for ch in view.guild.channels:
            if ch.id in keep_ids:
                continue
            if isinstance(ch, discord.CategoryChannel):
                targets.append(ch)
            elif getattr(ch, "permissions_synced", False):
                continue  # inherits its category's overwrite
            else:
                targets.append(ch)

        async def _lock_one(ch: discord.abc.GuildChannel) -> None:
            await ch.set_permissions(clanker_role, overwrite=deny, reason=reason)
            view.created.locked.append(ch.id)

        total = len(targets)
        await view.update_running(
            f"Locking the Clanker role out of {total} channel(s) (paced ~0.7s each, "
            f"~{max(1, round(total * 0.7 / 60))} min). 0/{total}.")

        async def _prog(res) -> None:
            await view.update_running(
                f"Locking the Clanker role out of channels... "
                f"{res.processed}/{res.total} done.")

        result = await BulkRunner(base_delay=0.7).run(
            targets, _lock_one, progress=_prog, progress_every=15)
        view.summary_lines.append(
            f"Locked the Clanker role out of {result.succeeded} existing channel(s)"
            + (f" (stopped early: {result.abort_reason})" if result.aborted else "")
        )

    async def revert(self, view: InitView) -> None:
        """Delete exactly what this run created and clear the settings it wrote."""
        guild = view.guild
        reason = "Clanksimus .init revert"
        # Undo the Clanker lockdown on existing channels. If this run created the
        # Clanker role, deleting it below removes its overwrites for free; only a
        # reused role needs its overwrites stripped explicitly.
        if view.created.locked and not view.created.role_was_created:
            role = discord.utils.get(guild.roles, name="Clanker")
            if role is not None:
                for cid in list(view.created.locked):
                    ch = guild.get_channel(cid)
                    if ch is not None:
                        try:
                            await ch.set_permissions(role, overwrite=None, reason=reason)
                        except Exception:  # noqa: BLE001
                            pass
        view.created.locked.clear()
        # Channels/threads/category first (reverse order), then roles.
        for cid in reversed(view.created.channels):
            ch = guild.get_channel(cid) or guild.get_thread(cid)
            if ch is None:
                try:
                    ch = await self.bot.fetch_channel(cid)
                except Exception:  # noqa: BLE001
                    ch = None
            if ch is not None:
                try:
                    await ch.delete(reason=reason)
                except Exception:  # noqa: BLE001
                    pass
        for rid in reversed(view.created.roles):
            role = guild.get_role(rid)
            if role is not None:
                try:
                    await role.delete(reason=reason)
                except Exception:  # noqa: BLE001
                    pass
        for key in view.created.applied_keys:
            try:
                await self.db.update_guild_setting(guild.id, key, None)
            except Exception:  # noqa: BLE001
                pass
        view.created.channels.clear()
        view.created.roles.clear()
        view.created.applied_keys.clear()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SetupWizard(bot))
