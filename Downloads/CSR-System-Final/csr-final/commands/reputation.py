import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone, timedelta
from utils.db import get_account, add_reputation, get_all_server_configs
from utils.logger import log
import sqlite3
from pathlib import Path

DB_PATH = Path('data/csr.db')


def get_last_rep_given(giver_id: str, receiver_id: str) -> str | None:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute('''
            SELECT given_at FROM rep_cooldowns
            WHERE giver_id = ? AND receiver_id = ?
        ''', (str(giver_id), str(receiver_id))).fetchone()
        return row[0] if row else None


def set_rep_cooldown(giver_id: str, receiver_id: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS rep_cooldowns (
                giver_id TEXT, receiver_id TEXT, given_at TEXT,
                PRIMARY KEY (giver_id, receiver_id)
            )
        ''')
        conn.execute('''
            INSERT OR REPLACE INTO rep_cooldowns (giver_id, receiver_id, given_at)
            VALUES (?, ?, ?)
        ''', (str(giver_id), str(receiver_id), datetime.now(timezone.utc).isoformat()))
        conn.commit()


def get_top_rep(limit: int = 10) -> list:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute('''
            SELECT discord_id, tag, reputation FROM accounts
            WHERE banned = 0
            ORDER BY reputation DESC LIMIT ?
        ''', (limit,)).fetchall()
        return [{'discord_id': r[0], 'tag': r[1], 'reputation': r[2]} for r in rows]


class ReputationCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='giverep', description='Give reputation to a member (once per day per person)')
    @app_commands.describe(member='Member to give rep to', reason='Why are you giving rep?')
    async def give_rep(self, interaction: discord.Interaction, member: discord.Member, reason: str = None):
        if member.id == interaction.user.id:
            return await interaction.response.send_message('❌ You can\'t give rep to yourself!', ephemeral=True)
        if member.bot:
            return await interaction.response.send_message('❌ Bots don\'t need rep 😂', ephemeral=True)

        giver = get_account(str(interaction.user.id))
        receiver = get_account(str(member.id))

        if not giver:
            return await interaction.response.send_message('❌ You need a CSR account first. Use `/signup`!', ephemeral=True)
        if not receiver:
            return await interaction.response.send_message('❌ That member doesn\'t have a CSR account yet.', ephemeral=True)

        # 24 hour cooldown per giver-receiver pair
        last = get_last_rep_given(str(interaction.user.id), str(member.id))
        if last:
            last_dt = datetime.fromisoformat(last)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            diff = datetime.now(timezone.utc) - last_dt
            if diff < timedelta(hours=24):
                remaining = timedelta(hours=24) - diff
                hours = int(remaining.total_seconds() // 3600)
                mins = int((remaining.total_seconds() % 3600) // 60)
                return await interaction.response.send_message(
                    f'⏰ You already gave rep to **{member.name}** today!\nTry again in **{hours}h {mins}m**.',
                    ephemeral=True
                )

        add_reputation(str(member.id), 1)
        set_rep_cooldown(str(interaction.user.id), str(member.id))

        await log(self.bot, str(interaction.guild.id), 'reputation',
                  f'⭐ **{interaction.user}** gave rep to **{member}**' + (f' — "{reason}"' if reason else ''))

        # Notify receiver
        try:
            notif = discord.Embed(
                description=(
                    f'⭐ **{interaction.user.display_name}** gave you reputation!\n'
                    + (f'> *"{reason}"*\n' if reason else '')
                    + f'\nYour total rep: **{receiver["reputation"] + 1}** ⭐'
                ),
                color=0x00b4d8
            )
            notif.set_footer(text='CSR System · Powered by Breezy')
            await member.send(embed=notif)
        except discord.Forbidden:
            pass

        embed = discord.Embed(
            description=f'⭐ Gave **+1 rep** to **{member.display_name}**!' + (f'\n> *"{reason}"*' if reason else ''),
            color=0x00b4d8
        )
        embed.set_footer(text='CSR System · Powered by Breezy')
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name='reputation', description='View reputation of a member')
    @app_commands.describe(member='Member to check (leave empty for yourself)')
    async def reputation(self, interaction: discord.Interaction, member: discord.Member = None):
        target = member or interaction.user
        account = get_account(str(target.id))
        if not account:
            return await interaction.response.send_message('❌ No CSR account found.', ephemeral=True)

        embed = discord.Embed(
            title=f'⭐ {target.display_name}\'s Reputation',
            description=f'**{account["reputation"]}** reputation points',
            color=0x00b4d8
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.set_footer(text='CSR System · Powered by Breezy')
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name='toprep', description='Top reputation leaderboard across all CSR servers')
    async def top_rep(self, interaction: discord.Interaction):
        await interaction.response.defer()
        top = get_top_rep(10)
        if not top:
            return await interaction.followup.send('No reputation data yet!')

        lines = []
        medals = ['🥇', '🥈', '🥉']
        for i, entry in enumerate(top):
            medal = medals[i] if i < 3 else f'`#{i+1}`'
            lines.append(f'{medal} **{entry["tag"]}** — ⭐ {entry["reputation"]}')

        embed = discord.Embed(
            title='🏆 CSR System — Reputation Leaderboard',
            description='\n'.join(lines),
            color=0x00b4d8
        )
        embed.set_footer(text='Cross-server · CSR System · Powered by Breezy')
        embed.timestamp = discord.utils.utcnow()
        await interaction.followup.send(embed=embed)


async def setup(bot):
    await bot.add_cog(ReputationCog(bot))
