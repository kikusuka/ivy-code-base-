import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional
import asyncio
import os
from utils.db import get_events, add_event


class EventCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    event_group = app_commands.Group(
        name='event',
        description='Guild event management',
        default_permissions=discord.Permissions(manage_events=True)
    )

    @event_group.command(name='list', description='View upcoming guild events')
    async def event_list(self, interaction: discord.Interaction):
        events = get_events()
        if not events:
            return await interaction.response.send_message('📅 No events scheduled yet.', ephemeral=True)

        embed = discord.Embed(title='📅 Upcoming Guild Events', color=0x2d7dd2)
        embed.set_footer(text='Champions of the Shattered Realm')
        embed.timestamp = discord.utils.utcnow()

        for e in events[-10:]:
            embed.add_field(
                name=f"🎉 {e['name']}",
                value=f"📅 **{e['date']}**{chr(10) + e['content'] if e.get('content') else ''}\n🔔 Notified: {e['notify']}",
                inline=False
            )
        await interaction.response.send_message(embed=embed)

    @event_group.command(name='create', description='Create an event and send DM reminders')
    @app_commands.describe(
        name='Event name',
        date='Event date & time (e.g. "July 20 at 8pm UTC")',
        content='Optional event description',
        notify='Who to notify',
        role='Specific role to notify (if "role" chosen)',
        ping='Also ping in announcements channel? (default: True)'
    )
    @app_commands.choices(notify=[
        app_commands.Choice(name='@everyone', value='everyone'),
        app_commands.Choice(name='Specific Role', value='role'),
    ])
    async def event_create(
        self,
        interaction: discord.Interaction,
        name: str,
        date: str,
        notify: str,
        content: Optional[str] = None,
        role: Optional[discord.Role] = None,
        ping: Optional[bool] = True,
    ):
        if notify == 'role' and not role:
            return await interaction.response.send_message(
                '❌ You chose "Specific Role" but didn\'t select a role. Run the command again.', ephemeral=True
            )

        await interaction.response.defer(ephemeral=True)

        # Save event
        event_data = {
            'name': name, 'date': date, 'content': content or '',
            'notify': role.name if notify == 'role' else '@everyone',
            'created_by': str(interaction.user),
        }
        add_event(event_data)

        guild = interaction.guild
        guild_name = os.getenv('GUILD_NAME', guild.name)

        dm_embed = discord.Embed(
            title=f'📅 Upcoming Event: {name}',
            description=(
                f'Hey! There\'s a new event in **{guild_name}** — don\'t miss it!\n\n'
                f'**📆 When:** {date}\n'
                + (f'**📝 Details:** {content}\n' if content else '')
                + '\nSee you there! 🎉'
            ),
            color=0x2d7dd2
        )
        dm_embed.set_footer(text='Champions of the Shattered Realm · Event Reminder')
        dm_embed.timestamp = discord.utils.utcnow()

        # Determine targets
        await guild.chunk()
        targets = [m for m in guild.members if not m.bot]
        if notify == 'role' and role:
            targets = [m for m in targets if role in m.roles]

        await interaction.followup.send(
            f'📤 Sending DMs to **{len(targets)}** member(s)... this may take a moment.',
            ephemeral=True
        )

        sent = failed = 0
        for member in targets:
            try:
                await member.send(embed=dm_embed)
                sent += 1
                await asyncio.sleep(0.3)  # rate-limit friendly
            except Exception:
                failed += 1

        # Optional channel ping
        if ping and os.getenv('WELCOME_CHANNEL_ID'):
            ch = guild.get_channel(int(os.getenv('WELCOME_CHANNEL_ID')))
            if ch:
                ping_str = '@everyone' if notify == 'everyone' else role.mention
                try:
                    await ch.send(
                        content=f'{ping_str} 📅 **New Event: {name}** — {date}' + (f' · {content}' if content else ''),
                        embed=dm_embed,
                        allowed_mentions=discord.AllowedMentions(everyone=True, roles=True)
                    )
                except Exception:
                    pass

        await interaction.followup.send(
            f'✅ Event created! DMs sent: **{sent}** · Failed (DMs closed): **{failed}**',
            ephemeral=True
        )


async def setup(bot):
    cog = EventCog(bot)
    bot.tree.add_command(cog.event_group)
    await bot.add_cog(cog)
