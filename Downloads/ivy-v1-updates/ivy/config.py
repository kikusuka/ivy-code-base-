# config.py
# Ivy Bot - Core Configuration
# Built by Kikusuka | Part of the Breezy Hub ecosystem
# Pydantic validated, dataclass grouped, startup-safe.

import os
from typing import NamedTuple
from dataclasses import dataclass
from dotenv import load_dotenv
from pydantic import BaseModel, validator, ValidationError

load_dotenv()


# ========================
# MODERATION LEVEL SCHEMA
# Pydantic validates every field on startup.
# Wrong type = immediate crash with clear error.
# ========================

class ModerationConfig(BaseModel):
    description: str
    spam: bool
    badwords: str           # "severe_only" | "full"
    grooming: bool
    scam: bool
    politics: bool
    semantic_ai: bool
    onnx_classifier: bool

    @validator("badwords")
    def badwords_must_be_valid(cls, v):
        allowed = {"severe_only", "full"}
        if v not in allowed:
            raise ValueError(f"badwords must be one of {allowed}, got '{v}'")
        return v

    @validator("onnx_classifier")
    def onnx_requires_semantic(cls, v, values):
        if v and not values.get("semantic_ai"):
            raise ValueError(
                "onnx_classifier cannot be True if semantic_ai is False. "
                "Hardcore requires semantic_ai enabled too."
            )
        return v


# ========================
# TIMING CONSTANTS
# NamedTuple for immutability — these should never change at runtime.
# ========================

class TimingConstants(NamedTuple):
    otp_expiry_seconds: int             # OTP expires after this
    mod_review_ping_interval: int       # how often to re-ping mods for pending reviews
    casual_ignore_hours: int            # ignore window for casual chat spammers
    appeal_window_hours: int            # window to open leaf after a strike
    spam_detection_window: int          # seconds to watch for repeated messages
    spam_repeat_threshold: int          # same message X times triggers spam
    alt_account_age_hours: int          # accounts younger than this get flagged


TIMING = TimingConstants(
    otp_expiry_seconds=300,
    mod_review_ping_interval=1800,
    casual_ignore_hours=24,
    appeal_window_hours=24,
    spam_detection_window=10,
    spam_repeat_threshold=3,
    alt_account_age_hours=24,
)


# ========================
# SCORE THRESHOLDS
# Dataclass for grouping — readable, organized, type hinted.
# ========================

@dataclass
class ScoreThresholds:
    block: float = 0.90     # auto action taken
    flag: float = 0.65      # flagged for mod review
    safe: float = 0.0       # allow through

    def __post_init__(self):
        if not (self.safe <= self.flag <= self.block <= 1.0):
            raise ValueError(
                f"Thresholds must satisfy: safe <= flag <= block <= 1.0. "
                f"Got safe={self.safe}, flag={self.flag}, block={self.block}"
            )


THRESHOLDS = ScoreThresholds()


# ========================
# SPAM THRESHOLDS
# Tunable without touching core logic.
# ========================

@dataclass
class SpamThresholds:
    near_duplicate_similarity: float = 0.85  # 0.85 = aggressive, raise if false positives
    flood_message_count: int = 6             # messages in window before flood trigger
    exact_repeat_count: int = 3             # identical messages before spam trigger

    def __post_init__(self):
        if not (0.0 < self.near_duplicate_similarity <= 1.0):
            raise ValueError("near_duplicate_similarity must be between 0 and 1")
        if self.flood_message_count < 2:
            raise ValueError("flood_message_count must be at least 2")
        if self.exact_repeat_count < 2:
            raise ValueError("exact_repeat_count must be at least 2")


SPAM_THRESHOLDS = SpamThresholds()


# ========================
# LEAF APPEAL CONFIG
# ========================

