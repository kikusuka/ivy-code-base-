import discord
from discord.ext import commands
from discord import app_commands
import os


class AboutCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='about', description='About the CSR System bot')
    async def about(self, interaction: discord.Interaction):
        guild = interaction.guild
        total_members = guild.member_count

        embed = discord.Embed(
            title='⚙️ CSR System',
            description=(
                'The official guild management bot for **Champions of the Shattered Realm**.\n\n'
                'Built to give the guild its own identity — no external bots, no compromises.'
            ),
            color=0x2d7dd2
        )

        embed.add_field(
            name='🤖 What I do',
            value=(
                '› Member verification with OTP\n'
                '› Roblox account linking & presence tracking\n'
                '› SBO:R group update scraping\n'
                '› Moderation & logging\n'
                '› Guild events & announcements\n'
                '› Permanent guild IDs for every member'
            ),
            inline=False
        )

        embed.add_field(
            name='🎮 Game',
            value='[Sword Blox Online Rebirth](https://www.roblox.com/games/4733278992)',
            inline=True
        )
        embed.add_field(
            name='🌐 Community',
            value=f'[SBO:R Roblox Group](https://www.roblox.com/communities/{os.getenv("ROBLOX_GROUP_ID", "5683480")})',
            inline=True
        )
        embed.add_field(
            name='👥 Members',
            value=f'`{total_members}`',
            inline=True
        )

        embed.set_thumbnail(url=guild.icon.url if guild.icon else None)
        embed.set_footer(text='Powered by Breezy  ·  Built for Champions of the Shattered Realm')
        embed.timestamp = discord.utils.utcnow()

        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            label='⚔️ Play SBO:R',
            url=f'https://www.roblox.com/games/{os.getenv("ROBLOX_PLACE_ID", "4733278992")}',
            style=discord.ButtonStyle.link
        ))

        await interaction.response.send_message(embed=embed, view=view)


async def setup(bot):
    await bot.add_cog(AboutCog(bot))
