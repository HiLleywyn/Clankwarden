"""cogs/dehoist.py -- Clankwarden's smart, server-aware dehoist system.

Most "dehoist" bots strip a leading "!" and call it a day. This one *learns the
server* -- its roles, staff, channel names and topics -- and synthesizes the
impersonation handles that community actually gets hit by (an investing server
breeds "Binance Support" / "Crypto Team"; a gaming server breeds "Steam
Support"). It then watches natural events (join / message / nick change), and for
anyone **below a configurable floor role** it dehoists the name and -- by default
-- auto-clanks the impersonator straight into the tank. Everything is paced
through :class:`clanklib.ratelimit.BulkRunner` so a big sweep never earns a
Cloudflare ban, every action is logged, and the whole surface is Components V2
with actionable alert/config/analytics panels.

Pure detection lives in :mod:`clanklib.dehoist` (unit-tested, dependency-free);
this cog is the Discord glue: settings, the hot-path listeners, the mod commands,
the act-fast slash commands, and the panels. Auto-clank is delegated to the
Clanktank cog's ``warden_contain`` so the containment path stays single-sourced.
"""
from __future__ import annotations

import logging
import time
from typing import Iterable

import discord
from discord import app_commands
from discord.ext import commands

from clanklib import dehoist as dh
from clanklib.permissions import ModCog
from clanklib.ratelimit import BulkRunner
from core.framework.components import Container, safe_defer, safe_edit, send_v2
from core.framework.context import DiscoContext
from core.framework.ui import C_ERROR, C_INFO, C_SUCCESS, C_WARNING, C_NAVY

log = logging.getLogger(__name__)

_MODES = ("off", "warn", "rename", "rename_clank")
_DEFAULT_MODE = "rename_clank"
_ALL_TRIGGERS = ("join", "message", "update")
_SIGNAL_TTL = 3600.0          # recompute a guild's signals at most hourly
_MSG_DEBOUNCE = 45.0          # min seconds between message-triggered checks per user


# --------------------------------------------------------------------------- #
# Settings helpers (read the same per-guild keys the web UI / .set write)
# --------------------------------------------------------------------------- #

def _csv(value: object, default: Iterable[str] = ()) -> list[str]:
    if not value:
        return list(default)
    if isinstance(value, (list, tuple)):
        return [str(v).strip().lower() for v in value if str(v).strip()]
    return [p.strip().lower() for p in str(value).split(",") if p.strip()]


def _mode_of(s: dict) -> str:
    m = str(s.get("dehoist_mode") or _DEFAULT_MODE).strip().lower()
    return m if m in _MODES else _DEFAULT_MODE