@dataclass
class AppealConfig:
    max_appeals_per_cycle: int = 2      # appeals allowed per 5 strikes
    strike_cycle: int = 5               # strikes per appeal cycle

    def __post_init__(self):
        if self.max_appeals_per_cycle < 1:
            raise ValueError("max_appeals_per_cycle must be at least 1")
        if self.strike_cycle < 1:
            raise ValueError("strike_cycle must be at least 1")


APPEAL = AppealConfig()


# ========================
# VERIFICATION CONFIG
# ========================

@dataclass
class VerificationConfig:
    captcha_length: int = 6
    captcha_timeout_seconds: int = 300
    otp_length: int = 6

    def __post_init__(self):
        if self.captcha_length < 4:
            raise ValueError("captcha_length must be at least 4 to be effective")
        if self.otp_length < 4:
            raise ValueError("otp_length must be at least 4 to be secure")


VERIFICATION = VerificationConfig()


# ========================
# CHANNEL EXCEPTIONS CONFIG
# ========================

@dataclass
class ExceptionConfig:
    max_partner: int = 3
    max_non_partner: int = 1

    def __post_init__(self):
        if self.max_partner < self.max_non_partner:
            raise ValueError(
                "Partner exception count must be >= non-partner count"
            )


EXCEPTIONS = ExceptionConfig()


# ========================
# IDENTITY
# ========================

BOT_NAME = "Ivy"
BOT_VERSION = "1.0.0"
UPDATE_NOTES = "Ivy is live. Say less."
COMMAND_PREFIX = "."
HEY_IVY_TRIGGERS = ["hey ivy", "hey, ivy", "hello ivy", "ivy,"]


# ========================
# OWNER & PARTNERSHIP
# ========================

KIKUSUKA_ID = 865472673131659264
KIKUSUKA_SERVER_ID = 1456024156003897398


# ========================
# API KEYS
# ========================

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
HF_API_KEY = os.getenv("HF_API_KEY")

# Signs the verification seal on real strike DMs (utils/verification_seal.py)
# — an HMAC secret, not something that needs to be memorable. Generate
# once with: python -c "import secrets; print(secrets.token_hex(32))"
# and keep it in .env alongside the other secrets. If unset, the seal
# feature just doesn't activate (DMs send without a seal line) — it
# degrades gracefully rather than breaking anything.
IVY_SIGNING_SECRET = os.getenv("IVY_SIGNING_SECRET")

# Gemini — used specifically as a resilience backup for Leaf appeal
# conversations, NOT for general moderation traffic. Appeals are
# naturally low-volume (someone has to get a strike, then choose to
# appeal, then converse) so two keys rotated is real headroom, not
# quota-stretching. Keep these as separate projects under one legitimate
# account, not separate accounts created to multiply free-tier quota.
GEMINI_API_KEYS = [
    k for k in [os.getenv("GEMINI_API_KEY_1"), os.getenv("GEMINI_API_KEY_2")]
    if k
]
HF_MODERATION_SPACE_URL = os.getenv("HF_MODERATION_SPACE_URL")
HF_CONVERSATION_SPACE_URL = os.getenv("HF_CONVERSATION_SPACE_URL")
HF_REASONING_SPACE_URL = os.getenv("HF_REASONING_SPACE_URL")  # Qwen 1.5B — rules/scam/politics
HF_CASE_EXPORT_SPACE_URL = os.getenv("HF_CASE_EXPORT_SPACE_URL")  # PDF rendering, offloaded from Wispbyte


# ========================
# GROQ MODEL POOLS
# Heavy models for reasoning, fast models for routine checks.
#
# Rotation is now failure-aware (see core/semantic.py _GroqModelPool)
# — a model that Groq reports as decommissioned gets permanently
# benched and logged once; a model that just rate-limits or times out
# gets a short cooldown, not a permanent ban. This means a dead model
# left in one of these lists can no longer silently eat retries
# forever — worst case it gets caught and benched on first contact.
# That said, don't rely on that as an excuse to leave known-dead
# entries here on purpose; check console.groq.com/docs/deprecations
# periodically, since the detection above is a safety net, not a
# substitute for keeping this list honest.
# ========================

