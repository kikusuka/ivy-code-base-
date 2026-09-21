# data/db.py
# Ivy Bot - SQLite Database Layer
# Single file, clean schema, fast queries.
# In-memory cache for active users to keep moderation pipeline lightweight.

import sqlite3
import datetime
import threading
from contextlib import contextmanager
from typing import Optional

from config import DATABASE_PATH


# ========================
# CONNECTION MANAGEMENT
# Thread-safe connection pool using threading.local
# Each thread gets its own connection — no race conditions.
# ========================

_local = threading.local()


def get_connection() -> sqlite3.Connection:
    """Returns a thread-local SQLite connection."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(
            DATABASE_PATH,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
            check_same_thread=False,
        )
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")   # concurrent reads
        _local.conn.execute("PRAGMA foreign_keys=ON")    # enforce relationships
        _local.conn.execute("PRAGMA synchronous=NORMAL") # safe + fast
    return _local.conn


@contextmanager
def db_cursor():
    """Context manager for safe cursor usage with auto commit/rollback."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        yield cursor
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()


# ========================
# IN-MEMORY CACHE
# Active user data cached to avoid DB hits on every message.
# Cache invalidated on any write to that user's record.
# ========================

_user_cache: dict[tuple[int, int], dict] = {}  # (user_id, guild_id) -> user data
_cache_lock = threading.Lock()


def _invalidate_user_cache(user_id: int, guild_id: int):
    with _cache_lock:
        _user_cache.pop((user_id, guild_id), None)


def _get_cached_user(user_id: int, guild_id: int) -> Optional[dict]:
    with _cache_lock:
        return _user_cache.get((user_id, guild_id))


def _set_cached_user(user_id: int, guild_id: int, data: dict):
    with _cache_lock:
        _user_cache[(user_id, guild_id)] = data


# ========================
# SCHEMA INITIALIZATION
# Creates all tables if they don't exist.
# Safe to run on every startup.
# ========================