class SmartDehoist(ModCog):
    """Smart dehoist + impersonation containment. Gated to managers via ModCog."""

    def __init__(self, bot) -> None:
        super().__init__(bot)
        self.bot = bot
        # guild_id -> (computed_at, GuildSignals)
        self._sig_cache: dict[int, tuple[float, dh.GuildSignals]] = {}
        # user_id -> last message-triggered check (debounce, dodge rate limits)
        self._debounce: dict[int, float] = {}

    # -- shared accessors -----------------------------------------------------

    @property
    def db(self):
        return self.bot.db

    async def _settings(self, gid: int) -> dict:
        return await self.bot.db.get_guild_settings(gid)

    async def _signals(self, guild: discord.Guild, s: dict | None = None) -> dh.GuildSignals:
        """Per-guild impersonation signals, cached in-process for `_SIGNAL_TTL`
        and persisted to `dehoist_signals` for the web surface."""
        now = time.time()
        cached = self._sig_cache.get(guild.id)
        if cached and now - cached[0] < _SIGNAL_TTL:
            return cached[1]
        s = s if s is not None else await self._settings(guild.id)
        extra = _csv(s.get("dehoist_topics"))
        sig = dh.scan_guild_signals(guild, extra_topics=extra)
        self._sig_cache[guild.id] = (now, sig)
        try:
            await self.bot.db.execute(
                """INSERT INTO dehoist_signals
                       (guild_id, topics, brand_terms, staff_terms, lexicon, refreshed_at)
                   VALUES ($1,$2,$3,$4,$5, now())
                   ON CONFLICT (guild_id) DO UPDATE
                   SET topics=$2, brand_terms=$3, staff_terms=$4, lexicon=$5,
                       refreshed_at=now()""",
                guild.id, sorted(sig.topics), sorted(sig.brand_terms),
                sorted(sig.staff_terms), sorted(sig.lexicon),
            )
        except Exception:
            log.debug("dehoist: persist signals failed gid=%s", guild.id)
        return sig

    def _floor_role(self, guild: discord.Guild, s: dict) -> discord.Role | None:
        rid = s.get("dehoist_floor_role")
        return guild.get_role(int(rid)) if rid else None

    def _whitelist(self, s: dict) -> set[int]:
        return {int(x) for x in (s.get("dehoist_whitelist") or []) if str(x).isdigit()}

    def _eligible(self, member: discord.Member, floor: discord.Role | None) -> bool:
        """Only act on non-staff members at/below the floor role. This is the
        rail that keeps legit 'degens messing around' from getting dehoisted."""
        if member.bot:
            return False
        p = member.guild_permissions
        if p.administrator or p.manage_guild or p.manage_nicknames or p.manage_roles:
            return False
        if floor is not None and member.top_role.position > floor.position:
            return False
        return True

    async def _is_clanked(self, member: discord.Member) -> bool:
        clank = self.bot.get_cog("Clanktank")
        if clank is None:
            return False
        try:
            return await clank.is_clanker(member.id, member.guild.id)
        except Exception:
            return False

    # -- hot path -------------------------------------------------------------

    async def _handle(self, member: discord.Member, *, trigger: str) -> None:
        """The shared on-event check. Cheap-rejects first, then classify + act."""
        try:
            guild = member.guild
            s = await self._settings(guild.id)
            if not s.get("dehoist_enabled"):
                return
            mode = _mode_of(s)
            if mode == "off" or trigger not in set(_csv(s.get("dehoist_triggers"), _ALL_TRIGGERS)):
                return
            if member.id in self._whitelist(s):
                return
            if not self._eligible(member, self._floor_role(guild, s)):
                return
            if await self._is_clanked(member):
                return
            sig = await self._signals(guild, s)
            det = dh.classify({member.name, member.display_name}, sig,
                              display_name=member.display_name)
            if det is None:
                return
            await self._act(member, det, mode, trigger, s)
        except Exception:
            log.exception("dehoist: _handle failed uid=%s trigger=%s", member.id, trigger)

    async def _act(self, member: discord.Member, det: dh.Detection, mode: str,
                   trigger: str, s: dict) -> None:
        guild = member.guild
        old = member.display_name
        renamed = clanked = False
        note = ""

        if mode in ("rename", "rename_clank") and det.clean_nick and det.clean_nick != old:
            try:
                await member.edit(nick=det.clean_nick[:32],
                                  reason=f"Clankwarden dehoist [{det.kind}]: {det.matched}")
                renamed = True
            except discord.Forbidden:
                note = "rename blocked (bot role must sit above the target)"
            except discord.HTTPException as exc:
                note = f"rename failed: {exc}"

        if mode == "rename_clank":
            clank = self.bot.get_cog("Clanktank")
            if clank is not None and hasattr(clank, "warden_contain"):
                try:
                    await clank.warden_contain(
                        member, reason=f"Auto-clank: dehoist {det.kind} ({det.matched})")
                    clanked = True
                except Exception:
                    log.exception("dehoist: warden_contain failed uid=%s", member.id)
            else:
                note = (note + "; " if note else "") + "clank cog unavailable"

        await self._record(guild.id, member, old, det,
                            "rename_clank" if clanked else ("rename" if renamed else mode),
                            trigger)
        await self._emit(member, det, old, renamed, clanked, trigger, note, s)

    async def _record(self, gid: int, member: discord.Member, old: str,
                      det: dh.Detection, action: str, trigger: str) -> None:
        try:
            await self.bot.db.execute(
                """INSERT INTO dehoist_events
                       (guild_id,user_id,old_name,new_name,kind,matched,action,trigger,confidence)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)""",
                gid, member.id, old, det.clean_nick, det.kind, det.matched,
                action, trigger, float(det.confidence),
            )
        except Exception:
            log.debug("dehoist: record event failed gid=%s uid=%s", gid, member.id)

    async def _emit(self, member: discord.Member, det: dh.Detection, old: str,
                    renamed: bool, clanked: bool, trigger: str, note: str, s: dict) -> None:
        # Structured modlog entry (hash-chained, never raises).
        try:
            await self.bot.modlog.security(
                "member.dehoist", member.guild.id, target=member,
                summary=f"Dehoist [{det.kind}] {member} -- matched {det.matched!r}.",
                metadata={"old": old, "new": det.clean_nick, "kind": det.kind,
                          "matched": det.matched, "trigger": trigger,
                          "renamed": renamed, "clanked": clanked,
                          "confidence": round(det.confidence, 2)},
            )
        except Exception:
            log.debug("dehoist: modlog emit failed")
        # Actionable alert panel to the dehoist log (fallback: clank log).
        cid = s.get("dehoist_log_channel") or s.get("clanktank_log_channel")
        if not cid:
            return
        ch = member.guild.get_channel(int(cid))
        if ch is None:
            return
        try:
            await ch.send(view=_DehoistAlert(self, member, det, old, renamed, clanked, trigger))
        except Exception:
            log.debug("dehoist: alert send failed")

    # -- listeners ------------------------------------------------------------

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        await self._handle(member, trigger="join")

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if before.nick == after.nick and before.display_name == after.display_name:
            return
        await self._handle(after, trigger="update")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot or message.webhook_id:
            return
        uid = message.author.id
        now = time.time()
        if now - self._debounce.get(uid, 0.0) < _MSG_DEBOUNCE:
            return
        self._debounce[uid] = now
        if isinstance(message.author, discord.Member):
            await self._handle(message.author, trigger="message")

    # -- commands: .dehoist group (mod-gated by ModCog) -----------------------

    @commands.group(name="dehoist", aliases=["dh", "warden"], invoke_without_command=True)
    @commands.guild_only()
    async def dehoist_grp(self, ctx: DiscoContext) -> None:
        """Smart dehoist: show and configure the impersonation guard."""
        s = await self._settings(ctx.guild.id)
        await ctx.reply(view=_DehoistConfig(self, ctx.author.id, ctx.guild, s),
                        mention_author=False)

    @dehoist_grp.command(name="on")
    async def dehoist_on(self, ctx: DiscoContext) -> None:
        """Enable smart dehoist."""
        await self.db.update_guild_setting(ctx.guild.id, "dehoist_enabled", True)
        await send_v2(ctx, Container(accent_color=C_SUCCESS).text("Smart dehoist **enabled**."))

    @dehoist_grp.command(name="off")
    async def dehoist_off(self, ctx: DiscoContext) -> None:
        """Disable smart dehoist."""
        await self.db.update_guild_setting(ctx.guild.id, "dehoist_enabled", False)
        await send_v2(ctx, Container(accent_color=C_INFO).text("Smart dehoist **disabled**."))

    @dehoist_grp.command(name="mode")
    async def dehoist_mode(self, ctx: DiscoContext, mode: str) -> None:
        """Set action mode: off | warn | rename | rename_clank."""
        mode = mode.strip().lower()
        if mode not in _MODES:
            await ctx.reply_error(f"Mode must be one of: {', '.join(_MODES)}.")
            return
        await self.db.update_guild_setting(ctx.guild.id, "dehoist_mode", mode)
        await send_v2(ctx, Container(accent_color=C_SUCCESS).text(f"Dehoist mode set to **{mode}**."))

    @dehoist_grp.command(name="floor")
    async def dehoist_floor(self, ctx: DiscoContext, role: discord.Role) -> None:
        """Set the floor role: only members at/below it are eligible."""
        await self.db.update_guild_setting(ctx.guild.id, "dehoist_floor_role", role.id)
        await send_v2(ctx, Container(accent_color=C_SUCCESS).text(
            f"Floor role set to {role.mention}. Members above it are never touched."))

    @dehoist_grp.command(name="channel", aliases=["log"])
    async def dehoist_channel(self, ctx: DiscoContext,
                              channel: discord.TextChannel | None = None) -> None:
        """Set (or show) the dehoist alert channel."""
        if channel is None:
            s = await self._settings(ctx.guild.id)
            cid = s.get("dehoist_log_channel")
            cur = f"<#{cid}>" if cid else "_unset_ (falls back to the Clanker log)"
            await send_v2(ctx, Container(accent_color=C_INFO).text(f"Dehoist log channel: {cur}"))
            return
        await self.db.update_guild_setting(ctx.guild.id, "dehoist_log_channel", channel.id)
        await send_v2(ctx, Container(accent_color=C_SUCCESS).text(
            f"Dehoist alerts will post to {channel.mention}."))

    @dehoist_grp.command(name="signals", aliases=["intel"])
    async def dehoist_signals(self, ctx: DiscoContext) -> None:
        """Show the impersonation signals learned from THIS server."""
        sig = await self._signals(ctx.guild)
        topics = ", ".join(sorted(sig.topics)) or "_none detected_"
        brands = ", ".join(sorted(sig.brand_terms)[:20]) or "_none_"
        lex = sorted(sig.lexicon)
        sample = ", ".join(lex[:24]) + (f" ... (+{len(lex) - 24})" if len(lex) > 24 else "")
        panel = (Container(accent_color=C_NAVY)
                 .text("## Dehoist intel for this server")
                 .separator()
                 .text(f"**Topics**  {topics}")
                 .text(f"**Brand terms**  {brands}")
                 .text(f"**Staff terms**  {', '.join(sorted(sig.staff_terms)[:15]) or '_none_'}")
                 .separator()
                 .text(f"**Impersonation lexicon ({len(lex)})**\n{sample}"))
        await send_v2(ctx, panel)

    @dehoist_grp.command(name="test")
    async def dehoist_test(self, ctx: DiscoContext, *, name: str) -> None:
        """Test a literal name against this server's signals (no action)."""
        sig = await self._signals(ctx.guild)
        det = dh.classify({name}, sig, display_name=name)
        if det is None:
            await send_v2(ctx, Container(accent_color=C_SUCCESS).text(
                f"`{name}` looks clean -- no hoist or impersonation match."))
            return
        await send_v2(ctx, Container(accent_color=C_WARNING)
                      .text(f"## Match: {det.kind}")
                      .separator()
                      .text(f"**Matched**  {det.matched}\n"
                            f"**Confidence**  {det.confidence:.0%}\n"
                            f"**Would rename to**  `{det.clean_nick}`\n"
                            f"**Reasons**  {', '.join(det.reasons)}"))

    @dehoist_grp.command(name="scan")
    async def dehoist_scan(self, ctx: DiscoContext, limit: int = 25) -> None:
        """Preview current members that match (read-only, no action)."""
        s = await self._settings(ctx.guild.id)
        floor = self._floor_role(ctx.guild, s)
        sig = await self._signals(ctx.guild, s)
        hits: list[tuple[discord.Member, dh.Detection]] = []
        for m in ctx.guild.members:
            if not self._eligible(m, floor):
                continue
            det = dh.classify({m.name, m.display_name}, sig, display_name=m.display_name)
            if det is not None:
                hits.append((m, det))
        if not hits:
            await send_v2(ctx, Container(accent_color=C_SUCCESS).text(
                "No matching members. The yard is clean."))
            return
        lines = "\n".join(
            f"- {m.mention} `{m.display_name}` -> **{d.kind}** ({d.matched})"
            for m, d in hits[:max(1, min(limit, 50))])
        more = f"\n-# ...and {len(hits) - limit} more" if len(hits) > limit else ""
        await send_v2(ctx, Container(accent_color=C_WARNING)
                      .text(f"## Dehoist scan -- {len(hits)} match(es)")
                      .separator().text(lines + more)
                      .separator().text(f"-# Run `{ctx.prefix}dehoist sweep` to act on these (paced)."))

    @dehoist_grp.command(name="sweep")
    @commands.bot_has_guild_permissions(manage_nicknames=True)
    async def dehoist_sweep(self, ctx: DiscoContext) -> None:
        """Apply the configured action to all current matches, paced via BulkRunner."""
        s = await self._settings(ctx.guild.id)
        mode = _mode_of(s)
        if mode == "off":
            await ctx.reply_error("Dehoist mode is `off`. Set a mode first.")
            return
        floor = self._floor_role(ctx.guild, s)
        sig = await self._signals(ctx.guild, s)
        wl = self._whitelist(s)
        targets: list[tuple[discord.Member, dh.Detection]] = []
        for m in ctx.guild.members:
            if m.id in wl or not self._eligible(m, floor):
                continue
            det = dh.classify({m.name, m.display_name}, sig, display_name=m.display_name)
            if det is not None:
                targets.append((m, det))
        if not targets:
            await send_v2(ctx, Container(accent_color=C_SUCCESS).text("Nothing to sweep."))
            return

        progress = await ctx.reply(view=_V2Line(C_INFO,
                                   f"Sweeping {len(targets)} match(es) (mode: {mode})..."),
                                   mention_author=False)

        async def _do(pair) -> None:
            m, det = pair
            await self._act(m, det, mode, "sweep", s)

        async def _tick(res: object) -> None:
            try:
                await progress.edit(view=_V2Line(
                    C_INFO, f"Sweeping... {res.processed}/{res.total} done."))
            except Exception:
                pass

        runner = BulkRunner(base_delay=1.0)
        res = await runner.run([t for t in targets], _do, progress=_tick, progress_every=10)
        tail = f"\nAborted: {res.abort_reason}" if res.aborted else ""
        await progress.edit(view=_V2Line(
            C_SUCCESS if not res.aborted else C_WARNING,
            f"Sweep done: {res.succeeded} acted, {res.failed} failed of {res.total}.{tail}"))

    @dehoist_grp.command(name="whitelist", aliases=["wl"])
    async def dehoist_whitelist(self, ctx: DiscoContext, member: discord.Member) -> None:
        """Exempt a member from future dehoist checks."""
        s = await self._settings(ctx.guild.id)
        wl = self._whitelist(s)
        wl.add(member.id)
        await self.db.update_guild_setting(ctx.guild.id, "dehoist_whitelist", sorted(wl))
        await send_v2(ctx, Container(accent_color=C_SUCCESS).text(
            f"{member.mention} whitelisted -- the dehoister will leave them alone."))

    @dehoist_grp.command(name="recent", aliases=["events"])
    async def dehoist_recent(self, ctx: DiscoContext, limit: int = 10) -> None:
        """Show recent dehoist actions."""
        rows = await self.bot.db.fetch_all(
            "SELECT * FROM dehoist_events WHERE guild_id=$1 ORDER BY created_at DESC LIMIT $2",
            ctx.guild.id, max(1, min(limit, 30)))
        if not rows:
            await send_v2(ctx, Container(accent_color=C_INFO).text("No dehoist actions yet."))
            return
        lines = "\n".join(
            f"- <@{r['user_id']}> `{r['old_name']}` -> `{r['new_name']}` "
            f"**{r['kind']}** ({r['action']}, {r['trigger']})"
            for r in rows)
        await send_v2(ctx, Container(accent_color=C_NAVY)
                      .text("## Recent dehoists").separator().text(lines))

    # -- act-fast slash commands ---------------------------------------------

    @app_commands.command(name="report", description="Report a suspected scammer to the mods.")
    @app_commands.guild_only()
    @app_commands.describe(user="The member you're reporting", reason="What's suspicious")
    async def slash_report(self, interaction: discord.Interaction,
                           user: discord.Member, reason: str = "") -> None:
        """Public, in-the-moment report -> posts an actionable alert for mods."""
        await interaction.response.defer(ephemeral=True)
        if user.id == interaction.user.id:
            await interaction.followup.send("You can't report yourself, lol.", ephemeral=True)
            return
        if user.bot:
            await interaction.followup.send("That's a bot -- nothing to report.", ephemeral=True)
            return
        s = await self._settings(interaction.guild_id)
        if self._is_protected(user, s):
            await interaction.followup.send(
                "You can't report a moderator or a clanker hunter.", ephemeral=True)
            return
        sig = await self._signals(interaction.guild, s)
        det = dh.classify({user.name, user.display_name}, sig, display_name=user.display_name)
        cid = s.get("dehoist_log_channel") or s.get("scam_report_channel") \
            or s.get("clanktank_log_channel")
        ch = interaction.guild.get_channel(int(cid)) if cid else None
        if ch is not None:
            try:
                await ch.send(view=_ReportAlert(self, user, interaction.user, reason, det))
            except Exception:
                log.debug("dehoist: report alert failed")
        try:
            await self.bot.modlog.security(
                "member.report", interaction.guild_id, actor=interaction.user, target=user,
                summary=f"{interaction.user} reported {user}.",
                metadata={"reason": reason or "(none)",
                          "auto_match": det.matched if det else "none"})
        except Exception:
            pass
        await interaction.followup.send(
            "Thanks -- your report was sent to the moderators.", ephemeral=True)

    def _is_protected(self, member: discord.Member, s: dict) -> bool:
        """A member who can't be reported: mods/admins and clanker hunters."""
        p = getattr(member, "guild_permissions", None)
        if p and (p.administrator or p.manage_guild or p.manage_roles
                  or p.manage_messages or p.moderate_members):
            return True
        hid = s.get("scam_hunter_role")
        return bool(hid and any(r.id == int(hid) for r in getattr(member, "roles", [])))

    @app_commands.command(name="dehoist", description="Dehoist a specific member right now.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_nicknames=True)
    @app_commands.describe(user="The member to dehoist now")
    async def slash_dehoist(self, interaction: discord.Interaction,
                            user: discord.Member) -> None:
        """Mod-only fast path: dehoist one member immediately using current mode."""
        if not interaction.user.guild_permissions.manage_guild \
                and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You need Manage Server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        s = await self._settings(interaction.guild_id)
        sig = await self._signals(interaction.guild, s)
        det = dh.classify({user.name, user.display_name}, sig, display_name=user.display_name)
        if det is None:
            # Force a plain hoist-strip even without a pattern match.
            clean = dh.clean_nick(user.display_name)
            if clean == user.display_name:
                await interaction.followup.send(
                    f"`{user.display_name}` has nothing to dehoist.", ephemeral=True)
                return
            det = dh.Detection(kind="hoist", matched="manual", confidence=1.0,
                               clean_nick=clean)
        mode = _mode_of(s)
        await self._act(user, det, mode if mode != "off" else "rename", "manual", s)
        await interaction.followup.send(
            f"Dehoisted {user.mention} -> `{det.clean_nick}`.", ephemeral=True)


class _V2Line(discord.ui.LayoutView):
    """A one-line Components V2 status panel for live-updating progress."""

    def __init__(self, color: int, text: str) -> None:
        super().__init__(timeout=None)
        self.add_item(discord.ui.Container(discord.ui.TextDisplay(text), accent_color=color))


class _DehoistAlert(discord.ui.LayoutView):
    """Actionable alert posted when the bot auto-dehoists someone."""

    def __init__(self, cog: SmartDehoist, member: discord.Member, det: dh.Detection,
                 old: str, renamed: bool, clanked: bool, trigger: str) -> None:
        super().__init__(timeout=86400)
        self.cog = cog
        self.member = member
        self.det = det
        self.old = old
        self.clanked = clanked
        self._build(renamed, trigger)

    def _build(self, renamed: bool, trigger: str) -> None:
        self.clear_items()
        status = []
        status.append("renamed" if renamed else "rename skipped")
        status.append("auto-clanked" if self.clanked else "not clanked")
        rows: list[discord.ui.Item] = [
            discord.ui.TextDisplay(f"## Dehoist: {self.det.kind} ({self.det.confidence:.0%})"),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            discord.ui.TextDisplay(
                f"**Member**  {self.member.mention} (`{self.member.id}`)\n"
                f"**Was**  `{self.old}`  ->  `{self.det.clean_nick}`\n"
                f"**Matched**  {self.det.matched}\n"
                f"**Trigger**  {trigger}   |   **Status**  {', '.join(status)}"),
        ]
        undo = discord.ui.Button(label="Undo rename", style=discord.ButtonStyle.secondary,
                                 custom_id=f"dh:undo:{self.member.id}")
        undo.callback = self._undo
        wl = discord.ui.Button(label="Whitelist", style=discord.ButtonStyle.success,
                               custom_id=f"dh:wl:{self.member.id}")
        wl.callback = self._whitelist
        section = discord.ui.Section(discord.ui.TextDisplay("-# Resolve inline:"),
                                     accessory=undo)
        rows.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))
        rows.append(section)
        rows.append(discord.ui.Section(discord.ui.TextDisplay(
            "-# Whitelist if this was a false positive."), accessory=wl))
        if not self.clanked:
            clank_btn = discord.ui.Button(label="Clank now", style=discord.ButtonStyle.danger,
                                          custom_id=f"dh:clank:{self.member.id}")
            clank_btn.callback = self._clank
            rows.append(discord.ui.Section(discord.ui.TextDisplay(
                "-# Or contain them now."), accessory=clank_btn))
        self.add_item(discord.ui.Container(*rows, accent_color=C_WARNING))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.manage_guild \
                or interaction.user.guild_permissions.administrator:
            return True
        await interaction.response.send_message("Mods only.", ephemeral=True)
        return False

    async def _undo(self, interaction: discord.Interaction) -> None:
        try:
            await self.member.edit(nick=self.old[:32], reason="Dehoist undo by mod")
            await interaction.response.send_message(
                f"Reverted {self.member.mention} to `{self.old}`.", ephemeral=True)
        except discord.HTTPException as exc:
            await interaction.response.send_message(f"Couldn't revert: {exc}", ephemeral=True)

    async def _whitelist(self, interaction: discord.Interaction) -> None:
        s = await self.cog._settings(self.member.guild.id)
        wl = self.cog._whitelist(s)
        wl.add(self.member.id)
        await self.cog.db.update_guild_setting(self.member.guild.id, "dehoist_whitelist", sorted(wl))
        await interaction.response.send_message(
            f"Whitelisted {self.member.mention}.", ephemeral=True)

    async def _clank(self, interaction: discord.Interaction) -> None:
        clank = self.cog.bot.get_cog("Clanktank")
        if clank is None or not hasattr(clank, "warden_contain"):
            await interaction.response.send_message("Clank cog unavailable.", ephemeral=True)
            return
        # Defer the component update first: containment can take longer than the
        # 3s interaction window, which would otherwise fail the click outright.
        await interaction.response.defer()
        try:
            await clank.warden_contain(self.member, reason="Mod clank from dehoist alert")
            self.clanked = True
            self._build(True, "alert")
            await interaction.edit_original_response(view=self)
        except Exception:
            log.exception("dehoist: alert-clank failed target=%s", self.member.id)
            await interaction.followup.send("Clank failed.", ephemeral=True)


