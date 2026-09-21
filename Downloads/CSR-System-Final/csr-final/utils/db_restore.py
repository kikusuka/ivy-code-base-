"""
CSR System — Database Health Check & Restore
On startup: checks SQLite integrity. If corrupted, rebuilds from Discord backup channel.
"""

import sqlite3
import json
import re
import asyncio
from pathlib import Path
from utils.db import init_db, DB_PATH

HEALTH_OK      = 'ok'
HEALTH_MISSING = 'missing'
HEALTH_CORRUPT = 'corrupt'


def check_db_health() -> str:
    """Check if SQLite database is healthy."""
    if not DB_PATH.exists():
        return HEALTH_MISSING
    try:
        with sqlite3.connect(DB_PATH, timeout=5) as conn:
            result = conn.execute('PRAGMA integrity_check').fetchone()
            if result and result[0] == 'ok':
                return HEALTH_OK
            return HEALTH_CORRUPT
    except Exception:
        return HEALTH_CORRUPT


async def restore_from_backup(bot, guild_id: str, backup_channel_id: str) -> bool:
    """
    Scan the backup channel and rebuild the SQLite database.
    Returns True if restore was successful.
    """
    print('🔄 Attempting database restore from Discord backup channel...')
    try:
        guild = bot.get_guild(int(guild_id))
        if not guild:
            print('❌ Restore failed: Guild not found')
            return False

        channel = guild.get_channel(int(backup_channel_id))
        if not channel:
            print('❌ Restore failed: Backup channel not found')
            return False

        # Re-init fresh DB
        if DB_PATH.exists():
            DB_PATH.rename(DB_PATH.with_suffix('.db.corrupted'))
        init_db()

        restored_accounts = 0
        restored_configs = 0

        # Scan last 200 messages in backup channel
        async for message in channel.history(limit=200, oldest_first=False):
            content = message.content
            if not content:
                continue

            # Extract JSON blocks
            json_blocks = re.findall(r'```(?:json)?\n(.*?)\n```', content, re.DOTALL)
            for block in json_blocks:
                try:
                    # Detect type from header
                    if '[BACKUP:ACCOUNTS]' in content:
                        data = json.loads(block)
                        _restore_accounts(data)
                        restored_accounts = len(data)

                    elif '[BACKUP:SERVER_' in content:
                        data = json.loads(block)
                        _restore_server_config(data)
                        restored_configs += 1

                except json.JSONDecodeError:
                    continue

        print(f'✅ Restore complete: {restored_accounts} accounts, {restored_configs} server configs')
        return True

    except Exception as e:
        print(f'❌ Restore error: {e}')
        return False


def _restore_accounts(data: dict):
    """Restore accounts table from backup JSON."""
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        for discord_id, acc in data.items():
            try:
                conn.execute('''
                    INSERT OR IGNORE INTO accounts
                    (discord_id, csr_id, tag, password_hash, created_at, verified,
                     reputation, roblox_id, roblox_username, roblox_linked,
                     steam_id, steam_username, steam_linked)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    str(discord_id),
                    acc.get('csr_id', f'CSR-RESTORED'),
                    acc.get('tag', 'Unknown'),
                    acc.get('password_hash', ''),
                    acc.get('created_at', ''),
                    int(acc.get('verified', 0)),
                    int(acc.get('reputation', 0)),
                    acc.get('roblox_id'),
                    acc.get('roblox_username'),
                    int(acc.get('roblox_linked', 0)),
                    acc.get('steam_id'),
                    acc.get('steam_username'),
                    int(acc.get('steam_linked', 0)),
                ))
            except Exception as e:
                print(f'Account restore error ({discord_id}): {e}')
        conn.commit()


def _restore_server_config(data: dict):
    """Restore server config from backup JSON."""
    guild_id = data.get('guild_id')
    if not guild_id:
        return
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        try:
            cols = ', '.join(data.keys())
            placeholders = ', '.join('?' * len(data))
            conn.execute(
                f'INSERT OR IGNORE INTO server_configs ({cols}) VALUES ({placeholders})',
                list(data.values())
            )
            conn.commit()
        except Exception as e:
            print(f'Config restore error: {e}')


async def startup_health_check(bot) -> bool:
    """
    Run on bot ready. Checks DB health and restores if needed.
    Returns True if DB is healthy (or was restored successfully).
    """
    health = check_db_health()

    if health == HEALTH_OK:
        print('✅ Database health check passed')
        return True

    status = '❌ Missing' if health == HEALTH_MISSING else '⚠️ Corrupted'
    print(f'{status} — attempting restore from Discord backup...')

    # Find any guild with a backup channel configured
    for guild in bot.guilds:
        try:
            with sqlite3.connect(DB_PATH, timeout=5) as conn:
                row = conn.execute(
                    'SELECT backup_channel_id FROM server_configs WHERE guild_id = ?',
                    (str(guild.id),)
                ).fetchone()
                if row and row[0]:
                    success = await restore_from_backup(bot, str(guild.id), row[0])
                    if success:
                        return True
        except Exception:
            # DB too corrupted to even read — try scanning all guilds
            for g in bot.guilds:
                for channel in g.text_channels:
                    if 'backup' in channel.name.lower():
                        success = await restore_from_backup(bot, str(g.id), str(channel.id))
                        if success:
                            return True

    print('❌ Could not restore database. Starting fresh.')
    init_db()
    return False
