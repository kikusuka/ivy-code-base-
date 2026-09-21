import discord
from discord.ext import commands
import json
from utils.db import get_server_config, get_account, upsert_member_data, save_member_roles, track_role_change, update_account
from utils.logger import log


class OnMemberRemove(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if member.bot:
            return

        guild_id = str(member.guild.id)
        cfg = get_server_config(guild_id)
        if not cfg:
            return

        # Save roles before they leave
        role_ids = [str(r.id) for r in member.roles if r.id != member.guild.id]
        save_member_roles(guild_id, str(member.id), role_ids)
        upsert_member_data(guild_id, str(member.id), current_member=0)

        account = get_account(str(member.id))
        guild_name = cfg.get('guild_name', member.guild.name)

        # Goodbye DM
        embed = discord.Embed(
            title="💙 We're Sorry to See You Go...",
            description=(
                f'Hey **{member.name}**,\n\n'
                f'You left **{guild_name}** and we genuinely miss you already. 😔\n\n'
                f'If you\'re willing, we\'d love to know:\n\n'
                f'**› Why did you leave?**\n'
                f'**› What could we improve?**\n'
                f'**› Any suggestions?**\n\n'
                f'Just reply to this message — a real person reads these. 💙\n\n'
                + (f'Your CSR ID **`{account["csr_id"]}`** is still yours — come back anytime. ⚔️'
                   if account else 'You\'re always welcome back. ⚔️')
            ),
            color=0x0077b6
        )
        embed.set_footer(text='CSR System · Powered by Breezy')
        embed.timestamp = discord.utils.utcnow()

        try:
            await member.send(embed=embed)
        except discord.Forbidden:
            pass

        await log(self.bot, guild_id, 'leave', f'📤 **{member}** left the server')


class OnMemberUpdate(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        guild_id = str(after.guild.id)

        # Track role changes
        added = [r for r in after.roles if r not in before.roles and r.id != after.guild.id]
        removed = [r for r in before.roles if r not in after.roles and r.id != after.guild.id]

        for r in added:
            track_role_change(str(after.id), guild_id, '+', r.name)
        for r in removed:
            track_role_change(str(after.id), guild_id, '−', r.name)

        # Save updated roles
        if added or removed:
            role_ids = [str(r.id) for r in after.roles if r.id != after.guild.id]
            save_member_roles(guild_id, str(after.id), role_ids)

    @commands.Cog.listener()
    async def on_user_update(self, before: discord.User, after: discord.User):
        if str(before) != str(after):
            update_account(str(after.id), tag=str(after))


async def setup(bot):
    await bot.add_cog(OnMemberRemove(bot))
    await bot.add_cog(OnMemberUpdate(bot))
