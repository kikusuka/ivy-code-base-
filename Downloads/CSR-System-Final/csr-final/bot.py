import discord
from discord.ext import commands
import os
import asyncio
from dotenv import load_dotenv
from web.server import start_web_server
from utils.db import init_db

load_dotenv()

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.presences = True

bot = commands.Bot(command_prefix='.', intents=intents, help_command=None)

COGS = [
    'commands.setup',
    'commands.account',
    'commands.verify',
    'commands.serverinfo',
    'commands.guildinfo',
    'commands.scout',
    'commands.mod',
    'commands.event',
    'commands.help',
    'commands.sync',
    'commands.about',
    'commands.portal',
    'commands.roblox_link',
    'commands.presence_tracker',
    'commands.group_scraper',
    'commands.warframe_tracker',
    'commands.wiki_chat',
    'commands.reputation',
    'commands.adminhelp',
    'events.on_ready',
    'events.on_member_join',
    'events.on_member_update',
]


async def main():
    print('\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    print('   CSR SYSTEM  ·  Powered by Breezy')
    print('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    init_db()

    failed = []
    for cog in COGS:
        try:
            await bot.load_extension(cog)
            print(f'  ✅ {cog}')
        except Exception as e:
            print(f'  ❌ {cog} — {e}')
            failed.append(cog)

    if failed:
        print(f'\n  ⚠️  {len(failed)} cog(s) failed to load')
    else:
        print(f'\n  ✅ All {len(COGS)} cogs loaded')

    start_web_server(bot)
    await bot.start(os.getenv('BOT_TOKEN'))


if __name__ == '__main__':
    asyncio.run(main())