class _ReportAlert(discord.ui.LayoutView):
    """Actionable alert posted when a member uses /report."""

    def __init__(self, cog: SmartDehoist, target: discord.Member,
                 reporter: discord.abc.User, reason: str, det: dh.Detection | None) -> None:
        super().__init__(timeout=86400)
        self.cog = cog
        self.target = target
        self.reporter = reporter
        auto = f"auto-match: {det.matched} ({det.kind})" if det else "no auto-match"
        rows: list[discord.ui.Item] = [
            discord.ui.TextDisplay("## Scammer report"),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            discord.ui.TextDisplay(
                f"**Reported**  {target.mention} (`{target.id}`)\n"
                f"**By**  {reporter.mention}\n"
                f"**Reason**  {reason or '_none given_'}\n"
                f"**Signal**  {auto}"),
        ]
        clank_btn = discord.ui.Button(label="Clank", style=discord.ButtonStyle.danger,
                                      custom_id=f"rep:clank:{target.id}")
        clank_btn.callback = self._clank
        false_btn = discord.ui.Button(label="False report (30m)", style=discord.ButtonStyle.secondary,
                                      custom_id=f"rep:false:{reporter.id}")
        false_btn.callback = self._false
        rows.append(discord.ui.Section(
            discord.ui.TextDisplay("-# Mods: clank the reported user..."), accessory=clank_btn))
        rows.append(discord.ui.Section(
            discord.ui.TextDisplay("-# ...or, if this report is bogus, clank the reporter for 30m."),
            accessory=false_btn))
        self.add_item(discord.ui.Container(*rows, accent_color=C_ERROR))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.manage_guild \
                or interaction.user.guild_permissions.administrator:
            return True
        await interaction.response.send_message("Mods only.", ephemeral=True)
        return False

    async def _clank(self, interaction: discord.Interaction) -> None:
        clank = self.cog.bot.get_cog("Clanktank")
        if clank is None or not hasattr(clank, "warden_contain"):
            await interaction.response.send_message("Clank cog unavailable.", ephemeral=True)
            return
        # Containment hits the gateway several times (role edits, DB, purge), which
        # can exceed Discord's 3s interaction window -- defer first so the token
        # stays valid and the click never shows "This interaction failed".
        await interaction.response.defer(ephemeral=True)
        try:
            await clank.warden_contain(self.target, reason=f"Clank from report by {interaction.user}")
            await interaction.followup.send(f"Clanked {self.target.mention}.", ephemeral=True)
        except ValueError as exc:
            await interaction.followup.send(f"Couldn't clank: {exc}", ephemeral=True)
        except Exception:
            log.exception("dehoist: report-clank failed target=%s", self.target.id)
            await interaction.followup.send("Clank failed (check the bot's role hierarchy).", ephemeral=True)

    async def _false(self, interaction: discord.Interaction) -> None:
        """False report: clank the *reporter* for 30 minutes."""
        clank = self.cog.bot.get_cog("Clanktank")
        member = interaction.guild.get_member(getattr(self.reporter, "id", 0))
        if clank is None or not hasattr(clank, "warden_contain") or member is None:
            await interaction.response.send_message(
                "Can't action that (reporter left, or clank cog unavailable).", ephemeral=True)
            return
        # Defer before the (slow) containment path so the interaction does not
        # expire mid-clank -- otherwise the follow-up reply 404s and Discord shows
        # "This interaction failed".
        await interaction.response.defer(ephemeral=True)
        try:
            await clank.warden_contain(
                member, reason=f"False scam report (marked by {interaction.user})",
                duration_s=1800)
            await interaction.followup.send(
                f"Marked false -- clanked {member.mention} for 30 minutes.", ephemeral=True)
        except ValueError as exc:
            await interaction.followup.send(f"Couldn't clank the reporter: {exc}", ephemeral=True)
        except Exception:
            log.exception("dehoist: false-report clank failed reporter=%s", member.id)
            await interaction.followup.send(
                "Couldn't clank the reporter (check the bot's role hierarchy).", ephemeral=True)


