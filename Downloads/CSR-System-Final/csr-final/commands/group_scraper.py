import discord
from discord.ext import commands, tasks
import aiohttp
import os
import json
from datetime import datetime, timezone
from utils.logger import log

GROUP_ID = os.getenv('ROBLOX_GROUP_ID', '5683480')
GROQ_API_KEY = os.getenv('GROQ_API_KEY')
GROQ_MODEL = os.getenv('GROQ_MODEL', 'llama3-8b-8192')

# Track last seen post ID to avoid reposting
_last_post_id = None


async def phrase_with_groq(raw_text: str, title: str) -> str:
    """Use Groq to rephrase a raw Roblox group update into guild-friendly language."""
    if not GROQ_API_KEY:
        return raw_text

    prompt = (
        f'You are the CSR System bot for the SBO:R Discord guild (Sword Blox Online Rebirth on Roblox). '
        f'Rephrase the following Roblox group update into a short, hype, guild-friendly announcement. '
        f'Keep it under 3 sentences. Sound excited but not cringe. Don\'t use hashtags. '
        f'End with something like "drop your thoughts below 💬" or "let\'s discuss ⚔️".\n\n'
        f'Title: {title}\n'
        f'Update: {raw_text}'
    )

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                'https://api.groq.com/openai/v1/chat/completions',
                headers={
                    'Authorization': f'Bearer {GROQ_API_KEY}',
                    'Content-Type': 'application/json',
                },
                json={
                    'model': GROQ_MODEL,
                    'messages': [{'role': 'user', 'content': prompt}],
                    'max_tokens': 200,
                    'temperature': 0.7,
                }
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data['choices'][0]['message']['content'].strip()
    except Exception as e:
        print(f'Groq error: {e}')

    return raw_text  # fallback to raw text


async def fetch_group_wall(session: aiohttp.ClientSession) -> list:
    """Fetch latest posts from the Roblox group wall."""
    url = f'https://groups.roblox.com/v2/groups/{GROUP_ID}/wall/posts?limit=5&sortOrder=Desc'
    try:
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get('data', [])
    except Exception as e:
        print(f'Group wall fetch error: {e}')
    return []


async def fetch_group_shout(session: aiohttp.ClientSession) -> dict | None:
    """Fetch the current group shout (announcement)."""
    url = f'https://groups.roblox.com/v1/groups/{GROUP_ID}'
    try:
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get('shout')
    except Exception as e:
        print(f'Group shout fetch error: {e}')
    return None


class GroupScraper(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.last_wall_id = None
        self.last_shout_body = None
        self.scrape_loop.start()

    def cog_unload(self):
        self.scrape_loop.cancel()

    @tasks.loop(hours=1)
    async def scrape_loop(self):
        updates_channel_id = os.getenv('UPDATES_CHANNEL_ID')
        if not updates_channel_id:
            return

        guild_id = os.getenv('GUILD_ID')
        guild = self.bot.get_guild(int(guild_id)) if guild_id else None
        if not guild:
            return

        channel = guild.get_channel(int(updates_channel_id))
        if not channel:
            return

        async with aiohttp.ClientSession() as session:
            # Check group shout (main announcement)
            shout = await fetch_group_shout(session)
            if shout and shout.get('body'):
                shout_body = shout['body']
                if shout_body != self.last_shout_body:
                    self.last_shout_body = shout_body
                    phrased = await phrase_with_groq(shout_body, 'Group Announcement')
                    await self.post_update(channel, '📢 SBO:R Announcement', phrased, shout_body)

            # Check group wall for new posts
            posts = await fetch_group_wall(session)
            if posts:
                latest = posts[0]
                post_id = latest.get('id')
                if post_id and post_id != self.last_wall_id:
                    self.last_wall_id = post_id
                    body = latest.get('body', '')
                    poster = latest.get('poster', {}).get('username', 'Unknown')
                    phrased = await phrase_with_groq(body, f'Post by {poster}')
                    await self.post_update(channel, f'📌 New Group Post by {poster}', phrased, body)

    async def post_update(self, channel: discord.TextChannel, title: str, phrased: str, raw: str):
        embed = discord.Embed(
            title=title,
            description=phrased,
            color=0x2d7dd2
        )
        embed.add_field(
            name='📄 Original',
            value=raw[:500] + ('...' if len(raw) > 500 else ''),
            inline=False
        )
        embed.set_footer(text='CSR System · Powered by Breezy · Scraped from SBO:R Roblox Group')
        embed.timestamp = discord.utils.utcnow()

        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            label='View SBO:R Group 🔗',
            url=f'https://www.roblox.com/communities/{GROUP_ID}',
            style=discord.ButtonStyle.link
        ))

        await channel.send(embed=embed, view=view)
        await log(self.bot, 'scraper', f'📰 New SBO:R group update posted to {channel.mention}')

    @scrape_loop.before_loop
    async def before_scrape(self):
        await self.bot.wait_until_ready()

    @discord.app_commands.command(name='forcescrape', description='[Admin] Manually trigger a group scrape now')
    @discord.app_commands.default_permissions(administrator=True)
    async def force_scrape(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.scrape_loop()
        await interaction.followup.send('✅ Scrape triggered!', ephemeral=True)


async def setup(bot):
    await bot.add_cog(GroupScraper(bot))