SCHEMA = """
-- Guild configuration
CREATE TABLE IF NOT EXISTS guilds (
    guild_id            INTEGER PRIMARY KEY,
    owner_id            INTEGER NOT NULL,
    is_partner          INTEGER NOT NULL DEFAULT 0,
    mod_level           TEXT NOT NULL DEFAULT 'normal',
    logs_channel_id     INTEGER,
    mod_role_id         INTEGER,
    admin_role_id       INTEGER,
    verify_channel_id   INTEGER,
    general_channel_id  INTEGER,
    setup_complete      INTEGER NOT NULL DEFAULT 0,
    setup_step          INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

-- Channel exceptions (different mod level per channel)
CREATE TABLE IF NOT EXISTS channel_exceptions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    channel_id  INTEGER NOT NULL,
    mod_level   TEXT NOT NULL,
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE,
    UNIQUE(guild_id, channel_id)
);

-- Server rules (parsed from owner input by Groq)
CREATE TABLE IF NOT EXISTS rules (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    slug            TEXT NOT NULL,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL,
    action          TEXT NOT NULL,      -- WARN | MUTE | KICK | BAN
    severity        INTEGER NOT NULL DEFAULT 1,  -- 1 low, 2 med, 3 high
    keywords        TEXT NOT NULL,      -- JSON array of keywords
    is_enabled      INTEGER NOT NULL DEFAULT 1,
    rule_version    INTEGER NOT NULL DEFAULT 1,  -- bumped on every structural update
    metadata        TEXT,               -- JSON: help_url, mod_instructions, notes
    created_at      TEXT NOT NULL,
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE,
    UNIQUE(guild_id, slug)
);

-- Users per guild
CREATE TABLE IF NOT EXISTS users (
    user_id         INTEGER NOT NULL,
    guild_id        INTEGER NOT NULL,
    username        TEXT NOT NULL,
    display_name    TEXT,
    good_points     INTEGER NOT NULL DEFAULT 0,
    strike_count    INTEGER NOT NULL DEFAULT 0,
    current_status  TEXT NOT NULL DEFAULT 'clean',  -- clean | warned | muted | banned
    appeals_used    INTEGER NOT NULL DEFAULT 0,     -- appeals used in current cycle
    cycle_strikes   INTEGER NOT NULL DEFAULT 0,     -- strikes in current cycle
    joined_at       TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (user_id, guild_id),
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
);

-- Username history (only tracked while Ivy is in server)
CREATE TABLE IF NOT EXISTS username_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    guild_id    INTEGER NOT NULL,
    username    TEXT NOT NULL,
    changed_at  TEXT NOT NULL,
    FOREIGN KEY (user_id, guild_id) REFERENCES users(user_id, guild_id) ON DELETE CASCADE
);

-- Strike events
CREATE TABLE IF NOT EXISTS strikes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    guild_id        INTEGER NOT NULL,
    reason          TEXT NOT NULL,
    layer           TEXT NOT NULL,      -- spam | badword | grooming | semantic | onnx
    score           REAL,               -- classifier score if applicable
    action_taken    TEXT NOT NULL,      -- WARN | MUTE
    channel_id      INTEGER,            -- channel where violation occurred
    message_content TEXT,               -- copy of offending message
    is_reversed     INTEGER NOT NULL DEFAULT 0,  -- 1 if appeal won
    timestamp       TEXT NOT NULL,
    FOREIGN KEY (user_id, guild_id) REFERENCES users(user_id, guild_id) ON DELETE CASCADE
);

-- Mod review queue (uncertain scores flagged for human decision)
CREATE TABLE IF NOT EXISTS mod_reviews (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    channel_id      INTEGER NOT NULL,
    message_id      INTEGER,
    message_content TEXT NOT NULL,
    score           REAL NOT NULL,
    reason          TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected
    reviewed_by     INTEGER,            -- mod user_id who acted
    created_at      TEXT NOT NULL,
    reviewed_at     TEXT,
    ping_count      INTEGER NOT NULL DEFAULT 0,
    last_pinged_at  TEXT,
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
);

-- Leaf appeals
CREATE TABLE IF NOT EXISTS leaves (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    strike_id       INTEGER,
    channel_id      INTEGER,            -- private leaf channel
    status          TEXT NOT NULL DEFAULT 'open',  -- open | resolved | closed
    priority        TEXT NOT NULL DEFAULT 'medium',
    category        TEXT NOT NULL DEFAULT 'general',
    title           TEXT NOT NULL,
    assigned_mod    INTEGER,            -- mod user_id
    mod_verdict     TEXT,               -- approved | rejected
    ai_summary      TEXT,               -- Groq summary of conversation
    try_number      INTEGER NOT NULL DEFAULT 1,
    copilot_enabled INTEGER NOT NULL DEFAULT 0,  -- mod explicitly let Ivy talk
    ai_engaged      INTEGER NOT NULL DEFAULT 0,  -- Ivy has started responding
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    resolved_at     TEXT,
    appeal_expires_at TEXT NOT NULL,    -- 24 hour window
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
);

-- Leaf messages (conversation history)
CREATE TABLE IF NOT EXISTS leaf_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    leaf_id     INTEGER NOT NULL,
    sender_id   INTEGER NOT NULL,
    is_staff    INTEGER NOT NULL DEFAULT 0,
    is_ai       INTEGER NOT NULL DEFAULT 0,
    content     TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    FOREIGN KEY (leaf_id) REFERENCES leaves(id) ON DELETE CASCADE
);

-- Mod feedback on Ivy decisions
CREATE TABLE IF NOT EXISTS feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    strike_id   INTEGER,
    mod_id      INTEGER NOT NULL,
    rating      TEXT NOT NULL,      -- approved | rejected
    note        TEXT,
    timestamp   TEXT NOT NULL,
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
);

-- Partner servers
CREATE TABLE IF NOT EXISTS partners (
    guild_id        INTEGER PRIMARY KEY,
    registered_by   INTEGER NOT NULL,   -- always KIKUSUKA_ID
    registered_at   TEXT NOT NULL
);

-- Verification state (cleared on restart, that's intentional)
CREATE TABLE IF NOT EXISTS verification_sessions (
    user_id         INTEGER NOT NULL,
    guild_id        INTEGER NOT NULL,
    captcha_answer  TEXT NOT NULL,
    otp             TEXT,
    stage           TEXT NOT NULL DEFAULT 'captcha',  -- captcha | otp
    created_at      TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    PRIMARY KEY (user_id, guild_id)
);

-- Casual chat ignore list (24hr ignore for casual spammers)
CREATE TABLE IF NOT EXISTS ignored_users (
    user_id     INTEGER NOT NULL,
    guild_id    INTEGER NOT NULL,
    ignored_until TEXT NOT NULL,
    PRIMARY KEY (user_id, guild_id)
);

-- Conflict logs (two admins giving conflicting Hey Ivy commands)
CREATE TABLE IF NOT EXISTS conflicts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    admin1_id       INTEGER NOT NULL,
    admin2_id       INTEGER NOT NULL,
    command1        TEXT NOT NULL,
    command2        TEXT NOT NULL,
    leaf_id         INTEGER,
    status          TEXT NOT NULL DEFAULT 'open',   -- open | resolved
    created_at      TEXT NOT NULL,
    resolved_at     TEXT
);

-- Per-channel message activity tracking
CREATE TABLE IF NOT EXISTS channel_activity (
    guild_id        INTEGER NOT NULL,
    channel_id      INTEGER NOT NULL,
    message_count   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, channel_id),
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
);

-- Per-member message activity tracking
CREATE TABLE IF NOT EXISTS member_activity (
    guild_id        INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    message_count   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id),
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
);

-- Member stats cache (updated every 30 min by background task)
CREATE TABLE IF NOT EXISTS member_cache (
    guild_id        INTEGER PRIMARY KEY,
    total_members   INTEGER NOT NULL DEFAULT 0,
    bot_count       INTEGER NOT NULL DEFAULT 0,
    online_members  INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT NOT NULL,
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
);

-- Daily rolled-up stats (granular tables truncated after rollup)
CREATE TABLE IF NOT EXISTS daily_stats (
    guild_id    INTEGER NOT NULL,
    stat_date   TEXT NOT NULL,
    stat_type   TEXT NOT NULL,
    entity_id   INTEGER NOT NULL,
    value       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, stat_date, stat_type, entity_id),
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
);

-- Audit trail of every case export — who pulled whose data, when.
-- This itself is the proof that case exports are never silent.
-- Per-guild trusted bot list — bots added here bypass moderation.
-- Ivy herself and Breezy are trusted globally, not per-guild.
CREATE TABLE IF NOT EXISTS trusted_bots (
    guild_id    INTEGER NOT NULL,
    bot_id      INTEGER NOT NULL,
    added_by    INTEGER NOT NULL,
    added_at    TEXT NOT NULL,
    PRIMARY KEY (guild_id, bot_id),
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
);

-- Server owner consent for training-data infrastructure to exist at all.
-- This is separate from and does not override individual member consent.
CREATE TABLE IF NOT EXISTS guild_data_consent (
    guild_id            INTEGER PRIMARY KEY,
    owner_consented     INTEGER NOT NULL DEFAULT 0,
    consented_at        TEXT,
    consented_by        INTEGER,
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
);

-- Individual member consent for their own messages being used in training.
-- Default state for any user not yet present in this table is EXCLUDED.
-- No response to the consent DM = excluded, permanently, until they
-- explicitly opt in themselves via /dataconsent.
CREATE TABLE IF NOT EXISTS user_data_consent (
    user_id             INTEGER NOT NULL,
    guild_id            INTEGER NOT NULL,
    consent_status      TEXT NOT NULL DEFAULT 'unanswered',
        -- 'unanswered' | 'opted_in' | 'opted_out'
    asked_at            TEXT,
    answered_at         TEXT,
    comprehension_passed INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, guild_id),
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
);

-- Audit trail — every consent state change is logged permanently,
-- even if the current state table gets overwritten later. This is
-- the actual proof of what was asked, when, and how someone answered.
CREATE TABLE IF NOT EXISTS consent_audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER,
    guild_id        INTEGER NOT NULL,
    event           TEXT NOT NULL,
        -- 'owner_consented' | 'user_dm_sent' | 'user_opted_in' |
        -- 'user_opted_out' | 'user_no_response_excluded' | 'user_revoked'
    timestamp       TEXT NOT NULL,
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
);

-- Known-bad image pHash cache — enables instant repost detection
-- without any AI call. Populated when AI vision confirms an image
-- as genuinely bad; future reposts of visually similar images (even
-- resized/recompressed) get caught by hash comparison alone.
CREATE TABLE IF NOT EXISTS known_bad_image_hashes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    phash       TEXT NOT NULL,
    category    TEXT NOT NULL,
    guild_id    INTEGER,
    recorded_at TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'ai_confirmed'
);

CREATE TABLE IF NOT EXISTS case_exports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    target_user_id  INTEGER NOT NULL,
    requested_by    INTEGER NOT NULL,
    exported_at     TEXT NOT NULL,
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
);

-- Analytics per guild
CREATE TABLE IF NOT EXISTS analytics (
    guild_id                INTEGER PRIMARY KEY,
    total_messages          INTEGER NOT NULL DEFAULT 0,
    toxic_blocked           INTEGER NOT NULL DEFAULT 0,
    badword_triggers        INTEGER NOT NULL DEFAULT 0,
    grooming_blocked        INTEGER NOT NULL DEFAULT 0,
    scam_blocked            INTEGER NOT NULL DEFAULT 0,
    spam_blocked            INTEGER NOT NULL DEFAULT 0,
    mod_reviews_total       INTEGER NOT NULL DEFAULT 0,
    mod_reviews_actioned    INTEGER NOT NULL DEFAULT 0,
    leaves_opened           INTEGER NOT NULL DEFAULT 0,
    leaves_won              INTEGER NOT NULL DEFAULT 0,
    avg_toxicity_score      REAL NOT NULL DEFAULT 0.0,
    score_samples           INTEGER NOT NULL DEFAULT 0,
    updated_at              TEXT NOT NULL,
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
);
"""


