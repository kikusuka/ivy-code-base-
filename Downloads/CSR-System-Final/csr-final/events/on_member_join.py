import discord
from discord.ext import commands
import asyncio
from utils.db import get_server_config, get_account, upsert_member_data, SERVER_TYPES
from utils.logger import log


class OnMemberJoin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return

        guild_id = str(member.guild.id)
        cfg = get_server_config(guild_id)
        if not cfg or not cfg.get('setup_complete'):
            return

        server_type = cfg.get('server_type', '')
        info = SERVER_TYPES.get(server_type, {})
        guild_name = cfg.get('guild_name', member.guild.name)
        game_name = info.get('name', 'the game')

        # Save member join
        upsert_member_data(guild_id, str(member.id), current_member=1)

        # Assign unverified role
        unverified_id = cfg.get('unverified_role_id')
        if unverified_id:
            role = member.guild.get_role(int(unverified_id))
            if role:
                try:
                    await member.add_roles(role, reason='New member')
                except Exception as e:
                    print(f'Unverified role error: {e}')

        # Check if they have a CSR account — if so, hint at login
        account = get_account(str(member.id))

        # Welcome DM
        welcome_embed = discord.Embed(
            title=f'⚔️ Welcome to {guild_name}!',
            description=(
                f'Hey **{member.name}**, glad you\'re here! 🎉\n\n'
                f'**{guild_name}** is a community of champions.\n\n'
                + (f'🎮 We play **{game_name}** together\n\n' if game_name else '')
                + ('🪪 **You have a CSR account!**\n'
                   f'Use `/login` to restore your roles instantly.\n\n'
                   if account else
                   '🪪 **New here?**\n'
                   'Get verified first, then use `/signup` to create your CSR account — it works across all our servers!\n\n')
                + '*See you on the battlefield! ⚔️*'
            ),
            color=0x00b4d8
        )
        welcome_embed.set_thumbnail(url=member.guild.icon.url if member.guild.icon else None)
        welcome_embed.set_footer(text='CSR System · Powered by Breezy')

        verify_embed = discord.Embed(
            title='🔐 Step 1 — Verify Your Identity',
            description=(
                f'To unlock all channels in **{guild_name}**:\n\n'
                f'1. Find the **#verification** channel\n'
                f'2. Click **Verify Me 🛡️**\n'
                f'3. Enter the 6-digit code I\'ll DM you\n'
                f'4. Done! ✅\n\n'
                f'> Codes expire in **2 minutes** — have the page open first!'
            ),
            color=0x0077b6
        )
        verify_embed.set_footer(text='CSR System · Powered by Breezy')

        try:
            await member.send(embed=welcome_embed)
            await asyncio.sleep(3)
            await member.send(embed=verify_embed)
        except discord.Forbidden:
            pass

        # Log join
        await log(self.bot, guild_id, 'join', f'👋 **{member}** joined the server')

        # Welcome channel message
        welcome_ch_id = cfg.get('welcome_channel_id')
        if welcome_ch_id:
            ch = member.guild.get_channel(int(welcome_ch_id))
            if ch:
                try:
                    embed = discord.Embed(
                        description=f'👋 Welcome **{member.mention}** to **{guild_name}**!',
                        color=0x00b4d8
                    )
                    embed.set_footer(text='CSR System · Powered by Breezy')
                    await ch.send(embed=embed)
                except Exception:
                    pass

        # Cross-server shoutouts
        await self._cross_server_shoutout(member, guild_id, guild_name)

    async def _cross_server_shoutout(self, member: discord.Member, joined_guild_id: str, joined_guild_name: str):
        from utils.db import check_shoutout_cooldown, set_shoutout_cooldown, get_all_server_configs
        if not check_shoutout_cooldown(str(member.id), joined_guild_id):
            return
        set_shoutout_cooldown(str(member.id), joined_guild_id)

        configs = get_all_server_configs()
        for cfg in configs:
            if cfg['guild_id'] == joined_guild_id:
                continue
            guild = self.bot.get_guild(int(cfg['guild_id']))
            if not guild:
                continue
            if not guild.get_member(member.id):
                continue
            welcome_ch_id = cfg.get('welcome_channel_id')
            if not welcome_ch_id:
                continue
            ch = guild.get_channel(int(welcome_ch_id))
            if not ch:
                continue
            try:
                embed = discord.Embed(
                    description=f'🌐 **{member.display_name}** just joined **{joined_guild_name}**! Another champion on the field ⚔️',
                    color=0x00b4d8
                )
                embed.set_footer(text='CSR System · Cross-Server · Powered by Breezy')
                await ch.send(embed=embed)
            except Exception:
                pass


async def setup(bot):
    await bot.add_cog(OnMemberJoin(bot))
