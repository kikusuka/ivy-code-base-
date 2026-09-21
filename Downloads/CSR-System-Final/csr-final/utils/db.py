"""
CSR System — Database Layer
SQLite with full schema. Abstracted so PostgreSQL swap = change this file only.
"""

import sqlite3
import bcrypt
import os
import asyncio
import json
from pathlib import Path
from datetime import datetime, timezone
from contextlib import contextmanager

DB_PATH = Path('data/csr.db')
_bot = None
_backup_msg_ids = {}

SERVER_TYPES = {
    'sbor':     {'name': 'Sword Blox Online Rebirth', 'platform': 'roblox', 'place_id': '4733278992',  'group_id': '5683480',  'wiki': 'swordbloxonlinerebirth.fandom.com', 'yt_query': 'Sword Blox Online Rebirth'},
    'bh2':      {'name': 'Blue Heater 2',             'platform': 'roblox', 'place_id': '16893821047', 'group_id': '34068311', 'wiki': 'blue-heater-2.fandom.com',           'yt_query': 'Blue Heater 2 Roblox'},
    'bf':       {'name': 'Blox Fruits',               'platform': 'roblox', 'place_id': '2753915549',  'group_id': '4372130',  'wiki': 'blox-fruits.fandom.com',             'yt_query': 'Blox Fruits Roblox'},
    'warframe': {'name': 'Warframe',                  'platform': 'steam',  'place_id': None,          'group_id': None,       'wiki': 'wiki.warframe.com',                  'yt_query': 'Warframe'},
}


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA foreign_keys=ON')
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript('''
            -- Global CSR accounts (cross-server)
            CREATE TABLE IF NOT EXISTS accounts (
                discord_id      TEXT PRIMARY KEY,
                csr_id          TEXT UNIQUE NOT NULL,
                tag             TEXT NOT NULL,
                password_hash   TEXT NOT NULL,
                created_at      TEXT NOT NULL,
                verified        INTEGER DEFAULT 0,
                banned          INTEGER DEFAULT 0,
                ban_reason      TEXT,
                reputation      INTEGER DEFAULT 0,
                roblox_id       TEXT UNIQUE,
                roblox_username TEXT,
                roblox_linked   INTEGER DEFAULT 0,
                steam_id        TEXT UNIQUE,
                steam_username  TEXT,
                steam_linked    INTEGER DEFAULT 0,
                in_game         INTEGER DEFAULT 0,
                last_seen       TEXT
            );

            -- Username history
            CREATE TABLE IF NOT EXISTS username_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                discord_id  TEXT NOT NULL,
                username    TEXT NOT NULL,
                changed_at  TEXT NOT NULL,
                FOREIGN KEY (discord_id) REFERENCES accounts(discord_id)
            );

            -- Role history (per guild)
            CREATE TABLE IF NOT EXISTS role_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                discord_id  TEXT NOT NULL,
                guild_id    TEXT NOT NULL,
                action      TEXT NOT NULL,
                role_name   TEXT NOT NULL,
                changed_at  TEXT NOT NULL
            );

            -- Server configs
            CREATE TABLE IF NOT EXISTS server_configs (
                guild_id            TEXT PRIMARY KEY,
                server_type         TEXT,
                guild_name          TEXT,
                verified_role_id    TEXT,
                unverified_role_id  TEXT,
                in_game_role_id     TEXT,
                log_channel_id      TEXT,
                backup_channel_id   TEXT,
                welcome_channel_id  TEXT,
                updates_channel_id  TEXT,
                portal_channel_id   TEXT,
                setup_complete      INTEGER DEFAULT 0,
                last_shout          TEXT,
                last_wall_id        TEXT
            );

            -- Guild info sections
            CREATE TABLE IF NOT EXISTS guild_info (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id    TEXT NOT NULL,
                section     TEXT NOT NULL,
                content     TEXT NOT NULL,
                UNIQUE(guild_id, section)
            );

            -- Per-server member data (roles saved for restore)
            CREATE TABLE IF NOT EXISTS member_data (
                discord_id      TEXT NOT NULL,
                guild_id        TEXT NOT NULL,
                guild_member_id TEXT,
                saved_roles     TEXT DEFAULT '[]',
                joined_at       TEXT,
                left_at         TEXT,
                current_member  INTEGER DEFAULT 1,
                PRIMARY KEY (discord_id, guild_id)
            );

            -- OTPs stored in DB (survives restarts)
            CREATE TABLE IF NOT EXISTS otps (
                code        TEXT PRIMARY KEY,
                discord_id  TEXT NOT NULL,
                expires_at  TEXT NOT NULL,
                used        INTEGER DEFAULT 0,
                created_at  TEXT NOT NULL
            );

            -- Pending role assignments (retry queue)
            CREATE TABLE IF NOT EXISTS pending_actions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id    TEXT NOT NULL,
                discord_id  TEXT NOT NULL,
                action      TEXT NOT NULL,
                payload     TEXT DEFAULT '{}',
                retries     INTEGER DEFAULT 0,
                created_at  TEXT NOT NULL,
                done        INTEGER DEFAULT 0
            );

            -- Login rate limiting
            CREATE TABLE IF NOT EXISTS login_attempts (
                discord_id  TEXT NOT NULL,
                attempted_at TEXT NOT NULL,
                success     INTEGER DEFAULT 0
            );

            -- Wiki cache
            CREATE TABLE IF NOT EXISTS wiki_cache (
                cache_key   TEXT PRIMARY KEY,
                content     TEXT NOT NULL,
                source_url  TEXT,
                cached_at   TEXT NOT NULL
            );

            -- Events
            CREATE TABLE IF NOT EXISTS events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id    TEXT NOT NULL,
                name        TEXT NOT NULL,
                date_str    TEXT NOT NULL,
                content     TEXT,
                notify      TEXT,
                created_by  TEXT,
                created_at  TEXT NOT NULL
            );

            -- Cross-server shoutouts cooldown
            CREATE TABLE IF NOT EXISTS shoutout_cooldowns (
                discord_id  TEXT NOT NULL,
                guild_id    TEXT NOT NULL,
                last_shout  TEXT NOT NULL,
                PRIMARY KEY (discord_id, guild_id)
            );

            -- Indexes
            CREATE INDEX IF NOT EXISTS idx_accounts_csr_id ON accounts(csr_id);
            CREATE INDEX IF NOT EXISTS idx_otps_discord ON otps(discord_id);
            CREATE INDEX IF NOT EXISTS idx_pending_done ON pending_actions(done);
            CREATE INDEX IF NOT EXISTS idx_member_guild ON member_data(guild_id);
            CREATE INDEX IF NOT EXISTS idx_login_discord ON login_attempts(discord_id);
        ''')
    print('✅ Database initialised')