GROQ_HEAVY_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-70b-versatile",
    # NOTE: llama-3.1-70b-versatile has a real deprecation history with
    # Groq — worth double-checking on console.groq.com before shipping.
    # Not removing it blindly since I can't verify live status without
    # web access from here; the pool above will auto-bench it safely
    # if it's actually gone, but confirming directly costs you 30 seconds
    # and saves a wasted retry cycle in production.
]

GROQ_FAST_MODELS = [
    "llama-3.1-8b-instant",
    # Only one entry — "gemma2-9b-it" and "mixtral-8x7b-32768" were
    # confirmed decommissioned in live production logs (2026-07-16).
    # With a single model, rotation has nothing to spread load across;
    # if you get/confirm real current alternatives from Groq's console,
    # add them here — the pool system will handle a wrong guess safely
    # now (auto-bench + log), but a correct list is still better than
    # relying on that safety net.
]

GROQ_VISION_MODELS = [
    "llama-3.2-11b-vision-preview",
]


# ========================
# MODERATION LEVELS
# All validated by Pydantic on startup.
# Bot will crash with a clear error if any value is wrong.
# ========================

_raw_mod_levels = {
    "easy": {
        "description": "Severe slurs and explicit threats only.",
        "spam": True,
        "badwords": "severe_only",
        "grooming": False,
        "scam": False,
        "politics": False,
        "semantic_ai": False,
        "onnx_classifier": False,
    },
    "normal": {
        "description": "Full badword list, scam links, grooming patterns.",
        "spam": True,
        "badwords": "full",
        "grooming": True,
        "scam": True,
        "politics": False,
        "semantic_ai": False,
        "onnx_classifier": False,
    },
    "medium": {
        "description": "Normal plus politics, strike counters, escalating warnings.",
        "spam": True,
        "badwords": "full",
        "grooming": True,
        "scam": True,
        "politics": True,
        "semantic_ai": False,
        "onnx_classifier": False,
    },
    "hard": {
        "description": "Full semantic AI, everything except ONNX classifier.",
        "spam": True,
        "badwords": "full",
        "grooming": True,
        "scam": True,
        "politics": True,
        "semantic_ai": True,
        "onnx_classifier": False,
    },
    "hardcore": {
        "description": "Full AI semantic checking plus the neural classifier when online — falls back to Hard-level coverage otherwise.",
        "spam": True,
        "badwords": "full",
        "grooming": True,
        "scam": True,
        "politics": True,
        "semantic_ai": True,
        "onnx_classifier": True,
    },
}

# Validate all moderation levels at import time
try:
    MOD_LEVELS: dict[str, ModerationConfig] = {
        level: ModerationConfig(**config)
        for level, config in _raw_mod_levels.items()
    }
except ValidationError as e:
    raise SystemExit(
        f"\n❌ Ivy startup failed — invalid moderation config:\n{e}\n"
        f"Fix config.py before running the bot."
    )


# ========================
# IVY PERSONALITY STRINGS
# The soul. Keep these sharp. Keep them her.
# ========================

BREEZY_REDIRECT = (
    "I don't do casual. That's Breezy's job, not mine. Go find it."
)
UNKNOWN_RESPONSE = (
    "I don't know anything about that. Nor do I care to."
)
MODEL_IDENTITY_RESPONSE = "Who knows. 🤔"

SOFT_PRAISE_MESSAGES = [
    "...fine. You got me. Don't tell anyone.",
    "I noticed. Don't make me regret it.",
    "You're making my job boring. Keep it up.",
    "Clean record. Somehow. Don't ruin it.",
    "Against all odds, you're behaving. Noted.",
]