SCHEMA_VERSION = 4


def _run_migrations(cursor, from_version: int):
    """
    Incremental migrations — never drops data.
    Each block runs only once per version upgrade.
    """
    if from_version < 2:
        # Add rule_version and metadata fields to existing rules table
        try:
            cursor.execute(
                "ALTER TABLE rules ADD COLUMN rule_version INTEGER NOT NULL DEFAULT 1"
            )
        except Exception:
            pass  # Column may already exist on fresh DB
        try:
            cursor.execute(
                "ALTER TABLE rules ADD COLUMN metadata TEXT"
            )
        except Exception:
            pass
        print("✅ [Ivy DB] Migration v1→v2: rules table updated.")

    if from_version < 3:
        # Consent tables are created fresh by SCHEMA's CREATE TABLE IF NOT
        # EXISTS on every startup, so no destructive migration is needed.
        # This block exists to bump the version marker cleanly.
        print("✅ [Ivy DB] Migration v2→v3: data consent tables added.")

    if from_version < 4:
        try:
            cursor.execute(
                "ALTER TABLE leaves ADD COLUMN copilot_enabled INTEGER NOT NULL DEFAULT 0"
            )
        except Exception:
            pass
        try:
            cursor.execute(
                "ALTER TABLE leaves ADD COLUMN ai_engaged INTEGER NOT NULL DEFAULT 0"
            )
        except Exception:
            pass
        print("✅ [Ivy DB] Migration v3→v4: leaf copilot fields added.")


def init_db():
    """
    Initialize all tables. Safe to call on every startup.
    Schema versioning via PRAGMA user_version ensures future
    ALTER TABLE migrations never destroy existing data.
    """
    conn = get_connection()
    with db_cursor() as cursor:
        cursor.executescript(SCHEMA)

        current_version = conn.execute(
            "PRAGMA user_version"
        ).fetchone()[0]

        if current_version == 0:
            # Fresh database, set current version
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            print(f"✅ [Ivy DB] Fresh database created at schema v{SCHEMA_VERSION}.")
        elif current_version < SCHEMA_VERSION:
            # Existing database needs migration
            print(
                f"⚙️  [Ivy DB] Migrating schema "
                f"v{current_version} → v{SCHEMA_VERSION}..."
            )
            _run_migrations(cursor, current_version)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            print(f"✅ [Ivy DB] Migration complete. Now at v{SCHEMA_VERSION}.")
        else:
            print(f"✅ [Ivy DB] Schema up to date at v{SCHEMA_VERSION}.")

    print("✅ [Ivy DB] Database initialized successfully.")


# ========================
# GUILD FUNCTIONS
# ========================

def get_guild(guild_id: int) -> Optional[sqlite3.Row]:
    with db_cursor() as cursor:
        cursor.execute(
            "SELECT * FROM guilds WHERE guild_id = ?", (guild_id,)
        )
        return cursor.fetchone()


def create_guild(guild_id: int, owner_id: int, general_channel_id: int) -> None:
    now = _now()
    with db_cursor() as cursor:
        cursor.execute("""
            INSERT OR IGNORE INTO guilds
            (guild_id, owner_id, general_channel_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
        """, (guild_id, owner_id, general_channel_id, now, now))

    # Initialize analytics row too
    with db_cursor() as cursor:
        cursor.execute("""
            INSERT OR IGNORE INTO analytics (guild_id, updated_at)
            VALUES (?, ?)
        """, (guild_id, now))


