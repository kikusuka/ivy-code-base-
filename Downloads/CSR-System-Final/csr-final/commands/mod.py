import discord
from discord.ext import commands
from discord import app_commands
from datetime import timedelta
import os


def mod_embed(action, target, mod, reason, color):
    embed = discord.Embed(title=f'🛡️ {action}', color=color)
    embed.add_field(name='Member',    value=f'{target} (`{target.id}`)', inline=True)
    embed.add_field(name='Moderator', value=str(mod),                    inline=True)
    embed.add_field(name='Reason',    value=reason or 'No reason provided', inline=False)
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.timestamp = discord.utils.utcnow()
    return embed


async def log_action(guild, embed):
    log_id = os.getenv('LOG_CHANNEL_ID')
    if not log_id:
        return
    ch = guild.get_channel(int(log_id))
    if ch:
        try:
            await ch.send(embed=embed)
        except Exception:
            pass


class ModCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    mod_group = app_commands.Group(
        name='mod',
        description='Moderation commands',
        default_permissions=discord.Permissions(moderate_members=True)
    )

    @mod_group.command(name='ban', description='Ban a member')
    @app_commands.describe(target='Member to ban', reason='Reason', days='Delete messages (0-7 days)')
    async def ban(self, interaction: discord.Interaction, target: discord.Member, reason: str = None, days: app_commands.Range[int, 0, 7] = 0):
        if not target.guild_permissions.administrator is False and target.top_role >= interaction.guild.me.top_role:
            return await interaction.response.send_message('❌ I cannot ban this member.', ephemeral=True)
        try:
            await target.send(f'🔨 You have been **banned** from **{interaction.guild.name}**.\nReason: {reason or "No reason provided"}')
        except Exception:
            pass
        await target.ban(reason=reason, delete_message_days=days)
        embed = mod_embed('Member Banned 🔨', target, interaction.user, reason, 0xe74c3c)
        await log_action(interaction.guild, embed)
        await interaction.response.send_message(embed=embed)

    @mod_group.command(name='kick', description='Kick a member')
    @app_commands.describe(target='Member to kick', reason='Reason')
    async def kick(self, interaction: discord.Interaction, target: discord.Member, reason: str = None):
        try:
            await target.send(f'👟 You have been **kicked** from **{interaction.guild.name}**.\nReason: {reason or "No reason provided"}')
        except Exception:
            pass
        await target.kick(reason=reason)
        embed = mod_embed('Member Kicked 👟', target, interaction.user, reason, 0xe67e22)
        await log_action(interaction.guild, embed)
        await interaction.response.send_message(embed=embed)

    @mod_group.command(name='mute', description='Timeout (mute) a member')
    @app_commands.describe(target='Member to mute', minutes='Duration in minutes (max 40320)', reason='Reason')
    async def mute(self, interaction: discord.Interaction, target: discord.Member, minutes: app_commands.Range[int, 1, 40320], reason: str = None):
        await target.timeout(timedelta(minutes=minutes), reason=reason)
        try:
            await target.send(f'🔇 You have been **muted** in **{interaction.guild.name}** for {minutes} minute(s).\nReason: {reason or "No reason provided"}')
        except Exception:
            pass
        embed = mod_embed(f'Member Muted 🔇 ({minutes}min)', target, interaction.user, reason, 0xf39c12)
        await log_action(interaction.guild, embed)
        await interaction.response.send_message(embed=embed)

    @mod_group.command(name='unmute', description='Remove a timeout from a member')
    @app_commands.describe(target='Member to unmute')
    async def unmute(self, interaction: discord.Interaction, target: discord.Member):
        await target.timeout(None)
        try:
            await target.send(f'🔊 Your timeout in **{interaction.guild.name}** has been removed.')
        except Exception:
            pass
        embed = mod_embed('Timeout Removed 🔊', target, interaction.user, 'Unmuted by moderator', 0x2ecc71)
        await log_action(interaction.guild, embed)
        await interaction.response.send_message(embed=embed)

    @mod_group.command(name='warn', description='Warn a member via DM')
    @app_commands.describe(target='Member to warn', reason='Warning reason')
    async def warn(self, interaction: discord.Interaction, target: discord.Member, reason: str):
        try:
            await target.send(
                f'⚠️ **Warning from {interaction.guild.name}**\n\n'
                f'Reason: **{reason}**\n\n'
                f'*Please review the server rules to avoid further action.*'
            )
        except Exception:
            pass
        embed = mod_embed('Member Warned ⚠️', target, interaction.user, reason, 0xf1c40f)
        await log_action(interaction.guild, embed)
        await interaction.response.send_message(embed=embed)

    @mod_group.command(name='purge', description='Bulk delete messages (1–100)')
    @app_commands.describe(amount='Number of messages to delete')
    async def purge(self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100]):
        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=amount)
        await interaction.followup.send(f'🗑️ Deleted **{len(deleted)}** messages.', ephemeral=True)


async def setup(bot):
    cog = ModCog(bot)
    bot.tree.add_command(cog.mod_group)
    await bot.add_cog(cog)
