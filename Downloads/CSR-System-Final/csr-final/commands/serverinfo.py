import discord
from discord.ext import commands
from discord import app_commands


class ServerInfoCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='serverinfo', description='Server stats — members, bots, channels & more.')
    async def serverinfo(self, interaction: discord.Interaction):
        await interaction.response.defer()
        guild = interaction.guild
        await guild.chunk()  # Ensure full member cache

        total    = guild.member_count
        bots     = sum(1 for m in guild.members if m.bot)
        humans   = total - bots
        online   = sum(1 for m in guild.members if m.status == discord.Status.online)
        roles    = len(guild.roles) - 1  # exclude @everyone
        text_ch  = len([c for c in guild.channels if isinstance(c, discord.TextChannel)])
        voice_ch = len([c for c in guild.channels if isinstance(c, discord.VoiceChannel)])
        total_ch = len(guild.channels)

        owner = guild.owner
        created_ts = int(guild.created_at.timestamp())

        embed = discord.Embed(title=f'📊 {guild.name} — Server Info', color=0x2d7dd2)
        embed.set_thumbnail(url=guild.icon.url if guild.icon else None)
        embed.add_field(name='👥 Total Members', value=f'`{total}`',   inline=True)
        embed.add_field(name='🧑 Humans',        value=f'`{humans}`',  inline=True)
        embed.add_field(name='🤖 Bots',          value=f'`{bots}`',    inline=True)
        embed.add_field(name='🟢 Online',        value=f'`{online}`',  inline=True)
        embed.add_field(name='📜 Roles',         value=f'`{roles}`',   inline=True)
        embed.add_field(name='📁 Channels',      value=f'`{total_ch}` (💬 {text_ch} · 🔊 {voice_ch})', inline=True)
        embed.add_field(name='🎖️ Boost Level',   value=f'`Tier {guild.premium_tier}` ({guild.premium_subscription_count} boosts)', inline=True)
        embed.add_field(name='👑 Owner',         value=str(owner) if owner else 'Unknown', inline=True)
        embed.add_field(name='📅 Created',       value=f'<t:{created_ts}:D>', inline=True)
        embed.set_footer(text='Champions of the Shattered Realm · Server Info')
        embed.timestamp = discord.utils.utcnow()

        await interaction.followup.send(embed=embed)


async def setup(bot):
    await bot.add_cog(ServerInfoCog(bot))
