import discord
from discord.ext import commands, tasks
import os
import json
from utils.db_restore import startup_health_check
from utils.db import (
    set_bot, get_pending_actions, mark_action_done,
    increment_action_retry, clean_expired_otps,
    get_server_config, get_all_server_configs, SERVER_TYPES
)


class OnReady(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        set_bot(self.bot)
        await startup_health_check(self.bot)
        print(f'\n✅ Logged in as {self.bot.user} ({self.bot.user.id})')
        print(f'📡 Connected to {len(self.bot.guilds)} server(s)')

        # Set presence
        await self.bot.change_presence(
            activity=discord.Game(name='⚔️ CSR System · Powered by Breezy'),
            status=discord.Status.online
        )

        # Auto sync commands
        try:
            for guild in self.bot.guilds:
                g = discord.Object(id=guild.id)
                self.bot.tree.copy_global_to(guild=g)
            synced = await self.bot.tree.sync()
            print(f'✅ Synced {len(synced)} global slash commands')
        except Exception as e:
            print(f'❌ Sync failed: {e}')

        # Health report
        print('\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
        print('  CSR SYSTEM HEALTH REPORT')
        print('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
        configs = get_all_server_configs()
        for cfg in configs:
            guild = self.bot.get_guild(int(cfg['guild_id']))
            name = guild.name if guild else f"Unknown ({cfg['guild_id']})"
            stype = cfg.get('server_type', 'unknown')
            info = SERVER_TYPES.get(stype, {})
            print(f'  ✅ {name} → {info.get("name", stype)}')
        if not configs:
            print('  ⚠️  No servers configured yet')
        print('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

        # Start background tasks
        self.retry_pending.start()
        self.cleanup_otps.start()

    @tasks.loop(minutes=2)
    async def retry_pending(self):
        """Retry failed role assignments and other pending actions."""
        actions = get_pending_actions()
        for action in actions:
            try:
                guild = self.bot.get_guild(int(action['guild_id']))
                if not guild:
                    increment_action_retry(action['id'])
                    continue

                member = guild.get_member(int(action['discord_id']))
                if not member:
                    increment_action_retry(action['id'])
                    continue

                payload = json.loads(action.get('payload', '{}'))

                if action['action'] == 'add_role':
                    role = guild.get_role(int(payload['role_id']))
                    if role:
                        await member.add_roles(role, reason='CSR pending action retry')
                        mark_action_done(action['id'])

                elif action['action'] == 'remove_role':
                    role = guild.get_role(int(payload['role_id']))
                    if role:
                        await member.remove_roles(role, reason='CSR pending action retry')
                        mark_action_done(action['id'])

                elif action['action'] == 'set_verified':
                    cfg = get_server_config(str(guild.id))
                    unverified_id = cfg.get('unverified_role_id')
                    verified_id = cfg.get('verified_role_id')
                    if unverified_id:
                        role = guild.get_role(int(unverified_id))
                        if role:
                            await member.remove_roles(role, reason='Verified (retry)')
                    if verified_id:
                        role = guild.get_role(int(verified_id))
                        if role:
                            await member.add_roles(role, reason='Verified (retry)')
                    mark_action_done(action['id'])

            except Exception as e:
                print(f'Pending action retry error: {e}')
                increment_action_retry(action['id'])

    @tasks.loop(minutes=10)
    async def cleanup_otps(self):
        clean_expired_otps()

    @retry_pending.before_loop
    @cleanup_otps.before_loop
    async def before_tasks(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(OnReady(bot))
