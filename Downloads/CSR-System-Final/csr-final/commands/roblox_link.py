import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
from utils.db import get_user, set_user
from utils.logger import log

TERMS = """
**📋 CSR System — Roblox Link Terms & Conditions**
*Powered by Breezy*

By linking your Roblox account you agree to the following:

**What we collect:**
› Your Roblox User ID and username
› Your in-game presence (whether you're playing SBO:R or not)

**What we do with it:**
› Show an "In Game 🎮" role when you're actively playing Sword Blox Online Rebirth
› Let guild members see who's online in-game
› Improve your overall guild experience

**What we DON'T do:**
› We do NOT sell, share, or trade your data with anyone
› We do NOT track anything outside of SBO:R game presence
› We do NOT store any personal information beyond your Roblox ID

**Your rights:**
› You can unlink your account anytime with `/unlinkroblox`
› Unlinking removes all stored Roblox data immediately
› This is completely optional — it only unlocks the In Game role feature

*By clicking Accept you confirm you have read and agree to these terms.*
"""


class RobloxLinkView(discord.ui.View):
    def __init__(self, roblox_id: str, roblox_username: str, user_id: str):
        super().__init__(timeout=120)
        self.roblox_id = roblox_id
        self.roblox_username = roblox_username
        self.user_id = user_id

    @discord.ui.button(label='✅ Accept & Link', style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.user_id:
            return await interaction.response.send_message('❌ This isn\'t your linking session.', ephemeral=True)

        set_user(self.user_id, {
            'roblox_id': self.roblox_id,
            'roblox_username': self.roblox_username,
            'roblox_linked': True,
        })

        await log(interaction.client, 'roblox_link',
            f'🔗 **{interaction.user}** linked Roblox account **{self.roblox_username}** (`{self.roblox_id}`)'
        )

        embed = discord.Embed(
            title='🔗 Roblox Account Linked!',
            description=(
                f'**{self.roblox_username}** is now linked to your Discord account.\n\n'
                f'You\'ll automatically receive the **In Game 🎮** role when you\'re playing SBO:R!\n\n'
                f'> Use `/unlinkroblox` anytime to remove the link.'
            ),
            color=0x2d7dd2
        )
        embed.set_footer(text='CSR System · Powered by Breezy')

        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label='❌ Decline', style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.user_id:
            return await interaction.response.send_message('❌ This isn\'t your linking session.', ephemeral=True)
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content='❌ Roblox linking cancelled. You can link anytime with `/linkroblox`.',
            embed=None, view=self
        )


class RobloxLinkCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def fetch_roblox_user(self, roblox_id: str):
        """Fetch Roblox username from user ID."""
        url = f'https://users.roblox.com/v1/users/{roblox_id}'
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get('name'), data.get('displayName')
                return None, None

    @app_commands.command(name='linkroblox', description='Link your Roblox account to unlock In Game tracking')
    @app_commands.describe(roblox_id='Your Roblox User ID (found in your Roblox profile URL)')
    async def link_roblox(self, interaction: discord.Interaction, roblox_id: str):
        await interaction.response.defer(ephemeral=True)

        # Must be verified first
        db_user = get_user(str(interaction.user.id))
        if not db_user or not db_user.get('verified'):
            return await interaction.followup.send(
                '❌ You need to be **verified** first before linking your Roblox account.\n'
                '> Use `/getverified` to start verification.',
                ephemeral=True
            )

        # Already linked?
        if db_user.get('roblox_linked'):
            return await interaction.followup.send(
                f'✅ You already have **{db_user.get("roblox_username")}** linked!\n'
                f'> Use `/unlinkroblox` first if you want to change it.',
                ephemeral=True
            )

        # Validate Roblox ID
        if not roblox_id.isdigit():
            return await interaction.followup.send(
                '❌ That doesn\'t look like a valid Roblox User ID. It should be a number.\n'
                '> Find it in your Roblox profile URL: `roblox.com/users/YOUR_ID/profile`',
                ephemeral=True
            )

        username, display_name = await self.fetch_roblox_user(roblox_id)
        if not username:
            return await interaction.followup.send(
                '❌ Couldn\'t find a Roblox account with that ID. Double-check and try again.',
                ephemeral=True
            )

        # Show T&C with user info preview
        embed = discord.Embed(
            title='🔗 Link Roblox Account',
            description=(
                f'**Found:** `{username}`' + (f' ({display_name})' if display_name != username else '') + '\n\n'
                + TERMS
            ),
            color=0x2d7dd2
        )
        embed.set_footer(text='CSR System · Powered by Breezy · You have 2 minutes to decide')

        view = RobloxLinkView(roblox_id, username, str(interaction.user.id))
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name='unlinkroblox', description='Unlink your Roblox account')
    async def unlink_roblox(self, interaction: discord.Interaction):
        db_user = get_user(str(interaction.user.id))
        if not db_user or not db_user.get('roblox_linked'):
            return await interaction.response.send_message('❌ You don\'t have a Roblox account linked.', ephemeral=True)

        username = db_user.get('roblox_username', 'Unknown')
        set_user(str(interaction.user.id), {
            'roblox_id': None,
            'roblox_username': None,
            'roblox_linked': False,
        })

        # Remove In Game role if they have it
        import os
        in_game_role_id = os.getenv('IN_GAME_ROLE_ID')
        if in_game_role_id:
            role = interaction.guild.get_role(int(in_game_role_id))
            if role and role in interaction.user.roles:
                await interaction.user.remove_roles(role, reason='Roblox unlinked')

        await log(interaction.client, 'roblox_unlink',
            f'🔓 **{interaction.user}** unlinked Roblox account **{username}**'
        )

        await interaction.response.send_message(
            f'✅ **{username}** has been unlinked. Your data has been removed.',
            ephemeral=True
        )

    @app_commands.command(name='robloxprofile', description='View your or another member\'s linked Roblox profile')
    @app_commands.describe(member='Member to check (leave empty for yourself)')
    async def roblox_profile(self, interaction: discord.Interaction, member: discord.Member = None):
        target = member or interaction.user
        db_user = get_user(str(target.id))

        if not db_user or not db_user.get('roblox_linked'):
            name = 'You don\'t' if target == interaction.user else f'**{target.name}** doesn\'t'
            return await interaction.response.send_message(
                f'❌ {name} have a Roblox account linked.',
                ephemeral=True
            )

        roblox_id = db_user.get('roblox_id')
        username = db_user.get('roblox_username')

        embed = discord.Embed(
            title=f'🎮 {target.name}\'s Roblox Profile',
            color=0x2d7dd2
        )
        embed.add_field(name='Roblox Username', value=f'`{username}`', inline=True)
        embed.add_field(name='Roblox ID',       value=f'`{roblox_id}`', inline=True)
        embed.add_field(name='In Game?',        value='🟢 Yes' if db_user.get('in_game') else '⚫ No', inline=True)

        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            label='View Roblox Profile 🔗',
            url=f'https://www.roblox.com/users/{roblox_id}/profile',
            style=discord.ButtonStyle.link
        ))
        embed.set_footer(text='CSR System · Powered by Breezy')

        await interaction.response.send_message(embed=embed, ephemeral=(member is None))


async def setup(bot):
    await bot.add_cog(RobloxLinkCog(bot))
