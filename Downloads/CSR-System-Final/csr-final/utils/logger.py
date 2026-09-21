import discord
from datetime import datetime, timezone
from utils.db import get_server_config

COLORS = {
    'verify':     0x00b4d8, 'account':    0x0077b6,
    'roblox':     0x48cae4, 'steam':      0x023e8a,
    'presence':   0x90e0ef, 'scraper':    0x0096c7,
    'mod':        0xe74c3c, 'join':       0x2ecc71,
    'leave':      0xe74c3c, 'reputation': 0xf1c40f,
    'wiki':       0x48cae4, 'setup':      0x0077b6,
    'default':    0x00b4d8,
}
ICONS = {
    'verify': '✅', 'account': '🪪', 'roblox': '🎮',
    'steam': '🎮', 'presence': '🟢', 'scraper': '📰',
    'mod': '🛡️', 'join': '👋', 'leave': '📤',
    'reputation': '⭐', 'wiki': '🔍', 'setup': '⚙️',
    'default': '📋',
}


async def log(bot, guild_id: str, category: str, message: str):
    cfg = get_server_config(str(guild_id))
    if not cfg:
        return
    ch_id = cfg.get('log_channel_id')
    if not ch_id:
        return
    try:
        guild = bot.get_guild(int(guild_id))
        if not guild:
            return
        ch = guild.get_channel(int(ch_id))
        if not ch:
            return
        embed = discord.Embed(
            description=f'{ICONS.get(category, "📋")} {message}',
            color=COLORS.get(category, 0x00b4d8)
        )
        embed.set_footer(text=f'CSR System · {category.upper()} · Powered by Breezy')
        embed.timestamp = datetime.now(timezone.utc)
        await ch.send(embed=embed)
    except Exception as e:
        print(f'[LOG ERROR] {e}')


def log_startup():
    print('✅ Logger ready')
