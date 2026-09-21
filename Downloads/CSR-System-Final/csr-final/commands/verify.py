import discord
from discord.ext import commands
from discord import app_commands
import os
from utils.otp import generate_otp
from utils.db import get_user, set_user


class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label='Verify Me 🛡️', style=discord.ButtonStyle.primary, custom_id='start_verify')
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        db_user = get_user(str(user.id))
        if db_user and db_user.get('verified'):
            return await interaction.response.send_message('✅ You\'re already verified!', ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        code, expires_at = generate_otp(str(user.id))
        verify_url = f"{os.getenv('WEB_BASE_URL', 'http://localhost:3000')}/verify"

        embed = discord.Embed(
            title='🔐 Your Verification Code',
            description=(
                f'Your one-time verification code:\n\n'
                f'## `{code}`\n\n'
                f'**Steps:**\n'
                f'1. Click the button below to open the verification page\n'
                f'2. Enter the code above\n'
                f'3. Done! ✅\n\n'
                f'> ⏰ Expires in **2 minutes** · Single-use only — do not share'
            ),
            color=0x2d7dd2
        )
        embed.set_footer(text='Champions of the Shattered Realm · Verification')

        view = discord.ui.View()
        view.add_item(discord.ui.Button(label='Open Verification Page 🔗', url=verify_url, style=discord.ButtonStyle.link))

        try:
            await user.send(embed=embed, view=view)
            await interaction.followup.send(
                '📬 Check your DMs! Your verification code has been sent.\n> Code expires in **2 minutes**.',
                ephemeral=True
            )
        except discord.Forbidden:
            await interaction.followup.send(
                '❌ I couldn\'t DM you! Please enable **DMs from server members** in Privacy Settings, then try again.',
                ephemeral=True
            )


class VerifyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Register persistent view
        bot.add_view(VerifyView())

    # .verifysetup (prefix command, admin only)
    @commands.command(name='verifysetup')
    @commands.has_permissions(administrator=True)
    async def verifysetup(self, ctx):
        guild_name = os.getenv('GUILD_NAME', 'the Guild')
        embed = discord.Embed(
            title='🛡️ Guild Verification',
            description=(
                f'Welcome to **{guild_name}**!\n\n'
                f'To gain full access, you need to verify your identity.\n\n'
                f'**How it works:**\n'
                f'1. Click **Verify Me** below\n'
                f'2. The bot will DM you a 6-digit code\n'
                f'3. Enter the code on the verification page\n'
                f'4. Done! You\'ll get the Verified role instantly ✅\n\n'
                f'> ⏰ Codes expire in **2 minutes** · Make sure your DMs are open'
            ),
            color=0x2d7dd2
        )
        embed.set_footer(text='Champions of the Shattered Realm · Verification System')
        await ctx.send(embed=embed, view=VerifyView())
        await ctx.message.delete()

    @verifysetup.error
    async def verifysetup_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply('❌ Only admins can run `.verifysetup`.')

    # /getverified slash command
    @app_commands.command(name='getverified', description='Start verification — check your DMs for a code!')
    async def getverified(self, interaction: discord.Interaction):
        db_user = get_user(str(interaction.user.id))
        if db_user and db_user.get('verified'):
            return await interaction.response.send_message('✅ You\'re already verified!', ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        code, expires_at = generate_otp(str(interaction.user.id))
        verify_url = f"{os.getenv('WEB_BASE_URL', 'http://localhost:3000')}/verify"

        embed = discord.Embed(
            title='🔐 Your Verification Code',
            description=(
                f'Here is your one-time code:\n\n'
                f'## `{code}`\n\n'
                f'1. Click the button below to open the verification page\n'
                f'2. Enter the code above\n'
                f'3. Get verified!\n\n'
                f'> ⏰ Expires in **2 minutes** · Single-use only'
            ),
            color=0x2d7dd2
        )

        view = discord.ui.View()
        view.add_item(discord.ui.Button(label='Open Verification Page 🔗', url=verify_url, style=discord.ButtonStyle.link))

        try:
            await interaction.user.send(embed=embed, view=view)
            await interaction.followup.send(
                '📬 Check your DMs! Your code is on its way.\n> Code expires in **2 minutes**.',
                ephemeral=True
            )
        except discord.Forbidden:
            await interaction.followup.send(
                '❌ I couldn\'t DM you! Enable DMs from server members in Privacy Settings, then try again.',
                ephemeral=True
            )


async def setup(bot):
    await bot.add_cog(VerifyCog(bot))