def update_guild(guild_id: int, **kwargs) -> None:
    """Update any guild fields by keyword argument."""
    if not kwargs:
        return
    kwargs["updated_at"] = _now()
    fields = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [guild_id]
    with db_cursor() as cursor:
        cursor.execute(
            f"UPDATE guilds SET {fields} WHERE guild_id = ?", values
        )


def is_setup_complete(guild_id: int) -> bool:
    guild = get_guild(guild_id)
    return bool(guild and guild["setup_complete"])


def get_setup_step(guild_id: int) -> int:
    guild = get_guild(guild_id)
    return guild["setup_step"] if guild else 0


# ========================
# PARTNER FUNCTIONS
# ========================

def add_partner(guild_id: int, registered_by: int) -> None:
    now = _now()
    with db_cursor() as cursor:
        cursor.execute("""
            INSERT OR REPLACE INTO partners (guild_id, registered_by, registered_at)
            VALUES (?, ?, ?)
        """, (guild_id, registered_by, now))
    update_guild(guild_id, is_partner=1)


def remove_partner(guild_id: int) -> None:
    with db_cursor() as cursor:
        cursor.execute(
            "DELETE FROM partners WHERE guild_id = ?", (guild_id,)
        )
    update_guild(guild_id, is_partner=0)


def is_partner(guild_id: int) -> bool:
    with db_cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM partners WHERE guild_id = ?", (guild_id,)
        )
        return cursor.fetchone() is not None


# ========================
# CHANNEL EXCEPTION FUNCTIONS
# ========================

def get_channel_exceptions(guild_id: int) -> list[sqlite3.Row]:
    with db_cursor() as cursor:
        cursor.execute(
            "SELECT * FROM channel_exceptions WHERE guild_id = ?", (guild_id,)
        )
        return cursor.fetchall()


def add_channel_exception(guild_id: int, channel_id: int, mod_level: str) -> bool:
    """
    Adds a channel exception.
    Returns False if the server has hit its exception limit.
    """
    existing = get_channel_exceptions(guild_id)
    partner = is_partner(guild_id)
    max_allowed = 3 if partner else 1

    # Check if this channel already has an exception (update it)
    existing_ids = [row["channel_id"] for row in existing]
    if channel_id not in existing_ids and len(existing) >= max_allowed:
        return False

    with db_cursor() as cursor:
        cursor.execute("""
            INSERT INTO channel_exceptions (guild_id, channel_id, mod_level)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id, channel_id) DO UPDATE SET mod_level = excluded.mod_level
        """, (guild_id, channel_id, mod_level))
    return True


def get_effective_mod_level(guild_id: int, channel_id: int) -> str:
    """Returns the mod level for a specific channel, falling back to guild default."""
    with db_cursor() as cursor:
        cursor.execute("""
            SELECT mod_level FROM channel_exceptions
            WHERE guild_id = ? AND channel_id = ?
        """, (guild_id, channel_id))
        row = cursor.fetchone()
        if row:
            return row["mod_level"]

    guild = get_guild(guild_id)
    return guild["mod_level"] if guild else "normal"


# ========================
# RULES FUNCTIONS
# ========================

def get_rules(guild_id: int) -> list[sqlite3.Row]:
    with db_cursor() as cursor:
        cursor.execute("""
            SELECT * FROM rules WHERE guild_id = ? AND is_enabled = 1
            ORDER BY severity DESC
        """, (guild_id,))
        return cursor.fetchall()


def add_rule(
    guild_id: int,
    slug: str,
    name: str,
    description: str,
    action: str,
    severity: int,
    keywords: list[str],
    metadata: dict = None,
    rule_version: int = 1,
) -> None:
    import json
    now = _now()
    metadata_json = json.dumps(metadata) if metadata else None
    with db_cursor() as cursor:
        cursor.execute("""
            INSERT INTO rules
            (guild_id, slug, name, description, action, severity,
             keywords, rule_version, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, slug) DO UPDATE SET
                name = excluded.name,
                description = excluded.description,
                action = excluded.action,
                severity = excluded.severity,
                keywords = excluded.keywords,
                rule_version = rule_version + 1,
                metadata = excluded.metadata
        """, (guild_id, slug, name, description, action, severity,
              json.dumps(keywords), rule_version, metadata_json, now))


def toggle_rule(guild_id: int, slug: str, enabled: bool) -> None:
    with db_cursor() as cursor:
        cursor.execute("""
            UPDATE rules SET is_enabled = ?
            WHERE guild_id = ? AND slug = ?
        """, (int(enabled), guild_id, slug))


# ========================
# USER FUNCTIONS
# ========================

def get_user(user_id: int, guild_id: int) -> Optional[sqlite3.Row]:
    cached = _get_cached_user(user_id, guild_id)
    if cached:
        return cached

    with db_cursor() as cursor:
        cursor.execute("""
            SELECT * FROM users WHERE user_id = ? AND guild_id = ?
        """, (user_id, guild_id))
        row = cursor.fetchone()
        if row:
            _set_cached_user(user_id, guild_id, row)
        return row


