import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiohttp
import os
from utils.db import get_all_server_configs, get_account, update_account, get_server_config
from utils.logger import log

STEAM_API = 'https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/'
WARFRAME_APP_ID = 230410


class WarframeTracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.check_presence.start()

    def cog_unload(self):
        self.check_presence.cancel()

    @tasks.loop(minutes=5)
    async def check_presence(self):
        configs = get_all_server_configs()
        warframe_guilds = [c for c in configs if c.get('server_type') == 'warframe']
        if not warframe_guilds:
            return

        steam_key = os.getenv('STEAM_API_KEY')

        for cfg in warframe_guilds:
            guild = self.bot.get_guild(int(cfg['guild_id']))
            if not guild:
                continue

            in_game_role_id = cfg.get('in_game_role_id')
            if not in_game_role_id:
                continue

            in_game_role = guild.get_role(int(in_game_role_id))
            if not in_game_role:
                continue

            await guild.chunk()

            for member in guild.members:
                if member.bot:
                    continue

                account = get_account(str(member.id))
                if not account:
                    continue

                is_playing = False

                # Method 1: Discord Rich Presence (zero API calls)
                for activity in member.activities:
                    if isinstance(activity, discord.Activity) and 'warframe' in (activity.name or '').lower():
                        is_playing = True
                        break
                    if isinstance(activity, discord.Game) and 'warframe' in (activity.name or '').lower():
                        is_playing = True
                        break

                # Method 2: Steam API fallback
                if not is_playing and steam_key and account.get('steam_id'):
                    try:
                        async with aiohttp.ClientSession() as session:
                            async with session.get(
                                STEAM_API,
                                params={'key': steam_key, 'steamids': account['steam_id']},
                                timeout=aiohttp.ClientTimeout(total=8)
                            ) as resp:
                                if resp.status == 200:
                                    data = await resp.json()
                                    players = data.get('response', {}).get('players', [])
                                    if players:
                                        p = players[0]
                                        if p.get('gameid') == str(WARFRAME_APP_ID):
                                            is_playing = True
                    except Exception as e:
                        print(f'Steam API error: {e}')

                has_role = in_game_role in member.roles
                was_playing = bool(account.get('in_game'))

                if is_playing and not has_role:
                    await member.add_roles(in_game_role, reason='Playing Warframe')
                    update_account(str(member.id), in_game=1)
                    await log(self.bot, cfg['guild_id'], 'presence',
                              f'🎮 **{member}** started playing Warframe')
                elif not is_playing and has_role:
                    await member.remove_roles(in_game_role, reason='Left Warframe')
                    update_account(str(member.id), in_game=0)
                    await log(self.bot, cfg['guild_id'], 'presence',
                              f'⏹️ **{member}** stopped playing Warframe')

    @check_presence.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name='linksteam', description='Link your Steam account for Warframe tracking')
    @app_commands.describe(steam_id='Your Steam ID64 (found at steamid.io)')
    async def link_steam(self, interaction: discord.Interaction, steam_id: str):
        cfg = get_server_config(str(interaction.guild.id))
        if not cfg or cfg.get('server_type') != 'warframe':
            return await interaction.response.send_message('❌ This command is only for Warframe servers.', ephemeral=True)

        account = get_account(str(interaction.user.id))
        if not account or not account.get('verified'):
            return await interaction.response.send_message('❌ You need to be verified first.', ephemeral=True)

        if account.get('steam_linked'):
            return await interaction.response.send_message(
                f'✅ You already have Steam `{account["steam_username"]}` linked!\n> Use `/unlinksteam` to change it.',
                ephemeral=True
            )

        await interaction.response.defer(ephemeral=True)

        # Validate Steam ID
        steam_key = os.getenv('STEAM_API_KEY')
        if steam_key:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        STEAM_API,
                        params={'key': steam_key, 'steamids': steam_id},
                        timeout=aiohttp.ClientTimeout(total=8)
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            players = data.get('response', {}).get('players', [])
                            if not players:
                                return await interaction.followup.send('❌ Steam ID not found. Check steamid.io for your ID64.', ephemeral=True)
                            username = players[0].get('personaname', 'Unknown')
                        else:
                            return await interaction.followup.send('❌ Could not validate Steam ID.', ephemeral=True)
            except Exception:
                return await interaction.followup.send('❌ Steam API error. Try again.', ephemeral=True)
        else:
            username = steam_id  # No key, just save the ID

        update_account(str(interaction.user.id), steam_id=steam_id, steam_username=username, steam_linked=1)

        await log(self.bot, str(interaction.guild.id), 'steam',
                  f'🔗 **{interaction.user}** linked Steam `{username}`')

        embed = discord.Embed(
            title='🔗 Steam Account Linked!',
            description=(
                f'**{username}** is now linked!\n\n'
                f'You\'ll get the **In Game 🎮** role when playing Warframe.\n\n'
                f'**Tip:** Enable Discord Rich Presence in Warframe settings for faster detection:\n'
                f'> Settings → Interface → Enable Discord Integration ✅'
            ),
            color=0x00b4d8
        )
        embed.set_footer(text='CSR System · Powered by Breezy')
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name='unlinksteam', description='Unlink your Steam account')
    async def unlink_steam(self, interaction: discord.Interaction):
        account = get_account(str(interaction.user.id))
        if not account or not account.get('steam_linked'):
            return await interaction.response.send_message('❌ No Steam account linked.', ephemeral=True)
        username = account.get('steam_username', 'Unknown')
        update_account(str(interaction.user.id), steam_id=None, steam_username=None, steam_linked=0, in_game=0)
        cfg = get_server_config(str(interaction.guild.id))
        if cfg:
            in_game_id = cfg.get('in_game_role_id')
            if in_game_id:
                role = interaction.guild.get_role(int(in_game_id))
                if role and role in interaction.user.roles:
                    await interaction.user.remove_roles(role, reason='Steam unlinked')
        await interaction.response.send_message(f'✅ Steam account **{username}** unlinked.', ephemeral=True)


async def setup(bot):
    await bot.add_cog(WarframeTracker(bot))
