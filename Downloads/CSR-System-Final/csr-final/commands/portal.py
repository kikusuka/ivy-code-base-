import discord
from discord.ext import commands
from discord import app_commands
import os

PLACE_ID = os.getenv('ROBLOX_PLACE_ID', '4733278992')
GROUP_ID = os.getenv('ROBLOX_GROUP_ID', '5683480')


class PortalCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def portal_embed(self):
        embed = discord.Embed(
            title='⚔️ Enter the Realm',
            description=(
                '**Sword Blox Online Rebirth** — the battlefield awaits.\n\n'
                '🗡️ Jump in and fight alongside your guild members\n'
                '🏆 Climb the ranks with your Champions\n'
                '🌐 Join the official SBO:R Roblox community\n\n'
                '*See who\'s online with `/whosplaying` · Link your account with `/linkroblox`*'
            ),
            color=0x2d7dd2
        )
        embed.set_image(url='https://tr.rbxcdn.com/180DAY-7f9b4d3e2a1c6f8b5d4e3a2c1f6b9d8e/768/432/Image/Webp/noFilter')
        embed.set_footer(text='CSR System · Powered by Breezy · Champions of the Shattered Realm')
        embed.timestamp = discord.utils.utcnow()
        return embed

    def portal_view(self):
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(
            label='⚔️ Play SBO:R Now',
            url=f'https://www.roblox.com/games/{PLACE_ID}',
            style=discord.ButtonStyle.link,
            row=0
        ))
        view.add_item(discord.ui.Button(
            label='🌐 Join SBO:R Group',
            url=f'https://www.roblox.com/communities/{GROUP_ID}',
            style=discord.ButtonStyle.link,
            row=0
        ))
        return view

    @commands.command(name='portalsetup')
    @commands.has_permissions(administrator=True)
    async def portal_setup(self, ctx):
        """Prefix command: .portalsetup — posts the portal embed in current channel."""
        await ctx.send(embed=self.portal_embed(), view=self.portal_view())
        await ctx.message.delete()

    @app_commands.command(name='portal', description='Show the SBO:R game portal')
    async def portal(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=self.portal_embed(), view=self.portal_view())


async def setup(bot):
    await bot.add_cog(PortalCog(bot))