def ensure_user(
    user_id: int,
    guild_id: int,
    username: str,
    display_name: str = None,
) -> None:
    """Creates user record if not exists. Updates username if changed."""
    now = _now()
    existing = get_user(user_id, guild_id)

    if existing is None:
        with db_cursor() as cursor:
            cursor.execute("""
                INSERT OR IGNORE INTO users
                (user_id, guild_id, username, display_name, joined_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (user_id, guild_id, username, display_name, now, now))
        _invalidate_user_cache(user_id, guild_id)
        return

    # Track username change if it happened
    if existing["username"] != username:
        _log_username_change(user_id, guild_id, username)
        with db_cursor() as cursor:
            cursor.execute("""
                UPDATE users SET username = ?, display_name = ?, updated_at = ?
                WHERE user_id = ? AND guild_id = ?
            """, (username, display_name, now, user_id, guild_id))
        _invalidate_user_cache(user_id, guild_id)


def _log_username_change(user_id: int, guild_id: int, new_username: str) -> None:
    with db_cursor() as cursor:
        cursor.execute("""
            INSERT INTO username_history (user_id, guild_id, username, changed_at)
            VALUES (?, ?, ?, ?)
        """, (user_id, guild_id, new_username, _now()))


def get_username_history(user_id: int, guild_id: int) -> list[sqlite3.Row]:
    with db_cursor() as cursor:
        cursor.execute("""
            SELECT * FROM username_history
            WHERE user_id = ? AND guild_id = ?
            ORDER BY changed_at DESC
            LIMIT 10
        """, (user_id, guild_id))
        return cursor.fetchall()


def update_user(user_id: int, guild_id: int, **kwargs) -> None:
    if not kwargs:
        return
    kwargs["updated_at"] = _now()
    fields = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [user_id, guild_id]
    with db_cursor() as cursor:
        cursor.execute(
            f"UPDATE users SET {fields} WHERE user_id = ? AND guild_id = ?",
            values
        )
    _invalidate_user_cache(user_id, guild_id)


def add_good_point(user_id: int, guild_id: int, amount: int = 1) -> None:
    with db_cursor() as cursor:
        cursor.execute("""
            UPDATE users SET good_points = good_points + ?, updated_at = ?
            WHERE user_id = ? AND guild_id = ?
        """, (amount, _now(), user_id, guild_id))
    _invalidate_user_cache(user_id, guild_id)


def get_reputation(user_id: int, guild_id: int) -> dict:
    """
    Calculates reputation score 0-100.
    Base 80, -10 per strike, +8 per good point, base 30 if flagged.
    """
    user = get_user(user_id, guild_id)
    if not user:
        return {"score": 80, "strikes": 0, "good_points": 0, "status": "clean"}

    base = 80
    score = base - (user["strike_count"] * 10) + (user["good_points"] * 8)
    score = max(0, min(100, score))

    return {
        "score": score,
        "strikes": user["strike_count"],
        "good_points": user["good_points"],
        "status": user["current_status"],
        "cycle_strikes": user["cycle_strikes"],
        "appeals_used": user["appeals_used"],
    }


# ========================
# STRIKE FUNCTIONS
# ========================

def add_strike(
    user_id: int,
    guild_id: int,
    reason: str,
    layer: str,
    action_taken: str,
    channel_id: int = None,
    message_content: str = None,
    score: float = None,
) -> int:
    """Adds a strike and returns the strike ID."""
    now = _now()
    with db_cursor() as cursor:
        cursor.execute("""
            INSERT INTO strikes
            (user_id, guild_id, reason, layer, score, action_taken,
             channel_id, message_content, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, guild_id, reason, layer, score, action_taken,
              channel_id, message_content, now))
        strike_id = cursor.lastrowid

    # Update user counters
    with db_cursor() as cursor:
        cursor.execute("""
            UPDATE users SET
                strike_count = strike_count + 1,
                cycle_strikes = cycle_strikes + 1,
                current_status = CASE
                    WHEN strike_count + 1 >= 4 THEN 'at_risk'
                    WHEN strike_count + 1 >= 2 THEN 'warned'
                    ELSE 'clean'
                END,
                updated_at = ?
            WHERE user_id = ? AND guild_id = ?
        """, (now, user_id, guild_id))

    _invalidate_user_cache(user_id, guild_id)
    return strike_id


def reverse_strike(strike_id: int, user_id: int, guild_id: int) -> None:
    """Reverses a strike after successful appeal."""
    with db_cursor() as cursor:
        cursor.execute("""
            UPDATE strikes SET is_reversed = 1 WHERE id = ?
        """, (strike_id,))

    with db_cursor() as cursor:
        cursor.execute("""
            UPDATE users SET
                strike_count = MAX(0, strike_count - 1),
                cycle_strikes = MAX(0, cycle_strikes - 1),
                updated_at = ?
            WHERE user_id = ? AND guild_id = ?
        """, (_now(), user_id, guild_id))

    _invalidate_user_cache(user_id, guild_id)


def get_user_strikes(
    user_id: int,
    guild_id: int,
    limit: int = 10,
) -> list[sqlite3.Row]:
    with db_cursor() as cursor:
        cursor.execute("""
            SELECT * FROM strikes
            WHERE user_id = ? AND guild_id = ? AND is_reversed = 0
            ORDER BY timestamp DESC
            LIMIT ?
        """, (user_id, guild_id, limit))
        return cursor.fetchall()


def get_all_user_strikes_for_verification(
    user_id: int,
    guild_id: int,
) -> list[sqlite3.Row]:
    """
    Returns EVERY strike ever issued to this user, including reversed
    ones. Used only by /verify (utils/verification_seal.py) — a strike
    that was later overturned on appeal still genuinely happened, so a
    seal from that DM should still verify as real. This is deliberately
    a separate function from get_user_strikes rather than an added
    parameter there, since every other caller of that function wants
    the "currently counts against them" view, and silently changing
    its default behavior would be an easy way to introduce a bug
    somewhere that isn't this feature.
    """
    with db_cursor() as cursor:
        cursor.execute("""
            SELECT * FROM strikes
            WHERE user_id = ? AND guild_id = ?
            ORDER BY timestamp DESC
        """, (user_id, guild_id))
        return cursor.fetchall()


# ========================
# MOD REVIEW FUNCTIONS
# ========================

