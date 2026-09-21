# commands/admin.py
# Ivy Bot - Admin Dot Commands
# All prefix commands restricted to server owner, admins, and registered staff.
# Prefix: . (dot)
# Only humans with the right roles can use these.

import discord
import logging
import datetime
from discord.ext import commands

from config import KIKUSUKA_ID, KIKUSUKA_SERVER_ID
from data.db import (
    get_guild, update_guild, is_partner, add_partner, remove_partner,
    get_pending_reviews, resolve_review, get_user, get_reputation,
    get_user_strikes, get_rules, toggle_rule, add_good_point,
    update_user, db_cursor,
)
from data.analytics import build_analytics_embed, build_mini_health_embed
from core.rules import parse_rules_from_text, add_single_rule, format_rules_for_embed
from core.setup import initiate_setup

log = logging.getLogger("ivy.commands.admin")


# ========================
# PERMISSION HELPERS
# ========================

def _is_admin(ctx: commands.Context, guild_data: dict) -> bool:
    """Checks if the command author has admin clearance."""
    member = ctx.author
    if member.guild_permissions.administrator:
        return True
    admin_role_id = guild_data.get("admin_role_id")
    if admin_role_id and int(admin_role_id) in [r.id for r in member.roles]:
        return True
    return False


def _is_mod(ctx: commands.Context, guild_data: dict) -> bool:
    """Checks if the command author has mod clearance."""
    if _is_admin(ctx, guild_data):
        return True
    member = ctx.author
    if member.guild_permissions.moderate_members:
        return True
    mod_role_id = guild_data.get("mod_role_id")
    if mod_role_id and int(mod_role_id) in [r.id for r in member.roles]:
        return True
    return False


def _is_kikusuka(ctx: commands.Context) -> bool:
    return ctx.author.id == KIKUSUKA_ID


# ========================
# ADMIN COG
# ========================

class AdminCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ========================
    # .ivylogs
    # Sets up or displays the logs channel.
    # ========================

    @commands.command(name="ivylogs")
    async def ivylogs(self, ctx: commands.Context, channel: discord.TextChannel = None):
        """Set or display the moderation logs channel."""
        if not ctx.guild:
            return

        guild_data = get_guild(ctx.guild.id)
        if not guild_data:
            await ctx.send("❌ Ivy isn't set up in this server yet.")
            return

        if not _is_admin(ctx, dict(guild_data)):
            await ctx.send("❌ Admin clearance required.", delete_after=5)
            return

        if channel:
            update_guild(ctx.guild.id, logs_channel_id=channel.id)
            embed = discord.Embed(
                title="📋 Logs Channel Updated",
                description=f"Moderation logs will now go to {channel.mention}.",
                color=0x2ECC71,
            )
            await ctx.send(embed=embed)
            log.info(f"[guild={ctx.guild.id}] Logs channel set to #{channel.name}")
        else:
            # Show current logs channel
            log_ch_id = guild_data["logs_channel_id"]
            if log_ch_id:
                ch = ctx.guild.get_channel(int(log_ch_id))
                await ctx.send(
                    f"📋 Current logs channel: {ch.mention if ch else '`Not found`'}. "
                    f"Use `.ivylogs #channel` to change it."
                )
            else:
                await ctx.send("📋 No logs channel set. Use `.ivylogs #channel` to set one.")

    # ========================
    # .ivyleaf
    # Opens a leaf appeal or lists active leaves.
    # ========================

    @commands.command(name="ivyleaf")
    async def ivyleaf(self, ctx: commands.Context, *, title: str = None):
        """Open a leaf appeal or list active leaves."""
        if not ctx.guild:
            return

        guild_data = get_guild(ctx.guild.id)
        if not guild_data:
            await ctx.send("❌ Ivy isn't set up here yet.")
            return

        is_mod_member = _is_mod(ctx, dict(guild_data))

        if not title:
            # List active leaves — mods only
            if not is_mod_member:
                await ctx.send(
                    "❌ Provide a title to open a leaf. "
                    "Example: `.ivyleaf My message was flagged unfairly`"
                )
                return

            from data.db import get_active_leaves
            leaves = get_active_leaves(ctx.guild.id)
            if not leaves:
                await ctx.send("🍃 No active leaves right now. Quiet day.")
                return

            embed = discord.Embed(
                title="🍃 Active Leaves Queue",
                color=0x1ABC9C,
            )
            text = ""
            for leaf in leaves[:10]:
                assigned = (
                    f"<@{leaf['assigned_mod']}>"
                    if leaf["assigned_mod"] else "⏳ Unassigned"
                )
                text += (
                    f"**#{leaf['id']}** — {leaf['title']}\n"
                    f"  • <@{leaf['user_id']}> | `{leaf['status']}` | {assigned}\n"
                    f"  • <#{leaf['channel_id']}>\n\n"
                )
            embed.description = text
            await ctx.send(embed=embed)
            return

        # Open a new leaf
        from leaves.appeal import create_leaf_channel
        from data.db import can_open_leaf

        can_open, reason = can_open_leaf(ctx.author.id, ctx.guild.id)
        if not can_open:
            await ctx.send(f"❌ {reason}")
            return

        await ctx.send("🍃 Creating your appeal channel...")
        channel = await create_leaf_channel(
            guild=ctx.guild,
            member=ctx.author,
            title=title,
            category="appeal",
        )

        if channel:
            await ctx.send(
                f"✅ Your appeal channel is ready: {channel.mention}\n"
                f"Explain your case there. A moderator will review it.",
                delete_after=15,
            )
        else:
            await ctx.send(
                "❌ Couldn't create your appeal channel. "
                "You may have no tries left or the appeal window has passed."
            )

    # ========================
    # .partner
    # Kikusuka only — registers a server as a partner.
    # ========================

    @commands.command(name="partner")
    async def partner(self, ctx: commands.Context):
        """Register this server as an Ivy partner. Kikusuka only."""
        if not _is_kikusuka(ctx):
            await ctx.send("❌ Only Kikusuka can use this command.", delete_after=5)
            return

        if not ctx.guild:
            await ctx.send("❌ Run this command in the server you want to partner.")
            return

        if is_partner(ctx.guild.id):
            await ctx.send(f"✅ **{ctx.guild.name}** is already a partner.")
            return

        add_partner(ctx.guild.id, KIKUSUKA_ID)

        embed = discord.Embed(
            title="🤝 Partnership Confirmed",
            description=(
                f"**{ctx.guild.name}** is now an official Ivy partner!\n\n"
                f"You now have access to:\n"
                f"• **3 channel exceptions** (instead of 1)\n"
                f"• Priority support from Kikusuka\n"
                f"• Early access to new Ivy features\n\n"
                f"Thanks for being part of the network. 🌿"
            ),
            color=0x2ECC71,
        )
        await ctx.send(embed=embed)
        log.info(f"[guild={ctx.guild.id}] Partnered: {ctx.guild.name}")

    # ========================
    # .cancelpartner
    # Kikusuka only — run in Kikusuka's server with target guild ID.
    # ========================

    @commands.command(name="cancelpartner")
    async def cancelpartner(self, ctx: commands.Context, guild_id: int = None):
        """Remove a server's partner status. Kikusuka only, run in Kikusuka's server."""
        if not _is_kikusuka(ctx):
            await ctx.send("❌ Only Kikusuka can use this command.", delete_after=5)
            return

        if ctx.guild.id != KIKUSUKA_SERVER_ID:
            await ctx.send(
                "❌ Run this command in Kikusuka's server with the target guild ID.\n"
                "Usage: `.cancelpartner <guild_id>`"
            )
            return

        if not guild_id:
            await ctx.send("❌ Provide a guild ID. Usage: `.cancelpartner <guild_id>`")
            return

        if not is_partner(guild_id):
            await ctx.send(f"❌ Guild `{guild_id}` is not a partner.")
            return

        remove_partner(guild_id)
        target_guild = self.bot.get_guild(guild_id)
        name = target_guild.name if target_guild else str(guild_id)

        await ctx.send(f"✅ Partnership removed for **{name}** (`{guild_id}`).")
        log.info(f"Partnership removed: {guild_id}")

        # Notify the target server
        if target_guild:
            guild_data = get_guild(guild_id)
            if guild_data and guild_data["general_channel_id"]:
                ch = target_guild.get_channel(int(guild_data["general_channel_id"]))
                if ch:
                    try:
                        await ch.send(
                            "🌿 Your server's Ivy partnership has ended. "
                            "You now have 1 channel exception (down from 3). "
                            "Contact Kikusuka if you have questions."
                        )
                    except Exception:
                        pass

    # ========================
    # .ivyrules
    # Show, update, or add server rules.
    # ========================

    @commands.command(name="ivyrules")
    async def ivyrules(self, ctx: commands.Context, action: str = "show", *, content: str = None):
        """
        Manage server rules.
        .ivyrules show — display current rules
        .ivyrules update — paste new full rulebook
        .ivyrules add <rule description> — add a single rule
        .ivyrules toggle <slug> — enable/disable a rule
        """
        if not ctx.guild:
            return

        guild_data = get_guild(ctx.guild.id)
        if not guild_data or not _is_admin(ctx, dict(guild_data)):
            await ctx.send("❌ Admin clearance required.", delete_after=5)
            return

        action = action.lower()

        if action == "show":
            rules_text, count = format_rules_for_embed(ctx.guild.id)
            embed = discord.Embed(
                title=f"📜 Server Rules ({count} active)",
                description=rules_text[:4000],
                color=0x34495E,
            )
            await ctx.send(embed=embed)

        elif action == "update":
            if not content:
                await ctx.send(
                    "📋 Paste your full server rules after the command.\n"
                    "Example: `.ivyrules update 1. No spam 2. Be respectful...`"
                )
                return
            await ctx.send("📋 Reading and restructuring your rules...")
            from core.rules import update_rules
            result = await update_rules(content, ctx.guild.id)
            if result.success:
                response = f"✅ **{result.rules_saved}** rules updated."
                if result.unenforceable:
                    response += f"\n⚠️ {len(result.unenforceable)} rule(s) I can't enforce:\n"
                    response += "\n".join(f"• {r}" for r in result.unenforceable)
                await ctx.send(response)
            else:
                await ctx.send(f"❌ Update failed: {result.error}")

        elif action == "add":
            if not content:
                await ctx.send("❌ Describe the rule to add after `.ivyrules add`")
                return
            result = await add_single_rule(content, ctx.guild.id)
            if result.success and result.rules_saved > 0:
                await ctx.send(f"✅ Rule added successfully.")
            elif result.unenforceable:
                await ctx.send(
                    f"⚠️ Rule recorded but marked unenforceable:\n"
                    + "\n".join(f"• {r}" for r in result.unenforceable)
                )
            else:
                await ctx.send(f"❌ Failed: {result.error}")

        elif action == "toggle":
            if not content:
                await ctx.send("❌ Provide a rule slug. Example: `.ivyrules toggle no-spam`")
                return
            slug = content.strip().lower()
            rules = get_rules(ctx.guild.id)
            all_slugs = [r["slug"] for r in rules]

            with db_cursor() as cursor:
                cursor.execute(
                    "SELECT is_enabled FROM rules WHERE guild_id = ? AND slug = ?",
                    (ctx.guild.id, slug)
                )
                row = cursor.fetchone()

            if not row:
                await ctx.send(f"❌ Rule `{slug}` not found.")
                return

            new_state = not bool(row["is_enabled"])
            toggle_rule(ctx.guild.id, slug, new_state)
            state_text = "enabled 🟢" if new_state else "disabled 🔴"
            await ctx.send(f"✅ Rule `{slug}` is now **{state_text}**.")

        else:
            await ctx.send(
                "❌ Unknown action. Use `show`, `update`, `add`, or `toggle`."
            )

    # ========================
    # .ivyanalytics
    # Shows server analytics embed.
    # ========================

    @commands.command(name="ivyanalytics")
    async def ivyanalytics(self, ctx: commands.Context, mode: str = "full"):
        """
        Show server analytics.
        .ivyanalytics — full analytics
        .ivyanalytics ai — full analytics + AI suggestions
        .ivyanalytics mini — quick health check
        """
        if not ctx.guild:
            return

        guild_data = get_guild(ctx.guild.id)
        if not guild_data or not _is_mod(ctx, dict(guild_data)):
            await ctx.send("❌ Mod clearance required.", delete_after=5)
            return

        async with ctx.typing():
            if mode == "mini":
                embed = build_mini_health_embed(ctx.guild)
                await ctx.send(embed=embed)
            elif mode == "ai":
                embed, ai_text = await build_analytics_embed(
                    ctx.guild, include_ai_suggestions=True
                )
                await ctx.send(embed=embed)
                if ai_text:
                    await ctx.send(f"🌿 **Ivy's Take**:\n{ai_text}")
            else:
                embed, _ = await build_analytics_embed(ctx.guild)
                await ctx.send(embed=embed)

    # ========================
    # .ivyreview
    # Action a pending mod review.
    # ========================

    @commands.command(name="ivyreview")
    async def ivyreview(self, ctx: commands.Context, review_id: int = None, verdict: str = None):
        """
        Action a pending mod review.
        .ivyreview <id> approve — approve the flagged message (no action)
        .ivyreview <id> reject — reject it (strike the user)
        """
        if not ctx.guild:
            return

        guild_data = get_guild(ctx.guild.id)
        if not guild_data or not _is_mod(ctx, dict(guild_data)):
            await ctx.send("❌ Mod clearance required.", delete_after=5)
            return

        if not review_id or not verdict:
            pending = get_pending_reviews(ctx.guild.id)
            if not pending:
                await ctx.send("✅ No pending reviews. Clean queue.")
                return

            embed = discord.Embed(title="⏳ Pending Reviews", color=0xF39C12)
            text = ""
            for r in pending[:8]:
                text += (
                    f"**ID {r['id']}** — Score: `{r['score']:.2f}`\n"
                    f"  • <@{r['user_id']}> in <#{r['channel_id']}>\n"
                    f"  • {r['message_content'][:80]}...\n"
                    f"  • <#{r['channel_id']}> | [Jump to message](https://discord.com/channels/{ctx.guild.id}/{r['channel_id']}/{r['message_id'] or ''})\n\n"
                )
            embed.description = text or "No pending reviews."
            embed.set_footer(text="Use .ivyreview <id> approve/reject to action")
            await ctx.send(embed=embed)
            return

        verdict = verdict.lower()
        if verdict not in ("approve", "reject"):
            await ctx.send("❌ Verdict must be `approve` or `reject`.")
            return

        # Get the review
        with db_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM mod_reviews WHERE id = ? AND guild_id = ?",
                (review_id, ctx.guild.id)
            )
            review = cursor.fetchone()

        if not review:
            await ctx.send(f"❌ Review `{review_id}` not found.")
            return

        if review["status"] != "pending":
            await ctx.send(f"❌ Review `{review_id}` is already `{review['status']}`.")
            return

        resolve_review(review_id, ctx.author.id, verdict)

        if verdict == "reject":
            # Strike the user
            from data.db import add_strike
            add_strike(
                user_id=review["user_id"],
                guild_id=ctx.guild.id,
                reason=f"Mod review rejected: {review['reason'] or 'Flagged message'}",
                layer="mod_review",
                action_taken="WARN",
                channel_id=review["channel_id"],
                message_content=review["message_content"],
                score=review["score"],
            )
            member = ctx.guild.get_member(int(review["user_id"]))
            if member:
                try:
                    await member.send(
                        f"⚠️ A moderator in **{ctx.guild.name}** reviewed your flagged message "
                        f"and confirmed the violation. A strike has been added to your record."
                    )
                except Exception:
                    pass

        color = 0x2ECC71 if verdict == "approve" else 0xE74C3C
        embed = discord.Embed(
            title=f"Review #{review_id} — {'Approved ✅' if verdict == 'approve' else 'Rejected ❌'}",
            color=color,
        )
        embed.add_field(name="Actioned by", value=ctx.author.mention, inline=True)
        embed.add_field(name="User", value=f"<@{review['user_id']}>", inline=True)
        embed.add_field(name="Original Score", value=f"`{review['score']:.2f}`", inline=True)
        await ctx.send(embed=embed)

    # ========================
    # .ivywarn
    # Manually warn a user.
    # ========================

    @commands.command(name="ivywarn")
    async def ivywarn(self, ctx: commands.Context, member: discord.Member = None, *, reason: str = None):
        """Manually issue a warning to a member."""
        if not ctx.guild:
            return

        guild_data = get_guild(ctx.guild.id)
        if not guild_data or not _is_mod(ctx, dict(guild_data)):
            await ctx.send("❌ Mod clearance required.", delete_after=5)
            return

        if not member or not reason:
            await ctx.send("❌ Usage: `.ivywarn @member reason`")
            return

        if member.bot:
            await ctx.send("❌ Can't warn a bot.")
            return

        from data.db import add_strike, ensure_user
        ensure_user(member.id, ctx.guild.id, member.name, member.display_name)

        strike_id = add_strike(
            user_id=member.id,
            guild_id=ctx.guild.id,
            reason=f"Manual warn by {ctx.author.name}: {reason}",
            layer="manual",
            action_taken="WARN",
            channel_id=ctx.channel.id,
        )

        rep = get_reputation(member.id, ctx.guild.id)

        embed = discord.Embed(title="⚠️ Warning Issued", color=0xF39C12)
        embed.add_field(name="Member", value=member.mention, inline=True)
        embed.add_field(name="Strikes", value=f"`{rep['strikes']}`", inline=True)
        embed.add_field(name="Rep", value=f"★ `{rep['score']}/100`", inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.set_footer(text=f"Issued by {ctx.author.name} • Strike ID: {strike_id}")
        await ctx.send(embed=embed)

        from utils.verification_seal import generate_seal
        seal = generate_seal(strike_id, member.id, ctx.guild.id)
        seal_line = (
            f"\n\n🔏 Verification seal: `{seal}` — run `/verify {seal}` "
            f"anytime to confirm this genuinely came from me." if seal else ""
        )

        try:
            await member.send(
                f"⚠️ You received a warning in **{ctx.guild.name}**.\n"
                f"**Reason**: {reason}\n"
                f"Use `/leaf create` to appeal within 24 hours."
                f"{seal_line}"
            )
        except Exception:
            pass

    # ========================
    # .ivymute
    # Manually mute a user.
    # ========================

    @commands.command(name="ivymute")
    async def ivymute(self, ctx: commands.Context, member: discord.Member = None, minutes: int = 15, *, reason: str = "Unspecified"):
        """Timeout a member. Default 15 minutes."""
        if not ctx.guild:
            return

        guild_data = get_guild(ctx.guild.id)
        if not guild_data or not _is_mod(ctx, dict(guild_data)):
            await ctx.send("❌ Mod clearance required.", delete_after=5)
            return

        if not member:
            await ctx.send("❌ Usage: `.ivymute @member [minutes] [reason]`")
            return

        if member.bot:
            await ctx.send("❌ Can't mute a bot.")
            return

        try:
            await member.timeout(
                datetime.timedelta(minutes=minutes),
                reason=f"Ivy Manual Mute by {ctx.author.name}: {reason}",
            )
            embed = discord.Embed(title="🔇 Member Muted", color=0x9B59B6)
            embed.add_field(name="Member", value=member.mention, inline=True)
            embed.add_field(name="Duration", value=f"{minutes} minutes", inline=True)
            embed.add_field(name="Reason", value=reason, inline=False)
            embed.set_footer(text=f"By {ctx.author.name}")
            await ctx.send(embed=embed)
        except discord.Forbidden:
            await ctx.send("❌ I don't have permission to timeout this member.")
        except Exception as e:
            await ctx.send(f"❌ Mute failed: {e}")

    # ========================
    # .ivyunmute
    # Removes an active timeout. .ivymute existed with no reverse —
    # a mod had zero way to undo a mute short of waiting it out.
    # ========================

    @commands.command(name="ivyunmute")
    async def ivyunmute(self, ctx: commands.Context, member: discord.Member = None):
        """Remove an active timeout from a member."""
        if not ctx.guild:
            return

        guild_data = get_guild(ctx.guild.id)
        if not guild_data or not _is_mod(ctx, dict(guild_data)):
            await ctx.send("❌ Mod clearance required.", delete_after=5)
            return

        if not member:
            await ctx.send("❌ Usage: `.ivyunmute @member`")
            return

        if member.timed_out_until is None:
            await ctx.send(f"❌ {member.mention} isn't currently muted.")
            return

        try:
            await member.timeout(
                None,
                reason=f"Ivy Manual Unmute by {ctx.author.name}",
            )
            embed = discord.Embed(title="🔊 Member Unmuted", color=0x2ECC71)
            embed.add_field(name="Member", value=member.mention, inline=True)
            embed.set_footer(text=f"By {ctx.author.name}")
            await ctx.send(embed=embed)
        except discord.Forbidden:
            await ctx.send("❌ I don't have permission to unmute this member.")
        except Exception as e:
            await ctx.send(f"❌ Unmute failed: {e}")

    # ========================
    # .ivyreset
    # Reset a user's strikes.
    # ========================

    @commands.command(name="ivyreset")
    async def ivyreset(self, ctx: commands.Context, member: discord.Member = None):
        """Reset all strikes and restore reputation for a member."""
        if not ctx.guild:
            return

        guild_data = get_guild(ctx.guild.id)
        if not guild_data or not _is_admin(ctx, dict(guild_data)):
            await ctx.send("❌ Admin clearance required.", delete_after=5)
            return

        if not member:
            await ctx.send("❌ Usage: `.ivyreset @member`")
            return

        with db_cursor() as cursor:
            cursor.execute(
                "UPDATE strikes SET is_reversed = 1 WHERE user_id = ? AND guild_id = ?",
                (member.id, ctx.guild.id)
            )
            cursor.execute(
                """UPDATE users SET strike_count = 0, cycle_strikes = 0,
                   appeals_used = 0, good_points = 0, current_status = 'clean'
                   WHERE user_id = ? AND guild_id = ?""",
                (member.id, ctx.guild.id)
            )

        embed = discord.Embed(
            title="🧹 Record Cleared",
            description=f"{member.mention}'s strikes have been reset to zero.",
            color=0x2ECC71,
        )
        embed.set_footer(text=f"Reset by {ctx.author.name}")
        await ctx.send(embed=embed)

    # ========================
    # .ivysetup
    # Re-triggers onboarding if needed.
    # ========================

    @commands.command(name="ivysetup")
    async def ivysetup(self, ctx: commands.Context):
        """Re-run Ivy's setup flow."""
        if not ctx.guild:
            return

        if ctx.author.id != ctx.guild.owner_id:
            await ctx.send("❌ Only the server owner can re-run setup.", delete_after=5)
            return

        update_guild(ctx.guild.id, setup_complete=0, setup_step=0)
        await ctx.send("🌿 Restarting setup. Talk to me.")
        from core.setup import initiate_setup
        await initiate_setup(ctx.guild, self.bot)

    # ========================
    # .ivylevel
    # Change moderation level.
    # ========================

    @commands.command(name="ivylevel")
    async def ivylevel(self, ctx: commands.Context, level: str = None):
        """Change the server moderation level."""
        if not ctx.guild:
            return

        guild_data = get_guild(ctx.guild.id)
        if not guild_data or not _is_admin(ctx, dict(guild_data)):
            await ctx.send("❌ Admin clearance required.", delete_after=5)
            return

        valid_levels = ["easy", "normal", "medium", "hard", "hardcore"]
        if not level or level.lower() not in valid_levels:
            current = guild_data["mod_level"]
            await ctx.send(
                f"Current level: **{current.capitalize()}**\n"
                f"Valid levels: `{' | '.join(valid_levels)}`\n"
                f"Usage: `.ivylevel <level>`"
            )
            return

        update_guild(ctx.guild.id, mod_level=level.lower())
        await ctx.send(
            f"✅ Moderation level set to **{level.capitalize()}**. "
            f"Changes take effect immediately."
        )

    # ========================
    # .ivythank
    # Give a good point to a member.
    # ========================

    @commands.command(name="ivythank")
    async def ivythank(self, ctx: commands.Context, member: discord.Member = None, *, reason: str = "Being a good community member"):
        """Give a reputation point to a member."""
        if not ctx.guild:
            return

        if not member:
            await ctx.send("❌ Usage: `.ivythank @member [reason]`")
            return

        if member.id == ctx.author.id:
            await ctx.send("❌ Praising yourself? No.")
            return

        if member.bot:
            await ctx.send("❌ Bots don't get good points.")
            return

        add_good_point(member.id, ctx.guild.id)
        rep = get_reputation(member.id, ctx.guild.id)

        embed = discord.Embed(title="🌟 Reputation Boosted", color=0x2ECC71)
        embed.add_field(name="Member", value=member.mention, inline=True)
        embed.add_field(name="New Rep", value=f"★ `{rep['score']}/100`", inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.set_footer(text=f"Credited by {ctx.author.name}")
        await ctx.send(embed=embed)

    # ========================
    # .ivyexportcase
    # Export a member's full moderation case history as a PDF.
    # Every export is itself logged — this action is never silent.
    # ========================

    @commands.command(name="ivyexportcase")
    async def ivyexportcase(self, ctx: commands.Context, member: discord.Member = None):
        """Export a member's full moderation case history as a PDF. Admin only."""
        if not ctx.guild:
            return
        guild_data = get_guild(ctx.guild.id)
        if not guild_data or not _is_admin(ctx, dict(guild_data)):
            await ctx.send("❌ Admin clearance required.", delete_after=5)
            return
        if not member:
            await ctx.send("❌ Usage: `.ivyexportcase @member`")
            return

        await ctx.send("📋 Generating case export... this may take a moment.")
        from utils.case_export import export_case
        file, error = await export_case(ctx.guild, member.id, ctx.author)

        if error:
            await ctx.send(f"❌ {error}")
            return

        await ctx.send(
            f"✅ Case export for {member.mention} generated. "
            f"This action has been logged.",
            file=file,
        )


    # ========================
    # .ivycopilot
    # Toggle Ivy's active participation in the leaf channel it's run in.
    # Mod-primary is the default — this lets a mod explicitly invite
    # Ivy to actively help, without waiting for the 10-minute silence
    # window to pass.
    # ========================

    @commands.command(name="ivycopilot")
    async def ivycopilot(self, ctx: commands.Context, mode: str = None):
        """Toggle Ivy's active participation in this leaf. Usage: .ivycopilot on|off"""
        if not ctx.guild:
            return

        guild_data = get_guild(ctx.guild.id)
        if not guild_data or not _is_mod(ctx, dict(guild_data)):
            await ctx.send("❌ Mod clearance required.", delete_after=5)
            return

        from data.db import get_leaf_by_channel
        leaf = get_leaf_by_channel(ctx.channel.id)
        if not leaf:
            await ctx.send("❌ This isn't a leaf channel.", delete_after=5)
            return

        if mode is None:
            state = "ON 🟢" if leaf["copilot_enabled"] else "OFF 🔴"
            await ctx.send(
                f"Copilot mode is currently **{state}** for this leaf. "
                f"Usage: `.ivycopilot on` or `.ivycopilot off`"
            )
            return

        from leaves.chat import enable_copilot_mode, disable_copilot_mode

        if mode.lower() in ("on", "enable", "yes"):
            await enable_copilot_mode(leaf["id"])
            await ctx.send(
                "🌿 **Copilot mode enabled.** I'll actively help in this "
                "conversation now, alongside you. Just say the word if "
                "you want me to step back."
            )
        elif mode.lower() in ("off", "disable", "no"):
            await disable_copilot_mode(leaf["id"])
            await ctx.send(
                "🌿 **Copilot mode disabled.** Stepping back. You've got this."
            )
        else:
            await ctx.send("❌ Usage: `.ivycopilot on` or `.ivycopilot off`")


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCommands(bot))
