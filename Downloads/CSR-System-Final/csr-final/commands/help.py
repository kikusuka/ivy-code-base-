import discord
from discord.ext import commands
from discord import app_commands


USER_COMMANDS = [
    ('/getverified',       'Start verification — get your OTP via DM'),
    ('/info view',         'View guild information'),
    ('/serverinfo',        'Server stats — members, bots, channels, etc.'),
    ('/scout @user',       "Scout a member's profile, roles & history"),
    ('/event list',        'See upcoming guild events'),
    ('/help',              'Shows this help message'),
]

ADMIN_COMMANDS = [
    ('.verifysetup',            'Post the verification panel in current channel'),
    ('/info addabout',          'Add or update a guild info section'),
    ('/info remove',            'Remove a guild info section'),
    ('/mod ban @user',          'Ban a member'),
    ('/mod kick @user',         'Kick a member'),
    ('/mod mute @user',         'Timeout (mute) a member'),
    ('/mod unmute @user',       'Remove timeout'),
    ('/mod warn @user',         'Warn a member via DM'),
    ('/mod purge [amount]',     'Bulk delete messages'),
    ('/event create',           'Create an event & send DM reminders'),
    ('/sync',                   'Re-register slash commands'),
    ('/help admin',             'Shows this admin reference'),
]


class HelpCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    help_group = app_commands.Group(name='help', description='Bot command reference')

    @help_group.command(name='user', description='Show all user commands')
    async def help_user(self, interaction: discord.Interaction):
        lines = '\n\n'.join(f'`{cmd}`\n└ {desc}' for cmd, desc in USER_COMMANDS)
        is_admin = interaction.user.guild_permissions.administrator
        embed = discord.Embed(title='📖 Bot Commands', description=lines, color=0x2d7dd2)
        if is_admin:
            embed.set_footer(text='Tip: Use /help admin to see admin commands')
        else:
            embed.set_footer(text='Champions of the Shattered Realm')
        embed.timestamp = discord.utils.utcnow()
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @help_group.command(name='admin', description='[Admin] Show all admin commands')
    async def help_admin(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message('❌ Admin commands are for admins only.', ephemeral=True)
        lines = '\n\n'.join(f'`{cmd}`\n└ {desc}' for cmd, desc in ADMIN_COMMANDS)
        embed = discord.Embed(title='⚙️ Admin Command Reference', description=lines, color=0x2d7dd2)
        embed.set_footer(text='Champions of the Shattered Realm · Admin Help')
        embed.timestamp = discord.utils.utcnow()
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    cog = HelpCog(bot)
    bot.tree.add_command(cog.help_group)
    await bot.add_cog(cog)
