import discord
from discord.ext import commands
from discord import app_commands
import os
from utils.db import get_guild_info, set_guild_info


class GuildInfoCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    info_group = app_commands.Group(name='info', description='Guild information commands')

    @info_group.command(name='view', description='View guild information')
    async def info_view(self, interaction: discord.Interaction):
        info = get_guild_info()
        sections = info.get('sections', {})
        guild_name = os.getenv('GUILD_NAME', interaction.guild.name)

        embed = discord.Embed(title=f'🏰 {guild_name}', color=0x2d7dd2)
        if interaction.guild.icon:
            embed.set_thumbnail(url=interaction.guild.icon.url)
        embed.set_footer(text='Champions of the Shattered Realm')
        embed.timestamp = discord.utils.utcnow()

        if not sections:
            embed.description = '*No guild info has been added yet. Admins can use `/info addabout` to add info.*'
        else:
            for name, content in sections.items():
                embed.add_field(name=name, value=content[:1024], inline=False)

        await interaction.response.send_message(embed=embed)

    @info_group.command(name='addabout', description='[Admin] Add or update a guild info section')
    @app_commands.describe(section='Section name (e.g. "About Us")', content='Content for this section')
    async def info_add(self, interaction: discord.Interaction, section: str, content: str):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message('❌ Only admins can add guild info.', ephemeral=True)
        info = get_guild_info()
        if 'sections' not in info:
            info['sections'] = {}
        info['sections'][section] = content
        set_guild_info(info)
        await interaction.response.send_message(f'✅ Guild info section **"{section}"** saved!', ephemeral=True)

    @info_group.command(name='remove', description='[Admin] Remove a guild info section')
    @app_commands.describe(section='Section name to remove')
    async def info_remove(self, interaction: discord.Interaction, section: str):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message('❌ Only admins can remove guild info.', ephemeral=True)
        info = get_guild_info()
        if section not in info.get('sections', {}):
            return await interaction.response.send_message(f'❌ Section **"{section}"** not found.', ephemeral=True)
        del info['sections'][section]
        set_guild_info(info)
        await interaction.response.send_message(f'🗑️ Section **"{section}"** removed.', ephemeral=True)


async def setup(bot):
    cog = GuildInfoCog(bot)
    bot.tree.add_command(cog.info_group)
    await bot.add_cog(cog)