def set_bot(bot):
    global _bot
    _bot = bot


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Accounts ───────────────────────────────────────────────────────────────

def get_account(discord_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute('SELECT * FROM accounts WHERE discord_id = ?', (str(discord_id),)).fetchone()
        return dict(row) if row else None


def get_account_by_csr(csr_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute('SELECT * FROM accounts WHERE csr_id = ?', (csr_id.upper(),)).fetchone()
        return dict(row) if row else None


def create_account(discord_id: str, tag: str, password: str) -> dict:
    with get_conn() as conn:
        count = conn.execute('SELECT COUNT(*) FROM accounts').fetchone()[0]
        csr_id = f'CSR-{str(count + 1).zfill(5)}'
        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        conn.execute('''
            INSERT INTO accounts (discord_id, csr_id, tag, password_hash, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (str(discord_id), csr_id, tag, hashed, now()))
        conn.execute('INSERT INTO username_history (discord_id, username, changed_at) VALUES (?, ?, ?)',
                     (str(discord_id), tag, now()))
    account = get_account(discord_id)
    _trigger_backup('accounts')
    return account


def update_account(discord_id: str, **kwargs) -> dict | None:
    if not kwargs:
        return get_account(discord_id)
    fields = ', '.join(f'{k} = ?' for k in kwargs)
    values = list(kwargs.values()) + [str(discord_id)]
    with get_conn() as conn:
        conn.execute(f'UPDATE accounts SET {fields} WHERE discord_id = ?', values)
    _trigger_backup('accounts')
    return get_account(discord_id)


def verify_password(discord_id: str, password: str) -> bool:
    acc = get_account(discord_id)
    if not acc:
        return False
    return bcrypt.checkpw(password.encode(), acc['password_hash'].encode())


def verify_password_by_csr(csr_id: str, password: str) -> bool:
    acc = get_account_by_csr(csr_id)
    if not acc:
        return False
    return bcrypt.checkpw(password.encode(), acc['password_hash'].encode())


# ── Login rate limiting ────────────────────────────────────────────────────

def check_login_rate_limit(discord_id: str) -> tuple[bool, int]:
    """Returns (is_allowed, remaining_cooldown_seconds)."""
    with get_conn() as conn:
        from datetime import timedelta
        import datetime as dt
        cutoff = (dt.datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        fails = conn.execute('''
            SELECT COUNT(*) FROM login_attempts
            WHERE discord_id = ? AND success = 0 AND attempted_at > ?
        ''', (str(discord_id), cutoff)).fetchone()[0]

        if fails >= 3:
            last = conn.execute('''
                SELECT attempted_at FROM login_attempts
                WHERE discord_id = ? AND success = 0
                ORDER BY attempted_at DESC LIMIT 1
            ''', (str(discord_id),)).fetchone()
            if last:
                last_time = dt.datetime.fromisoformat(last[0])
                if last_time.tzinfo is None:
                    last_time = last_time.replace(tzinfo=timezone.utc)
                elapsed = (dt.datetime.now(timezone.utc) - last_time).total_seconds()
                remaining = max(0, int(600 - elapsed))
                if remaining > 0:
                    return False, remaining
        return True, 0


def record_login_attempt(discord_id: str, success: bool):
    with get_conn() as conn:
        conn.execute('INSERT INTO login_attempts (discord_id, attempted_at, success) VALUES (?, ?, ?)',
                     (str(discord_id), now(), 1 if success else 0))


# ── OTPs ──────────────────────────────────────────────────────────────────

def create_otp(discord_id: str) -> tuple[str, str]:
    import random
    import datetime as dt
    code = str(random.randint(100000, 999999))
    expires = (dt.datetime.now(timezone.utc) + dt.timedelta(minutes=2)).isoformat()
    with get_conn() as conn:
        conn.execute('DELETE FROM otps WHERE discord_id = ?', (str(discord_id),))
        conn.execute('INSERT INTO otps (code, discord_id, expires_at, created_at) VALUES (?, ?, ?, ?)',
                     (code, str(discord_id), expires, now()))
    return code, expires


def validate_otp(code: str) -> dict:
    import datetime as dt
    with get_conn() as conn:
        row = conn.execute('SELECT * FROM otps WHERE code = ?', (code,)).fetchone()
        if not row:
            return {'valid': False, 'reason': 'invalid'}
        if row['used']:
            return {'valid': False, 'reason': 'already_used'}
        expires = dt.datetime.fromisoformat(row['expires_at'])
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if dt.datetime.now(timezone.utc) > expires:
            conn.execute('DELETE FROM otps WHERE code = ?', (code,))
            return {'valid': False, 'reason': 'expired'}
        conn.execute('UPDATE otps SET used = 1 WHERE code = ?', (code,))
        return {'valid': True, 'user_id': row['discord_id']}


def clean_expired_otps():
    import datetime as dt
    cutoff = dt.datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute('DELETE FROM otps WHERE expires_at < ? OR used = 1', (cutoff,))


# ── Server Config ──────────────────────────────────────────────────────────

def get_server_config(guild_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute('SELECT * FROM server_configs WHERE guild_id = ?', (str(guild_id),)).fetchone()
        return dict(row) if row else None


def upsert_server_config(guild_id: str, **kwargs) -> dict:
    with get_conn() as conn:
        existing = conn.execute('SELECT guild_id FROM server_configs WHERE guild_id = ?', (str(guild_id),)).fetchone()
        if existing:
            if kwargs:
                fields = ', '.join(f'{k} = ?' for k in kwargs)
                conn.execute(f'UPDATE server_configs SET {fields} WHERE guild_id = ?',
                             list(kwargs.values()) + [str(guild_id)])
        else:
            kwargs['guild_id'] = str(guild_id)
            cols = ', '.join(kwargs.keys())
            placeholders = ', '.join('?' * len(kwargs))
            conn.execute(f'INSERT INTO server_configs ({cols}) VALUES ({placeholders})', list(kwargs.values()))
    return get_server_config(guild_id)


def get_server_type(guild_id: str) -> str | None:
    cfg = get_server_config(guild_id)
    return cfg['server_type'] if cfg else None


def get_server_info(server_type: str) -> dict:
    return SERVER_TYPES.get(server_type, {})


def get_all_server_configs() -> list:
    with get_conn() as conn:
        rows = conn.execute('SELECT * FROM server_configs WHERE setup_complete = 1').fetchall()
        return [dict(r) for r in rows]


# ── Member Data ────────────────────────────────────────────────────────────

def get_member_data(guild_id: str, discord_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute('SELECT * FROM member_data WHERE guild_id = ? AND discord_id = ?',
                           (str(guild_id), str(discord_id))).fetchone()
        return dict(row) if row else None


def upsert_member_data(guild_id: str, discord_id: str, **kwargs) -> dict:
    with get_conn() as conn:
        existing = conn.execute('SELECT discord_id FROM member_data WHERE guild_id = ? AND discord_id = ?',
                                (str(guild_id), str(discord_id))).fetchone()
        if existing:
            if kwargs:
                fields = ', '.join(f'{k} = ?' for k in kwargs)
                conn.execute(f'UPDATE member_data SET {fields} WHERE guild_id = ? AND discord_id = ?',
                             list(kwargs.values()) + [str(guild_id), str(discord_id)])
        else:
            kwargs.update({'guild_id': str(guild_id), 'discord_id': str(discord_id), 'joined_at': now()})
            cols = ', '.join(kwargs.keys())
            placeholders = ', '.join('?' * len(kwargs))
            conn.execute(f'INSERT INTO member_data ({cols}) VALUES ({placeholders})', list(kwargs.values()))
    return get_member_data(guild_id, discord_id)


def save_member_roles(guild_id: str, discord_id: str, role_ids: list):
    upsert_member_data(guild_id, discord_id, saved_roles=json.dumps(role_ids))


def get_saved_roles(guild_id: str, discord_id: str) -> list:
    data = get_member_data(guild_id, discord_id)
    if not data:
        return []
    try:
        return json.loads(data.get('saved_roles', '[]'))
    except Exception:
        return []


def track_role_change(discord_id: str, guild_id: str, action: str, role_name: str):
    with get_conn() as conn:
        conn.execute('INSERT INTO role_history (discord_id, guild_id, action, role_name, changed_at) VALUES (?, ?, ?, ?, ?)',
                     (str(discord_id), str(guild_id), action, role_name, now()))


def get_role_history(discord_id: str, guild_id: str, limit: int = 10) -> list:
    with get_conn() as conn:
        rows = conn.execute('''SELECT * FROM role_history WHERE discord_id = ? AND guild_id = ?
                               ORDER BY changed_at DESC LIMIT ?''',
                            (str(discord_id), str(guild_id), limit)).fetchall()
        return [dict(r) for r in rows]


# ── Guild Info ─────────────────────────────────────────────────────────────

def get_guild_info(guild_id: str) -> list:
    with get_conn() as conn:
        rows = conn.execute('SELECT * FROM guild_info WHERE guild_id = ?', (str(guild_id),)).fetchall()
        return [dict(r) for r in rows]


def upsert_guild_info(guild_id: str, section: str, content: str):
    with get_conn() as conn:
        conn.execute('INSERT OR REPLACE INTO guild_info (guild_id, section, content) VALUES (?, ?, ?)',
                     (str(guild_id), section, content))


def delete_guild_info(guild_id: str, section: str):
    with get_conn() as conn:
        conn.execute('DELETE FROM guild_info WHERE guild_id = ? AND section = ?', (str(guild_id), section))


# ── Pending Actions Queue ──────────────────────────────────────────────────

def queue_action(guild_id: str, discord_id: str, action: str, payload: dict = {}):
    with get_conn() as conn:
        conn.execute('''INSERT INTO pending_actions (guild_id, discord_id, action, payload, created_at)
                        VALUES (?, ?, ?, ?, ?)''',
                     (str(guild_id), str(discord_id), action, json.dumps(payload), now()))


def get_pending_actions() -> list:
    with get_conn() as conn:
        rows = conn.execute('SELECT * FROM pending_actions WHERE done = 0 AND retries < 5').fetchall()
        return [dict(r) for r in rows]


def mark_action_done(action_id: int):
    with get_conn() as conn:
        conn.execute('UPDATE pending_actions SET done = 1 WHERE id = ?', (action_id,))


def increment_action_retry(action_id: int):
    with get_conn() as conn:
        conn.execute('UPDATE pending_actions SET retries = retries + 1 WHERE id = ?', (action_id,))


# ── Wiki Cache ─────────────────────────────────────────────────────────────

def get_wiki_cache(cache_key: str) -> dict | None:
    import datetime as dt
    with get_conn() as conn:
        row = conn.execute('SELECT * FROM wiki_cache WHERE cache_key = ?', (cache_key,)).fetchone()
        if not row:
            return None
        cached_at = dt.datetime.fromisoformat(row['cached_at'])
        if cached_at.tzinfo is None:
            cached_at = cached_at.replace(tzinfo=timezone.utc)
        age = (dt.datetime.now(timezone.utc) - cached_at).total_seconds()
        if age > 86400:  # 24 hours
            conn.execute('DELETE FROM wiki_cache WHERE cache_key = ?', (cache_key,))
            return None
        return dict(row)


def set_wiki_cache(cache_key: str, content: str, source_url: str):
    with get_conn() as conn:
        conn.execute('INSERT OR REPLACE INTO wiki_cache (cache_key, content, source_url, cached_at) VALUES (?, ?, ?, ?)',
                     (cache_key, content, source_url, now()))


# ── Events ────────────────────────────────────────────────────────────────

def get_events(guild_id: str) -> list:
    with get_conn() as conn:
        rows = conn.execute('SELECT * FROM events WHERE guild_id = ? ORDER BY created_at DESC LIMIT 20',
                            (str(guild_id),)).fetchall()
        return [dict(r) for r in rows]


def add_event(guild_id: str, name: str, date_str: str, content: str, notify: str, created_by: str) -> dict:
    with get_conn() as conn:
        conn.execute('''INSERT INTO events (guild_id, name, date_str, content, notify, created_by, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)''',
                     (str(guild_id), name, date_str, content, notify, created_by, now()))
        row = conn.execute('SELECT * FROM events WHERE guild_id = ? ORDER BY id DESC LIMIT 1',
                           (str(guild_id),)).fetchone()
        return dict(row)


# ── Reputation ────────────────────────────────────────────────────────────

def add_reputation(discord_id: str, points: int):
    with get_conn() as conn:
        conn.execute('UPDATE accounts SET reputation = reputation + ? WHERE discord_id = ?',
                     (points, str(discord_id)))
    _trigger_backup('accounts')


# ── Shoutout Cooldown ─────────────────────────────────────────────────────

def check_shoutout_cooldown(discord_id: str, guild_id: str) -> bool:
    import datetime as dt
    with get_conn() as conn:
        row = conn.execute('SELECT last_shout FROM shoutout_cooldowns WHERE discord_id = ? AND guild_id = ?',
                           (str(discord_id), str(guild_id))).fetchone()
        if not row:
            return True
        last = dt.datetime.fromisoformat(row[0])
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return (dt.datetime.now(timezone.utc) - last).total_seconds() > 3600


def set_shoutout_cooldown(discord_id: str, guild_id: str):
    with get_conn() as conn:
        conn.execute('INSERT OR REPLACE INTO shoutout_cooldowns (discord_id, guild_id, last_shout) VALUES (?, ?, ?)',
                     (str(discord_id), str(guild_id), now()))


# ── Backup ────────────────────────────────────────────────────────────────

def _trigger_backup(label: str):
    if _bot:
        asyncio.run_coroutine_threadsafe(_backup(label), _bot.loop)


async def _backup(label: str):
    try:
        configs = get_all_server_configs()
        for cfg in configs:
            ch_id = cfg.get('backup_channel_id')
            if not ch_id:
                continue
            guild = _bot.get_guild(int(cfg['guild_id']))
            if not guild:
                continue
            ch = guild.get_channel(int(ch_id))
            if not ch:
                continue
            content = f'```\n[CSR BACKUP · {label.upper()}] {datetime.now(timezone.utc).isoformat()}\nDatabase: data/csr.db · Size: {DB_PATH.stat().st_size / 1024:.1f}KB\n```'
            key = f"{cfg['guild_id']}:{label}"
            if key in _backup_msg_ids:
                try:
                    msg = await ch.fetch_message(_backup_msg_ids[key])
                    await msg.edit(content=content)
                    return
                except Exception:
                    pass
            msg = await ch.send(content)
            _backup_msg_ids[key] = msg.id
            break
    except Exception as e:
        print(f'Backup error: {e}')
