import discord
from discord.ext import commands
from discord import app_commands
from utils.db import get_user


class ScoutCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='scout', description='Scout a member — profile, roles, and history.')
    @app_commands.describe(target='The member to scout')
    async def scout(self, interaction: discord.Interaction, target: discord.Member):
        await interaction.response.defer()

        db_user = get_user(str(target.id)) or {}
        guild_id   = db_user.get('guild_id', 'Not Registered')
        verified   = '✅ Verified' if db_user.get('verified') else '❌ Unverified'
        registered = (
            f"<t:{int(__import__('datetime').datetime.fromisoformat(db_user['registered_at']).timestamp())}:D>"
            if db_user.get('registered_at') else 'N/A'
        )

        # Roles
        roles = [r for r in target.roles if r.id != interaction.guild.id]
        roles.sort(key=lambda r: r.position, reverse=True)
        roles_str = ' '.join(r.mention for r in roles[:15]) or '*No roles*'

        # Role history (last 5)
        role_history = db_user.get('role_history', [])[-5:][::-1]
        role_history_str = '\n'.join(
            f"`{entry['changed_at'][:10]}` {entry['action']} {entry['role']}"
            for entry in role_history
        ) or '*No history tracked*'

        # Username history (last 5)
        username_history = db_user.get('username_history', [])[-5:][::-1]
        username_history_str = '\n'.join(
            f"`{entry['changed_at'][:10]}` → `{entry['name']}`"
            for entry in username_history
        ) or f'`{target}`'

        joined_ts = int(target.joined_at.timestamp()) if target.joined_at else None
        created_ts = int(target.created_at.timestamp())

        color = target.color if target.color != discord.Color.default() else discord.Color(0x2d7dd2)
        embed = discord.Embed(title=f'🔍 Scout: {target.name}', color=color)
        embed.set_thumbnail(url=target.display_avatar.url)

        embed.add_field(name='🆔 Discord Tag',      value=f'`{target}`',              inline=True)
        embed.add_field(name='🎖️ Guild ID',          value=f'`#{guild_id}`',           inline=True)
        embed.add_field(name='🔐 Status',            value=verified,                   inline=True)
        embed.add_field(name='📅 Joined Server',     value=f'<t:{joined_ts}:R>' if joined_ts else 'Unknown', inline=True)
        embed.add_field(name='🎂 Account Created',   value=f'<t:{created_ts}:D>',      inline=True)
        embed.add_field(name='📝 Registered',        value=registered,                 inline=True)
        embed.add_field(name=f'📜 Roles [{len(roles)}]', value=roles_str,              inline=False)
        embed.add_field(name='🔄 Role History (last 5)',  value=role_history_str,       inline=False)
        embed.add_field(name='📛 Username History',       value=username_history_str,   inline=False)
        embed.set_footer(text=f'Discord ID: {target.id}')
        embed.timestamp = discord.utils.utcnow()

        await interaction.followup.send(embed=embed)


async def setup(bot):
    await bot.add_cog(ScoutCog(bot))
