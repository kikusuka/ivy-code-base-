import discord
from discord.ext import commands, tasks
import aiohttp
import os
from utils.db import get_all_users, set_user
from utils.logger import log

PLACE_ID = int(os.getenv('ROBLOX_PLACE_ID', 4733278992))
UNIVERSE_ID = int(os.getenv('ROBLOX_UNIVERSE_ID', 1530882360))
BATCH_SIZE = 50  # Roblox API max per request


class PresenceTracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.check_presence.start()

    def cog_unload(self):
        self.check_presence.cancel()

    @tasks.loop(minutes=5)
    async def check_presence(self):
        guild_id = os.getenv('GUILD_ID')
        in_game_role_id = os.getenv('IN_GAME_ROLE_ID')
        if not guild_id or not in_game_role_id:
            return

        guild = self.bot.get_guild(int(guild_id))
        if not guild:
            return

        in_game_role = guild.get_role(int(in_game_role_id))
        if not in_game_role:
            return

        # Get all linked users
        all_users = get_all_users()
        linked = {
            uid: data for uid, data in all_users.items()
            if data.get('roblox_linked') and data.get('roblox_id')
        }

        if not linked:
            return

        roblox_ids = [data['roblox_id'] for data in linked.values()]
        discord_by_roblox = {data['roblox_id']: uid for uid, data in linked.items()}

        # Fetch presence in batches
        playing_roblox_ids = set()
        try:
            async with aiohttp.ClientSession() as session:
                for i in range(0, len(roblox_ids), BATCH_SIZE):
                    batch = roblox_ids[i:i + BATCH_SIZE]
                    async with session.post(
                        'https://presence.roblox.com/v1/presence/users',
                        json={'userIds': [int(rid) for rid in batch]},
                        headers={'Content-Type': 'application/json'}
                    ) as resp:
                        if resp.status != 200:
                            continue
                        data = await resp.json()
                        for entry in data.get('userPresences', []):
                            # placeId matches SBO:R and they're in-game
                            if (
                                entry.get('userPresenceType') == 2 and  # In game
                                entry.get('placeId') == PLACE_ID
                            ):
                                playing_roblox_ids.add(str(entry['userId']))
        except Exception as e:
            print(f'Presence check error: {e}')
            return

        # Update roles
        for roblox_id, discord_id in discord_by_roblox.items():
            try:
                member = guild.get_member(int(discord_id))
                if not member:
                    continue

                is_playing = roblox_id in playing_roblox_ids
                has_role = in_game_role in member.roles
                db_was_playing = linked[discord_id].get('in_game', False)

                if is_playing and not has_role:
                    await member.add_roles(in_game_role, reason='Playing SBO:R')
                    set_user(discord_id, {'in_game': True})
                    await log(self.bot, 'presence',
                        f'🎮 **{member}** started playing SBO:R — In Game role assigned'
                    )
                elif not is_playing and has_role:
                    await member.remove_roles(in_game_role, reason='Left SBO:R')
                    set_user(discord_id, {'in_game': False})
                    await log(self.bot, 'presence',
                        f'⏹️ **{member}** stopped playing SBO:R — In Game role removed'
                    )
            except Exception as e:
                print(f'Role update error for {discord_id}: {e}')

    @check_presence.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

    @discord.app_commands.command(name='whosplaying', description='See who\'s currently playing SBO:R')
    async def whos_playing(self, interaction: discord.Interaction):
        await interaction.response.defer()

        guild = interaction.guild
        in_game_role_id = os.getenv('IN_GAME_ROLE_ID')
        if not in_game_role_id:
            return await interaction.followup.send('❌ In Game role not configured.', ephemeral=True)

        role = guild.get_role(int(in_game_role_id))
        if not role:
            return await interaction.followup.send('❌ In Game role not found.', ephemeral=True)

        players = [m for m in guild.members if role in m.roles]

        embed = discord.Embed(
            title='⚔️ Currently Playing SBO:R',
            color=0x2d7dd2
        )

        if not players:
            embed.description = '*No one is playing right now. Be the first! 🎮*'
        else:
            all_users = get_all_users()
            lines = []
            for m in players:
                db = all_users.get(str(m.id), {})
                roblox_name = db.get('roblox_username', 'Unknown')
                lines.append(f'🟢 **{m.display_name}** (`{roblox_name}`)')
            embed.description = '\n'.join(lines)
            embed.set_footer(text=f'{len(players)} player(s) in-game · Updates every 5 minutes · CSR System · Powered by Breezy')

        embed.timestamp = discord.utils.utcnow()
        await interaction.followup.send(embed=embed)


async def setup(bot):
    await bot.add_cog(PresenceTracker(bot))