def create_mod_review(
    guild_id: int,
    user_id: int,
    channel_id: int,
    message_content: str,
    score: float,
    message_id: int = None,
    reason: str = None,
) -> int:
    now = _now()
    with db_cursor() as cursor:
        cursor.execute("""
            INSERT INTO mod_reviews
            (guild_id, user_id, channel_id, message_id, message_content,
             score, reason, created_at, last_pinged_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (guild_id, user_id, channel_id, message_id,
              message_content, score, reason, now, now))
        return cursor.lastrowid


def get_pending_reviews(guild_id: int) -> list[sqlite3.Row]:
    with db_cursor() as cursor:
        cursor.execute("""
            SELECT * FROM mod_reviews
            WHERE guild_id = ? AND status = 'pending'
            ORDER BY created_at ASC
        """, (guild_id,))
        return cursor.fetchall()


def resolve_review(
    review_id: int,
    reviewed_by: int,
    status: str,
) -> None:
    with db_cursor() as cursor:
        cursor.execute("""
            UPDATE mod_reviews SET
                status = ?,
                reviewed_by = ?,
                reviewed_at = ?
            WHERE id = ?
        """, (status, reviewed_by, _now(), review_id))


def increment_review_ping(review_id: int) -> None:
    with db_cursor() as cursor:
        cursor.execute("""
            UPDATE mod_reviews SET
                ping_count = ping_count + 1,
                last_pinged_at = ?
            WHERE id = ?
        """, (_now(), review_id))


# ========================
# LEAF FUNCTIONS
# ========================

def create_leaf(
    guild_id: int,
    user_id: int,
    title: str,
    channel_id: int,
    strike_id: int = None,
    priority: str = "medium",
    category: str = "general",
    try_number: int = 1,
) -> int:
    from config import APPEAL
    now = _now()
    expires = _hours_from_now(24)

    with db_cursor() as cursor:
        cursor.execute("""
            INSERT INTO leaves
            (guild_id, user_id, strike_id, channel_id, title,
             priority, category, try_number, created_at, updated_at,
             appeal_expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (guild_id, user_id, strike_id, channel_id, title,
              priority, category, try_number, now, now, expires))
        leaf_id = cursor.lastrowid

    # Increment appeals used for this user
    with db_cursor() as cursor:
        cursor.execute("""
            UPDATE users SET appeals_used = appeals_used + 1, updated_at = ?
            WHERE user_id = ? AND guild_id = ?
        """, (now, user_id, guild_id))

    _invalidate_user_cache(user_id, guild_id)

    # Track analytics
    _increment_analytics(guild_id, "leaves_opened")

    return leaf_id


def get_leaf(leaf_id: int) -> Optional[sqlite3.Row]:
    with db_cursor() as cursor:
        cursor.execute("SELECT * FROM leaves WHERE id = ?", (leaf_id,))
        return cursor.fetchone()


def get_leaf_by_channel(channel_id: int) -> Optional[sqlite3.Row]:
    with db_cursor() as cursor:
        cursor.execute(
            "SELECT * FROM leaves WHERE channel_id = ?", (channel_id,)
        )
        return cursor.fetchone()


def get_active_leaves(guild_id: int) -> list[sqlite3.Row]:
    with db_cursor() as cursor:
        cursor.execute("""
            SELECT * FROM leaves
            WHERE guild_id = ? AND status = 'open'
            ORDER BY created_at DESC
        """, (guild_id,))
        return cursor.fetchall()


def update_leaf(leaf_id: int, **kwargs) -> None:
    if not kwargs:
        return
    kwargs["updated_at"] = _now()
    fields = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [leaf_id]
    with db_cursor() as cursor:
        cursor.execute(
            f"UPDATE leaves SET {fields} WHERE id = ?", values
        )


def resolve_leaf(
    leaf_id: int,
    verdict: str,
    mod_id: int,
    ai_summary: str = None,
) -> None:
    now = _now()
    with db_cursor() as cursor:
        cursor.execute("""
            UPDATE leaves SET
                status = 'resolved',
                mod_verdict = ?,
                assigned_mod = ?,
                ai_summary = ?,
                resolved_at = ?,
                updated_at = ?
            WHERE id = ?
        """, (verdict, mod_id, ai_summary, now, now, leaf_id))

    if verdict == "approved":
        _increment_analytics(
            _get_leaf_guild_id(leaf_id), "leaves_won"
        )


def _get_leaf_guild_id(leaf_id: int) -> int:
    with db_cursor() as cursor:
        cursor.execute("SELECT guild_id FROM leaves WHERE id = ?", (leaf_id,))
        row = cursor.fetchone()
        return row["guild_id"] if row else 0


def can_open_leaf(user_id: int, guild_id: int) -> tuple[bool, str]:
    """
    Checks if user can open a leaf appeal.
    Returns (can_open, reason).
    2 appeals per 5 strikes cycle.
    """
    user = get_user(user_id, guild_id)
    if not user:
        return False, "User record not found."

    from config import APPEAL
    cycle_strikes = user["cycle_strikes"]
    appeals_used = user["appeals_used"]

    if cycle_strikes < 1:
        return False, "You don't have any active strikes to appeal."

    # How many appeal cycles have they completed
    cycles_completed = cycle_strikes // APPEAL.strike_cycle
    max_appeals = (cycles_completed + 1) * APPEAL.max_appeals_per_cycle

    if appeals_used >= max_appeals:
        return False, (
            f"You've used all your appeals for this cycle. "
            f"Appeals reset after every {APPEAL.strike_cycle} strikes."
        )

    return True, "ok"


def add_leaf_message(
    leaf_id: int,
    sender_id: int,
    content: str,
    is_staff: bool = False,
    is_ai: bool = False,
) -> None:
    with db_cursor() as cursor:
        cursor.execute("""
            INSERT INTO leaf_messages
            (leaf_id, sender_id, is_staff, is_ai, content, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (leaf_id, sender_id, int(is_staff), int(is_ai),
              content, _now()))


def get_leaf_messages(leaf_id: int) -> list[sqlite3.Row]:
    with db_cursor() as cursor:
        cursor.execute("""
            SELECT * FROM leaf_messages
            WHERE leaf_id = ?
            ORDER BY timestamp ASC
        """, (leaf_id,))
        return cursor.fetchall()


# ========================
# FEEDBACK FUNCTIONS
# ========================

def add_feedback(
    guild_id: int,
    mod_id: int,
    rating: str,
    strike_id: int = None,
    note: str = None,
) -> None:
    with db_cursor() as cursor:
        cursor.execute("""
            INSERT INTO feedback
            (guild_id, strike_id, mod_id, rating, note, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (guild_id, strike_id, mod_id, rating, note, _now()))


def get_feedback_ratio(guild_id: int) -> dict:
    """Returns approval/rejection ratio for Ivy's decisions in this guild."""
    with db_cursor() as cursor:
        cursor.execute("""
            SELECT rating, COUNT(*) as count
            FROM feedback WHERE guild_id = ?
            GROUP BY rating
        """, (guild_id,))
        rows = cursor.fetchall()

    total = sum(r["count"] for r in rows)
    approved = next((r["count"] for r in rows if r["rating"] == "approved"), 0)
    rejected = next((r["count"] for r in rows if r["rating"] == "rejected"), 0)

    return {
        "total": total,
        "approved": approved,
        "rejected": rejected,
        "approval_rate": round(approved / total, 2) if total > 0 else 1.0,
    }


# ========================
# VERIFICATION FUNCTIONS
# ========================

def create_verification_session(
    user_id: int,
    guild_id: int,
    captcha_answer: str,
) -> None:
    now = _now()
    expires = _minutes_from_now(5)
    with db_cursor() as cursor:
        cursor.execute("""
            INSERT OR REPLACE INTO verification_sessions
            (user_id, guild_id, captcha_answer, stage, created_at, expires_at)
            VALUES (?, ?, ?, 'captcha', ?, ?)
        """, (user_id, guild_id, captcha_answer, now, expires))


def get_verification_session(
    user_id: int, guild_id: int
) -> Optional[sqlite3.Row]:
    with db_cursor() as cursor:
        cursor.execute("""
            SELECT * FROM verification_sessions
            WHERE user_id = ? AND guild_id = ?
        """, (user_id, guild_id))
        return cursor.fetchone()


def update_verification_session(
    user_id: int, guild_id: int, **kwargs
) -> None:
    if not kwargs:
        return
    fields = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [user_id, guild_id]
    with db_cursor() as cursor:
        cursor.execute(
            f"UPDATE verification_sessions SET {fields} "
            f"WHERE user_id = ? AND guild_id = ?",
            values
        )


def clear_verification_session(user_id: int, guild_id: int) -> None:
    with db_cursor() as cursor:
        cursor.execute("""
            DELETE FROM verification_sessions
            WHERE user_id = ? AND guild_id = ?
        """, (user_id, guild_id))


# ========================
# IGNORE LIST FUNCTIONS
# ========================

def ignore_user(user_id: int, guild_id: int, hours: int = 24) -> None:
    with db_cursor() as cursor:
        cursor.execute("""
            INSERT OR REPLACE INTO ignored_users
            (user_id, guild_id, ignored_until)
            VALUES (?, ?, ?)
        """, (user_id, guild_id, _hours_from_now(hours)))


def is_user_ignored(user_id: int, guild_id: int) -> bool:
    with db_cursor() as cursor:
        cursor.execute("""
            SELECT ignored_until FROM ignored_users
            WHERE user_id = ? AND guild_id = ?
        """, (user_id, guild_id))
        row = cursor.fetchone()
        if not row:
            return False
        if row["ignored_until"] < _now():
            # Expired, clean it up
            cursor.execute("""
                DELETE FROM ignored_users
                WHERE user_id = ? AND guild_id = ?
            """, (user_id, guild_id))
            return False
        return True


# ========================
# CONFLICT FUNCTIONS
# ========================

def log_conflict(
    guild_id: int,
    admin1_id: int,
    admin2_id: int,
    command1: str,
    command2: str,
    leaf_id: int = None,
) -> int:
    with db_cursor() as cursor:
        cursor.execute("""
            INSERT INTO conflicts
            (guild_id, admin1_id, admin2_id, command1, command2,
             leaf_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (guild_id, admin1_id, admin2_id, command1, command2,
              leaf_id, _now()))
        return cursor.lastrowid


def resolve_conflict(conflict_id: int) -> None:
    with db_cursor() as cursor:
        cursor.execute("""
            UPDATE conflicts SET status = 'resolved', resolved_at = ?
            WHERE id = ?
        """, (_now(), conflict_id))


# ========================
# ANALYTICS FUNCTIONS
# ========================

def _increment_analytics(guild_id: int, field: str, amount: int = 1) -> None:
    with db_cursor() as cursor:
        cursor.execute(f"""
            UPDATE analytics SET {field} = {field} + ?, updated_at = ?
            WHERE guild_id = ?
        """, (amount, _now(), guild_id))


def update_avg_toxicity(guild_id: int, new_score: float) -> None:
    with db_cursor() as cursor:
        cursor.execute("""
            SELECT avg_toxicity_score, score_samples FROM analytics
            WHERE guild_id = ?
        """, (guild_id,))
        row = cursor.fetchone()
        if not row:
            return

        samples = row["score_samples"]
        current_avg = row["avg_toxicity_score"]
        new_avg = ((current_avg * samples) + new_score) / (samples + 1)

        cursor.execute("""
            UPDATE analytics SET
                avg_toxicity_score = ?,
                score_samples = score_samples + 1,
                updated_at = ?
            WHERE guild_id = ?
        """, (round(new_avg, 4), _now(), guild_id))


def track_event(guild_id: int, event: str) -> None:
    """Track moderation events by name."""
    valid_fields = {
        "total_messages", "toxic_blocked", "badword_triggers",
        "grooming_blocked", "scam_blocked", "spam_blocked",
        "mod_reviews_total", "mod_reviews_actioned",
        "leaves_opened", "leaves_won",
    }
    if event in valid_fields:
        _increment_analytics(guild_id, event)


def get_analytics(guild_id: int) -> Optional[sqlite3.Row]:
    with db_cursor() as cursor:
        cursor.execute(
            "SELECT * FROM analytics WHERE guild_id = ?", (guild_id,)
        )
        return cursor.fetchone()


# ========================
# UTILITY HELPERS
# ========================

def _now() -> str:
    """ISO-8601 without microseconds — clean for SQLite date functions."""
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")


def _hours_from_now(hours: int) -> str:
    return (
        datetime.datetime.utcnow() + datetime.timedelta(hours=hours)
    ).strftime("%Y-%m-%dT%H:%M:%S")


def _minutes_from_now(minutes: int) -> str:
    return (
        datetime.datetime.utcnow() + datetime.timedelta(minutes=minutes)
    ).strftime("%Y-%m-%dT%H:%M:%S")


# ========================
# DATA CONSENT FUNCTIONS
# Two separate consent layers:
# 1. Guild owner — consents for the training-data pipeline to exist
#    for this server at all. Does not override individual member choice.
# 2. Individual member — consents for THEIR OWN messages specifically.
#    Default for anyone unanswered is EXCLUDED. No response = opted out.
# ========================

def set_guild_data_consent(guild_id: int, owner_id: int) -> None:
    """Records that the server owner has consented to the training pipeline."""
    now = _now()
    with db_cursor() as cursor:
        cursor.execute("""
            INSERT INTO guild_data_consent (guild_id, owner_consented, consented_at, consented_by)
            VALUES (?, 1, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                owner_consented = 1, consented_at = excluded.consented_at,
                consented_by = excluded.consented_by
        """, (guild_id, now, owner_id))
    log_consent_event(guild_id, None, "owner_consented")


def has_guild_consent(guild_id: int) -> bool:
    """Checks if the server owner has consented to the training pipeline existing."""
    with db_cursor() as cursor:
        cursor.execute(
            "SELECT owner_consented FROM guild_data_consent WHERE guild_id = ?",
            (guild_id,)
        )
        row = cursor.fetchone()
        return bool(row and row["owner_consented"])


def mark_consent_dm_sent(user_id: int, guild_id: int) -> None:
    """Records that the individual consent DM was sent to a user."""
    now = _now()
    with db_cursor() as cursor:
        cursor.execute("""
            INSERT INTO user_data_consent (user_id, guild_id, consent_status, asked_at)
            VALUES (?, ?, 'unanswered', ?)
            ON CONFLICT(user_id, guild_id) DO UPDATE SET asked_at = excluded.asked_at
        """, (user_id, guild_id, now))
    log_consent_event(guild_id, user_id, "user_dm_sent")


def set_user_consent(
    user_id: int,
    guild_id: int,
    opted_in: bool,
    comprehension_passed: bool = False,
) -> None:
    """Records an individual user's explicit consent decision."""
    now = _now()
    status = "opted_in" if opted_in else "opted_out"
    with db_cursor() as cursor:
        cursor.execute("""
            INSERT INTO user_data_consent
            (user_id, guild_id, consent_status, answered_at, comprehension_passed)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, guild_id) DO UPDATE SET
                consent_status = excluded.consent_status,
                answered_at = excluded.answered_at,
                comprehension_passed = excluded.comprehension_passed
        """, (user_id, guild_id, status, now, int(comprehension_passed)))
    log_consent_event(guild_id, user_id, "user_opted_in" if opted_in else "user_opted_out")


def mark_no_response_excluded(user_id: int, guild_id: int) -> None:
    """
    Called when a consent DM times out with no reply.
    No response is treated as a deliberate exclusion, not a pending state —
    this is a firm, permanent default until the user explicitly opts in
    themselves later via /dataconsent.
    """
    now = _now()
    with db_cursor() as cursor:
        cursor.execute("""
            UPDATE user_data_consent SET consent_status = 'opted_out', answered_at = ?
            WHERE user_id = ? AND guild_id = ? AND consent_status = 'unanswered'
        """, (now, user_id, guild_id))
    log_consent_event(guild_id, user_id, "user_no_response_excluded")


def get_user_consent_status(user_id: int, guild_id: int) -> str:
    """Returns 'unanswered' | 'opted_in' | 'opted_out'. Defaults to opted_out if no record exists."""
    with db_cursor() as cursor:
        cursor.execute(
            "SELECT consent_status FROM user_data_consent WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id)
        )
        row = cursor.fetchone()
        return row["consent_status"] if row else "opted_out"


def can_use_for_training(user_id: int, guild_id: int) -> bool:
    """
    The single source of truth for whether a specific message/event can
    be included in any future training dataset. Requires BOTH the guild
    owner's consent AND this specific user's individual opt-in.
    """
    if not has_guild_consent(guild_id):
        return False
    return get_user_consent_status(user_id, guild_id) == "opted_in"


def revoke_user_consent(user_id: int, guild_id: int) -> None:
    """User-initiated revocation. Always available, no questions asked."""
    now = _now()
    with db_cursor() as cursor:
        cursor.execute("""
            UPDATE user_data_consent SET consent_status = 'opted_out', answered_at = ?
            WHERE user_id = ? AND guild_id = ?
        """, (now, user_id, guild_id))
    log_consent_event(guild_id, user_id, "user_revoked")


def log_consent_event(guild_id: int, user_id: Optional[int], event: str) -> None:
    """Permanent audit trail of every consent state change. Never overwritten, never deleted."""
    with db_cursor() as cursor:
        cursor.execute("""
            INSERT INTO consent_audit_log (user_id, guild_id, event, timestamp)
            VALUES (?, ?, ?, ?)
        """, (user_id, guild_id, event, _now()))
