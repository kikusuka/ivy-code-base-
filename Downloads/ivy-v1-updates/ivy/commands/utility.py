# commands/utility.py
# Ivy Bot - Utility Slash Commands
# /help, /profile, /leaf create/list/reply
# Available to all users. Profile shows different data to mods vs users.

import discord
import logging
import datetime
from discord import app_commands
from discord.ext import commands
from typing import Optional

from data.db import (
    get_guild, get_user, get_reputation, get_user_strikes,
    get_username_history, can_open_leaf, get_active_leaves,
    get_leaf_by_channel, add_leaf_message,
)
from config import BOT_VERSION, APPEAL

log = logging.getLogger("ivy.commands.utility")


def _is_mod(member: discord.Member, guild_data: dict) -> bool:
    if member.guild_permissions.administrator:
        return True
    if member.guild_permissions.moderate_members:
        return True
    mod_role_id = guild_data.get("mod_role_id")
    admin_role_id = guild_data.get("admin_role_id")
    role_ids = [r.id for r in member.roles]
    if mod_role_id and int(mod_role_id) in role_ids:
        return True
    if admin_role_id and int(admin_role_id) in role_ids:
        return True
    return False


class UtilityCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ========================
    # /help
    # ========================

    @app_commands.command(name="help", description="Learn what Ivy does and how to use her")
    async def help(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title=f"🌿 Ivy v{BOT_VERSION} — Command Reference",
            description=(
                "I'm Ivy. I keep servers clean. "
                "Here's what you need to know."
            ),
            color=0x2ECC71,
        )

        embed.add_field(
            name="👤 For Everyone",
            value=(
                "`/help` — You're looking at it\n"
                "`/profile` — Your reputation card\n"
                "`/profile @user` — Another member's card (mods see full history)\n"
                "`/leaf create` — Open a strike appeal\n"
                "`/leaf list` — See active appeals (mods only)\n"
                "`/leaf reply` — Reply inside your leaf channel\n"
            ),
            inline=False,
        )

        embed.add_field(
            name="🛡️ For Mods & Admins (dot commands)",
            value=(
                "`.ivylogs #channel` — Set logs channel\n"
                "`.ivyleaf` — List active leaves\n"
                "`.ivyrules show/update/add/toggle` — Manage rules\n"
                "`.ivyanalytics` — Server analytics\n"
                "`.ivyreview <id> approve/reject` — Action flagged messages\n"
                "`.ivywarn @member reason` — Manual warning\n"
                "`.ivymute @member [mins] [reason]` — Timeout member\n"
                "`.ivyreset @member` — Clear strikes\n"
                "`.ivythank @member [reason]` — Give good point\n"
                "`.ivylevel <level>` — Change moderation level\n"
                "`.ivysetup` — Re-run server setup\n"
            ),
            inline=False,
        )

        embed.add_field(
            name="🤖 Hey Ivy",
            value=(
                "Admins can give natural language commands:\n"
                "*\"Hey Ivy warn @user for spamming\"*\n"
                "*\"Hey Ivy show analytics\"*\n"
                "*\"Hey Ivy what's the mod level?\"*\n"
            ),
            inline=False,
        )

        embed.add_field(
            name="🍃 Appeal System",
            value=(
                f"• **{APPEAL.max_appeals_per_cycle} appeals** per {APPEAL.strike_cycle} strikes\n"
                f"• **24 hour** window to appeal after a strike\n"
                f"• Win an appeal → strike reversed, try returned\n"
                f"• Mod makes the final call — not me\n"
            ),
            inline=False,
        )

        embed.add_field(
            name="⚙️ Moderation Levels",
            value=(
                "`Easy` → Severe slurs only\n"
                "`Normal` → Full badwords, scams, grooming\n"
                "`Medium` → + Politics, strike counters\n"
                "`Hard` → Full semantic AI\n"
                "`Hardcore` → Everything including neural classifier\n"
            ),
            inline=False,
        )

        embed.set_footer(
            text="For casual chat, try Breezy. For moderation issues, I'm here."
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ========================
    # /profile
    # ========================

    @app_commands.command(name="profile", description="View your Ivy reputation card")
    @app_commands.describe(member="Member to check (mods see full history)")
    async def profile(
        self,
        interaction: discord.Interaction,
        member: Optional[discord.Member] = None,
    ):
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ Use this in a server.", ephemeral=True
            )
            return

        guild_data = get_guild(interaction.guild.id)
        if not guild_data:
            await interaction.response.send_message(
                "❌ Ivy isn't set up here.", ephemeral=True
            )
            return

        is_mod_user = _is_mod(interaction.user, dict(guild_data))
        target = member or interaction.user

        # Regular users can only see their own profile
        if target.id != interaction.user.id and not is_mod_user:
            await interaction.response.send_message(
                "❌ You can only view your own profile.", ephemeral=True
            )
            return

        user_data = get_user(target.id, interaction.guild.id)
        rep = get_reputation(target.id, interaction.guild.id)

        # Reputation status label
        score = rep["score"]
        if score >= 90:
            status_label, color = "🟢 Exalted", 0x2ECC71
        elif score >= 70:
            status_label, color = "🟢 Good Standing", 0x1ABC9C
        elif score >= 50:
            status_label, color = "🟡 Standard", 0xF1C40F
        elif score >= 35:
            status_label, color = "🟠 At Risk", 0xE67E22
        else:
            status_label, color = "🔴 Hazard", 0xE74C3C

        embed = discord.Embed(
            title=f"📊 {'Full Dossier' if is_mod_user else 'Profile'}: {target.display_name}",
            color=color,
        )
        embed.set_thumbnail(url=target.display_avatar.url)

        # Everyone sees this
        embed.add_field(name="Username", value=f"`{target.name}`", inline=True)
        embed.add_field(name="Rep Score", value=f"★ **{score}/100**", inline=True)
        embed.add_field(name="Status", value=status_label, inline=True)
        embed.add_field(name="Strikes", value=f"`{rep['strikes']}`", inline=True)
        embed.add_field(name="Good Points", value=f"`{rep['good_points']}`", inline=True)

        if is_mod_user:
            # Mods see full history
            strikes = get_user_strikes(target.id, interaction.guild.id, limit=5)
            if strikes:
                strike_text = "\n".join(
                    f"• [{s['timestamp'][:10]}] `{s['layer']}` — {s['reason'][:60]}"
                    + (" *(reversed)*" if s["is_reversed"] else "")
                    for s in strikes
                )
                embed.add_field(
                    name="Recent Strikes",
                    value=strike_text,
                    inline=False,
                )
            else:
                embed.add_field(name="Strike History", value="Clean record.", inline=False)

            # Username history
            history = get_username_history(target.id, interaction.guild.id)
            if history:
                names = ", ".join(f"`{h['username']}`" for h in history[:5])
                embed.add_field(name="Username History", value=names, inline=False)

            # Account info
            embed.add_field(
                name="Account Created",
                value=f"<t:{int(target.created_at.timestamp())}:R>",
                inline=True,
            )
            embed.add_field(
                name="Joined Server",
                value=f"<t:{int(target.joined_at.timestamp())}:R>" if target.joined_at else "Unknown",
                inline=True,
            )

            # Appeal cycle info
            cycle_strikes = rep.get("cycle_strikes", 0)
            appeals_used = rep.get("appeals_used", 0)
            cycles = cycle_strikes // APPEAL.strike_cycle
            max_appeals = (cycles + 1) * APPEAL.max_appeals_per_cycle
            appeals_left = max(0, max_appeals - appeals_used)
            embed.add_field(
                name="Appeal Cycle",
                value=(
                    f"Cycle strikes: `{cycle_strikes}` | "
                    f"Appeals used: `{appeals_used}` | "
                    f"Appeals left: `{appeals_left}`"
                ),
                inline=False,
            )

        embed.set_footer(text=f"Ivy v{BOT_VERSION} • {'Mod view' if is_mod_user else 'User view'}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ========================
    # /leaf group
    # ========================

    leaf_group = app_commands.Group(name="leaf", description="Leaf appeal system")

    @leaf_group.command(name="create", description="Open a strike appeal")
    @app_commands.describe(title="Brief description of your appeal")
    async def leaf_create(self, interaction: discord.Interaction, title: str):
        if not interaction.guild:
            await interaction.response.send_message("❌ Use in a server.", ephemeral=True)
            return

        guild_data = get_guild(interaction.guild.id)
        if not guild_data:
            await interaction.response.send_message("❌ Ivy isn't set up here.", ephemeral=True)
            return

        can_open, reason = can_open_leaf(interaction.user.id, interaction.guild.id)
        if not can_open:
            await interaction.response.send_message(f"❌ {reason}", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        from leaves.appeal import create_leaf_channel
        channel = await create_leaf_channel(
            guild=interaction.guild,
            member=interaction.user,
            title=title,
            category="appeal",
        )

        if channel:
            await interaction.followup.send(
                f"✅ Appeal channel created: {channel.mention}\n"
                f"Explain your case there. You have 24 hours.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                "❌ Couldn't create appeal channel. "
                "Check if you have tries remaining or if the window has passed.",
                ephemeral=True,
            )

    @leaf_group.command(name="list", description="List active appeal leaves (mods only)")
    async def leaf_list(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ Use in a server.", ephemeral=True)
            return

        guild_data = get_guild(interaction.guild.id)
        if not guild_data or not _is_mod(interaction.user, dict(guild_data)):
            await interaction.response.send_message(
                "❌ Mod clearance required.", ephemeral=True
            )
            return

        leaves = get_active_leaves(interaction.guild.id)
        if not leaves:
            await interaction.response.send_message(
                "🍃 No active leaves. Quiet day.", ephemeral=True
            )
            return

        embed = discord.Embed(title="🍃 Active Leaves", color=0x1ABC9C)
        text = ""
        for leaf in leaves[:10]:
            assigned = f"<@{leaf['assigned_mod']}>" if leaf["assigned_mod"] else "⏳ Unassigned"
            text += (
                f"**#{leaf['id']}** — {leaf['title']}\n"
                f"  • <@{leaf['user_id']}> | `{leaf['status']}` | {assigned}\n"
                f"  • <#{leaf['channel_id']}>\n\n"
            )
        embed.description = text
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @leaf_group.command(name="reply", description="Send a reply in your leaf channel")
    @app_commands.describe(message="Your message")
    async def leaf_reply(self, interaction: discord.Interaction, message: str):
        if not interaction.guild:
            await interaction.response.send_message("❌ Use in a server.", ephemeral=True)
            return

        leaf = get_leaf_by_channel(interaction.channel.id)
        if not leaf:
            await interaction.response.send_message(
                "❌ This isn't a leaf channel.", ephemeral=True
            )
            return

        if leaf["status"] not in ("open", "claimed"):
            await interaction.response.send_message(
                f"❌ This leaf is `{leaf['status']}` — no more replies.", ephemeral=True
            )
            return

        is_mod_user = _is_mod(
            interaction.user,
            dict(get_guild(interaction.guild.id) or {}),
        )

        add_leaf_message(
            leaf["id"],
            sender_id=interaction.user.id,
            content=message,
            is_staff=is_mod_user,
        )

        color = 0x9B59B6 if is_mod_user else 0x34495E
        role = "🛡️ Staff" if is_mod_user else "👤 Member"
        embed = discord.Embed(description=message, color=color)
        embed.set_author(
            name=f"{interaction.user.display_name} {role}",
            icon_url=interaction.user.display_avatar.url,
        )
        embed.set_footer(text=datetime.datetime.utcnow().strftime("%H:%M UTC"))

        await interaction.response.send_message(embed=embed)

        # Trigger AI response if message is from the leaf owner
        if not is_mod_user and str(interaction.user.id) == str(leaf["user_id"]):
            from leaves.chat import handle_leaf_message
            await handle_leaf_message(
                await interaction.original_response(),
                leaf["id"],
            )


    # ========================
    # /dataconsent
    # Standing command — always available, no questions asked.
    # Lets a user check or change their training-data consent anytime.
    # This never affects how Ivy moderates anyone, ever.
    # ========================

    consent_group = app_commands.Group(
        name="dataconsent", description="Manage your data consent for Ivy's training pipeline"
    )

    @consent_group.command(name="status", description="Check your current data consent status")
    async def consent_status(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("\u274c Use in a server.", ephemeral=True)
            return

        from core.consent import get_consent_summary
        summary = get_consent_summary(interaction.user.id, interaction.guild.id)

        status_text = {
            "unanswered": "\u23f3 Not yet answered",
            "opted_in": "\u2705 Opted in",
            "opted_out": "\ud83d\udeab Opted out (or not yet answered)",
        }.get(summary["individual_status"], "Unknown")

        embed = discord.Embed(title="\ud83d\udd12 Your Data Consent Status", color=0x3498DB)
        embed.add_field(name="Your Status", value=status_text, inline=False)
        embed.add_field(
            name="Server Training Pipeline",
            value="\ud83d\udfe2 Active" if summary["guild_pipeline_active"] else "\ud83d\udd34 Not active (owner hasn't enabled it)",
            inline=False,
        )
        embed.add_field(
            name="Are Your Messages Currently Used for Training?",
            value="Yes" if summary["effectively_used_for_training"] else "No",
            inline=False,
        )
        embed.set_footer(text="This never affects how Ivy moderates you. Change anytime below.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @consent_group.command(name="opt-in", description="Allow your messages to be used for training Ivy's models")
    async def consent_opt_in(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("\u274c Use in a server.", ephemeral=True)
            return
        from core.consent import manual_opt_in
        await manual_opt_in(interaction.user.id, interaction.guild.id)
        await interaction.response.send_message(
            "\u2705 You've opted in. Thank you. Revoke anytime with `/dataconsent opt-out`.",
            ephemeral=True,
        )

    @consent_group.command(name="opt-out", description="Stop your messages from being used for training, or revoke prior consent")
    async def consent_opt_out(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("\u274c Use in a server.", ephemeral=True)
            return
        from core.consent import manual_opt_out
        await manual_opt_out(interaction.user.id, interaction.guild.id)
        await interaction.response.send_message(
            "\u2705 You've opted out. Your data will not be used for training. "
            "This never affects how Ivy moderates you.",
            ephemeral=True,
        )

    # ========================
    # /verify
    # Checks a DM's verification seal against the user's own real
    # strike records. Answers a real threat: a lookalike bot or a
    # prankster sending a fake "you got warned by Ivy" message. The
    # seal is an HMAC signed with a secret only Ivy's process holds —
    # nobody can produce a valid one without it, no matter how
    # convincing the rest of a fake message looks.
    # ========================

    @app_commands.command(name="verify", description="Check if a DM claiming to be from Ivy is genuine")
    @app_commands.describe(code="The verification seal from the bottom of the DM")
    async def verify(self, interaction: discord.Interaction, code: str):
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ Use this in the server the DM claims to be from — "
                "the seal is tied to a specific server.",
                ephemeral=True,
            )
            return

        from utils.verification_seal import verify_seal
        from data.db import get_all_user_strikes_for_verification

        strikes = get_all_user_strikes_for_verification(interaction.user.id, interaction.guild.id)

        matched = None
        for s in strikes:
            if verify_seal(code, s["id"], interaction.user.id, interaction.guild.id):
                matched = s
                break

        if matched:
            embed = discord.Embed(
                title="✅ Verified — Genuine Ivy Message",
                description=(
                    f"This matches a real record on file for you, "
                    f"issued {matched['timestamp'][:10]}."
                ),
                color=0x2ECC71,
            )
        else:
            embed = discord.Embed(
                title="❌ Not Verified",
                description=(
                    "This code doesn't match anything on file for you "
                    "in this server. This did not come from me — "
                    "treat it as fake."
                ),
                color=0xE74C3C,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ========================
    # /ivyterms
    # Standing, always-available disclosure of what Ivy actually does
    # here — not a one-time DM blast on join (intrusive, fails silently
    # against closed DMs, reads as spammy at scale). Anyone can check
    # this anytime instead. Transparency as a design principle, not a
    # single moment.
    # ========================

    @app_commands.command(name="ivyterms", description="What Ivy actually does in this server")
    async def ivyterms(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🌿 What Ivy Actually Does Here",
            color=0x34495E,
        )
        embed.add_field(
            name="Moderation — always on, no opt-out",
            value=(
                "• Every message is checked against this server's rules "
                "and general safety filters.\n"
                "• Applies equally to everyone, including staff — "
                "nothing is silently exempt.\n"
                "• If something's flagged, you'll know — strikes are "
                "DMed to you directly, with a reason, every time."
            ),
            inline=False,
        )
        embed.add_field(
            name="What IS optional",
            value=(
                "Whether messages Ivy takes action on can be used later "
                "to improve her own moderation models. Check or change "
                "this anytime with `/dataconsent`. That choice never "
                "changes how you're moderated — same rules either way."
            ),
            inline=False,
        )
        embed.add_field(
            name="Your rights here",
            value=(
                "• Every strike can be appealed within 24 hours via "
                "`/leaf create`. A human moderator always makes the "
                "final call, not me.\n"
                "• `/profile` shows your own record anytime.\n"
                "• Every real DM from me carries a verification seal — "
                "check any suspicious one with `/verify`."
            ),
            inline=False,
        )
        embed.set_footer(text="Questions a bot can't answer? Ask a moderator.")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(UtilityCommands(bot))