class _DehoistConfig(discord.ui.LayoutView):
    """Interactive config panel: enable toggle, mode cycle, floor role, log channel."""

    def __init__(self, cog: SmartDehoist, author_id: int, guild: discord.Guild, s: dict) -> None:
        super().__init__(timeout=180)
        self.cog = cog
        self.author_id = author_id
        self.guild = guild
        self.s = dict(s)
        self._build()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Not your panel.", ephemeral=True)
            return False
        return True

    def _build(self) -> None:
        self.clear_items()
        enabled = bool(self.s.get("dehoist_enabled"))
        mode = _mode_of(self.s)
        floor_id = self.s.get("dehoist_floor_role")
        floor = self.guild.get_role(int(floor_id)) if floor_id else None
        log_id = self.s.get("dehoist_log_channel")

        en_btn = discord.ui.Button(label="ON" if enabled else "OFF",
                                   style=discord.ButtonStyle.success if enabled
                                   else discord.ButtonStyle.secondary,
                                   custom_id="dhc:enabled")
        en_btn.callback = self._toggle_enabled
        mode_btn = discord.ui.Button(label=f"Mode: {mode}", style=discord.ButtonStyle.primary,
                                     custom_id="dhc:mode")
        mode_btn.callback = self._cycle_mode

        rows: list[discord.ui.Item] = [
            discord.ui.TextDisplay("## Smart Dehoist -- configuration"),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            discord.ui.Section(discord.ui.TextDisplay(
                "**Enabled**\n-# Scan members against this server's signals."),
                accessory=en_btn),
            discord.ui.Section(discord.ui.TextDisplay(
                "**Action mode**\n-# off / warn / rename / rename_clank."),
                accessory=mode_btn),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            discord.ui.TextDisplay(
                f"**Floor role**  {floor.mention if floor else '_unset_ (only staff are exempt)'}\n"
                f"**Log channel**  {f'<#{log_id}>' if log_id else '_unset_ (uses Clanker log)'}"),
        ]
        self.add_item(discord.ui.Container(*rows, accent_color=C_NAVY))

        floor_sel = discord.ui.RoleSelect(placeholder="Set the floor role...", max_values=1)
        floor_sel.callback = self._set_floor
        self.add_item(discord.ui.ActionRow(floor_sel))
        ch_sel = discord.ui.ChannelSelect(channel_types=[discord.ChannelType.text],
                                          placeholder="Set the dehoist log channel...", max_values=1)
        ch_sel.callback = self._set_channel
        self.add_item(discord.ui.ActionRow(ch_sel))

    async def _persist(self, key: str, value) -> None:
        self.s[key] = value
        await self.cog.db.update_guild_setting(self.guild.id, key, value)

    async def _toggle_enabled(self, interaction: discord.Interaction) -> None:
        await safe_defer(interaction)
        await self._persist("dehoist_enabled", not bool(self.s.get("dehoist_enabled")))
        self._build()
        await safe_edit(interaction, self)

    async def _cycle_mode(self, interaction: discord.Interaction) -> None:
        await safe_defer(interaction)
        cur = _mode_of(self.s)
        nxt = _MODES[(_MODES.index(cur) + 1) % len(_MODES)]
        await self._persist("dehoist_mode", nxt)
        self._build()
        await safe_edit(interaction, self)

    async def _set_floor(self, interaction: discord.Interaction) -> None:
        role = interaction.data["values"][0]
        await safe_defer(interaction)
        await self._persist("dehoist_floor_role", int(role))
        self._build()
        await safe_edit(interaction, self)

    async def _set_channel(self, interaction: discord.Interaction) -> None:
        cid = interaction.data["values"][0]
        await safe_defer(interaction)
        await self._persist("dehoist_log_channel", int(cid))
        self._build()
        await safe_edit(interaction, self)


async def setup(bot) -> None:
    await bot.add_cog(SmartDehoist(bot))