STRIKE_DM_TEMPLATE = (
    "Hey. You just got a strike in **{guild_name}**.\n\n"
    "**What you did**: {reason}\n"
    "**Your current strikes**: {strike_count}\n"
    "**What happens next**: {next_action}\n\n"
    "You have {appeal_hours} hours to open a Leaf appeal if you think "
    "this was wrong. Use `/leaf create` in the server.\n"
    "Don't waste your tries. You get {appeals_left} left this cycle."
)

PUBLIC_STRIKE_DM_CLOSED = (
    "Hey {mention}, your DMs are closed so public humiliation it is. "
    "You just got a strike. **Reason**: {reason}. "
    "Open your DMs and use `/leaf create` if you want to appeal. "
    "You have {appeal_hours} hours. Clock's ticking."
)

APPEAL_WIN_DM = (
    "After further review, your appeal was successful. "
    "The strike has been reversed. "
    "Your Leaf try has been returned.\n\n"
    "Don't make me do this again. 🌿"
)

APPEAL_WIN_PUBLIC = (
    "After further review, {mention} was found to be in the clear. "
    "The strike has been reversed. Ivy acknowledges the error. "
    "Don't get used to it."
)

IGNORED_CASUAL_MESSAGE = (
    "I'll be ignoring you for the next 24 hours. "
    "You know why. Find Breezy."
)

CONFLICT_LEAF_MESSAGE = (
    "Two admins. Conflicting commands. Both cancelled.\n"
    "Sort it out between yourselves. I've logged it. "
    "This Leaf stays open until you do."
)

UNENFORCEABLE_RULE = (
    "I can't enforce this rule. "
    "If it's a server issue, speak to your moderators. "
    "If you think it's a bot problem, find Kikusuka."
)

MOD_REVIEW_PING = (
    "Hey {mod_role}, this message needs a human decision. "
    "It's been sitting here for {minutes} minutes. "
    "Yes or no. That's all I need."
)

ALT_ACCOUNT_FLAG = (
    "Heads up {mod_role} — new account just joined. "
    "Account age under 24 hours. Might be an alt. "
    "Might be fine. Worth a look."
)

MODERATOR_STRIKE_LOG = (
    "For the record — {mod_name} triggered a moderation filter. "
    "No action taken. Logged for owner and senior staff visibility."
)


# ========================
# DATABASE
# ========================

DATABASE_PATH = "ivy.db"


# ========================
# STARTUP VALIDATION
# Runs once on import. Missing critical env vars = clear error.
# ========================

def validate_environment():
    """
    Crashes the process if critical secrets are missing — this is
    intentional for actual bot startup (bot.py should never run
    half-configured). But it made config.py impossible to import for
    ANY other purpose — tests, linters, utility scripts — without full
    production secrets in the environment.

    Set IVY_SKIP_ENV_VALIDATION=1 to import config.py without triggering
    the crash, e.g. for testing or tooling. bot.py never sets this, so
    real bot startup is unaffected — it still fails loud and immediately
    if secrets are missing, exactly as before.
    """
    if os.getenv("IVY_SKIP_ENV_VALIDATION"):
        return

    critical = {
        "DISCORD_TOKEN": DISCORD_TOKEN,
        "GROQ_API_KEY": GROQ_API_KEY,
    }
    missing = [k for k, v in critical.items() if not v]
    if missing:
        raise SystemExit(
            f"\n❌ Ivy startup failed — missing environment variables:\n"
            f"{', '.join(missing)}\n"
            f"Add them to your .env file before running."
        )

    optional_warnings = {
        "HF_API_KEY": HF_API_KEY,
        "HF_MODERATION_SPACE_URL": HF_MODERATION_SPACE_URL,
        "HF_CONVERSATION_SPACE_URL": HF_CONVERSATION_SPACE_URL,
        "HF_REASONING_SPACE_URL": HF_REASONING_SPACE_URL,
        "IVY_SIGNING_SECRET": IVY_SIGNING_SECRET,
    }
    for k, v in optional_warnings.items():
        if not v:
            print(
                f"⚠️  Warning: {k} not set. "
                f"Related features will fall back to Groq."
            )


validate_environment()
