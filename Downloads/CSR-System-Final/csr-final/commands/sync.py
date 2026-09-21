import discord
from discord.ext import commands
from discord import app_commands
import os


class SyncCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='sync', description='[Admin] Re-register all slash commands')
    @app_commands.default_permissions(administrator=True)
    async def sync(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            guild = discord.Object(id=int(os.getenv('GUILD_ID')))
            self.bot.tree.copy_global_to(guild=guild)
            synced = await self.bot.tree.sync(guild=guild)
            await interaction.followup.send(f'✅ Synced **{len(synced)}** slash commands to this server!', ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f'❌ Sync failed: `{e}`', ephemeral=True)

    # Also available as prefix command: .sync
    @commands.command(name='sync')
    @commands.has_permissions(administrator=True)
    async def sync_prefix(self, ctx):
        try:
            guild = discord.Object(id=int(os.getenv('GUILD_ID')))
            self.bot.tree.copy_global_to(guild=guild)
            synced = await self.bot.tree.sync(guild=guild)
            await ctx.reply(f'✅ Synced **{len(synced)}** slash commands!')
        except Exception as e:
            await ctx.reply(f'❌ Sync failed: `{e}`')


async def setup(bot):
    await bot.add_cog(SyncCog(bot))
