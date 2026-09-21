# core/consent.py
# Ivy Bot - Individual Data Consent System
# Two-layer consent model:
#   1. Server owner consents during setup for the training pipeline
#      to exist for this server at all (see core/setup.py).
#   2. Each individual member separately consents for THEIR OWN
#      messages to be used. No response = excluded, permanently,
#      until they explicitly opt in themselves.
#
# Moderation always applies to everyone regardless of consent status.
# Declining or ignoring this never affects how Ivy moderates a user —
# it only affects whether their data can ever be used for training.

import discord
import asyncio
import logging
import datetime
from typing import Optional

from data.db import (
    has_guild_consent, get_user_consent_status, mark_consent_dm_sent,
    set_user_consent, mark_no_response_excluded, revoke_user_consent,
)
from config import BOT_VERSION

log = logging.getLogger("ivy.consent")


# ========================
# CONSENT TEXT
# Short, plain, honest. No legal jargon dressed up to look official.
# A 13 year old should be able to read this and understand exactly
# what is and isn't happening to their data.
# ========================

CONSENT_EXPLANATION = (
    "Hey. Quick thing before anything else.\n\n"
    "**This is NOT required for me to moderate your server normally.** "
    "Whatever you choose here, I still keep the server safe the same way "
    "for everyone, always. *(Full breakdown of what is and isn't optional "
    "anytime: run `/ivyterms` in the server.)*\n\n"
    "**What I'm actually asking:** can I use messages of yours that I "
    "directly act on (a strike, a flag, an appeal conversation) to help "
    "train and improve my own moderation models in the future?\n\n"
    "**If you say yes:**\n"
    "• Only messages Ivy actually takes action on are used — not your "
    "whole chat history, not casual conversation.\n"
    "• Your username and identity are stripped out before anything is "
    "used for training. The training data is anonymized.\n"
    "• It's never sold, never shared outside Syntropy, never used for "
    "anything except making Ivy better at her actual job.\n"
    "• You can change your mind anytime with `/dataconsent revoke`.\n\n"
    "**If you say no, or don't reply at all:** nothing changes for you. "
    "Your data is simply never used for this, ever, until you say "
    "otherwise yourself.\n\n"
    "Reply **YES** to opt in, or **NO** to opt out. "
    "I'll ask one quick question after to make sure this actually made sense — "
    "not a trick question, just making sure I explained it clearly."
)

COMPREHENSION_QUESTION = (
    "Quick check — in your own words, just so I know I explained this "
    "clearly: **does choosing NO (or not replying) mean Ivy moderates "
    "you differently, or worse, than someone who said YES?**\n\n"
    "Reply with what you think — there's no wrong answer here, "
    "I just want to make sure the explanation actually landed."
)

CONSENT_TIMEOUT_HOURS = 48


# ========================
# DM CONVERSATION STATE
# In-memory tracking of who's mid-conversation right now.
# Cleared on resolution or timeout.
# ========================

_pending_consent_dms: dict[int, dict] = {}  # user_id -> {guild_id, stage, started_at}


# ========================
# TRIGGER
# Called the first time Ivy takes any moderation action on a member
# who hasn't yet answered the individual consent question.
# Never triggered on join — only when there's already a real, concrete
# reason Ivy is relevant to this specific person.
# ========================

async def maybe_request_consent(
    member: discord.Member,
    guild: discord.Guild,
) -> None:
    """
    Checks if this member needs to be asked for data consent, and if so,
    sends the DM. Safe to call repeatedly — does nothing if already asked
    or already answered.
    """
    if member.bot:
        return

    if not has_guild_consent(guild.id):
        return  # Owner hasn't consented to the pipeline existing — nothing to ask

    status = get_user_consent_status(member.id, guild.id)
    if status != "opted_out" or member.id in _pending_consent_dms:
        # Already answered (opted_in/opted_out via explicit reply) or
        # already mid-conversation. Note: "opted_out" here could mean
        # "never asked" since that's the safe default — we distinguish
        # via the asked_at field being null in a real check below.
        pass

    from data.db import db_cursor
    with db_cursor() as cursor:
        cursor.execute(
            "SELECT asked_at FROM user_data_consent WHERE user_id = ? AND guild_id = ?",
            (member.id, guild.id)
        )
        row = cursor.fetchone()

    if row is not None:
        return  # Already asked at some point — don't ask again automatically

    if member.id in _pending_consent_dms:
        return

    await _send_consent_dm(member, guild)


async def _send_consent_dm(member: discord.Member, guild: discord.Guild) -> None:
    """Sends the initial consent explanation DM and starts the timeout clock."""
    try:
        await member.send(
            f"🌿 **Ivy here — quick message about {guild.name}**\n\n"
            + CONSENT_EXPLANATION
        )
    except discord.Forbidden:
        # Can't DM them — treat as no response, mark excluded immediately
        mark_consent_dm_sent(member.id, guild.id)
        mark_no_response_excluded(member.id, guild.id)
        log.info(
            f"[guild={guild.id}] Consent DM blocked for user={member.id} "
            f"(DMs closed) — defaulted to excluded."
        )
        return
    except Exception as e:
        log.warning(f"[guild={guild.id}] Consent DM send failed for user={member.id}: {e}")
        return

    mark_consent_dm_sent(member.id, guild.id)
    _pending_consent_dms[member.id] = {
        "guild_id": guild.id,
        "stage": "awaiting_choice",
        "started_at": datetime.datetime.utcnow(),
    }

    log.info(f"[guild={guild.id}] Consent DM sent to user={member.id}")

    # Start the timeout watcher for this specific user
    asyncio.create_task(_consent_timeout_watcher(member.id, guild.id))


async def _consent_timeout_watcher(user_id: int, guild_id: int) -> None:
    """If no response within the timeout window, marks as excluded."""
    await asyncio.sleep(CONSENT_TIMEOUT_HOURS * 3600)

    state = _pending_consent_dms.get(user_id)
    if not state or state["guild_id"] != guild_id:
        return  # Already resolved

    mark_no_response_excluded(user_id, guild_id)
    _pending_consent_dms.pop(user_id, None)
    log.info(
        f"[guild={guild_id}] Consent timeout reached for user={user_id} "
        f"— no response, defaulted to excluded."
    )


# ========================
# DM REPLY HANDLER
# Called from on_message when a DM arrives from a user with a
# pending consent conversation.
# ========================

async def handle_consent_dm_reply(message: discord.Message) -> bool:
    """
    Handles a DM reply during an active consent conversation.
    Returns True if the message was consumed by this flow, False otherwise.
    """
    user_id = message.author.id
    state = _pending_consent_dms.get(user_id)
    if not state:
        return False

    guild_id = state["guild_id"]
    content = message.content.strip().lower()

    if state["stage"] == "awaiting_choice":
        if content in ("yes", "y", "yeah", "yep", "sure", "i agree", "agree"):
            state["stage"] = "awaiting_comprehension"
            state["pending_choice"] = True
            await message.channel.send(COMPREHENSION_QUESTION)
            return True

        elif content in ("no", "n", "nope", "decline", "i decline"):
            state["stage"] = "awaiting_comprehension"
            state["pending_choice"] = False
            await message.channel.send(COMPREHENSION_QUESTION)
            return True

        else:
            await message.channel.send(
                "Just need a clear **YES** or **NO** here. "
                "Take your time, no rush."
            )
            return True

    elif state["stage"] == "awaiting_comprehension":
        # Check if their answer demonstrates correct understanding —
        # the correct understanding is: NO/no-response does NOT change
        # moderation treatment in any way.
        understood = _check_comprehension(content)

        opted_in = state.get("pending_choice", False)
        set_user_consent(user_id, guild_id, opted_in, comprehension_passed=understood)

        _pending_consent_dms.pop(user_id, None)

        if not understood:
            await message.channel.send(
                "Just to be fully clear, since your answer made me want to "
                "double-check: **no, choosing NO never changes how Ivy "
                "moderates you.** Moderation is the same for everyone, "
                "always, no matter what you choose here.\n\n"
                f"Your choice (**{'YES' if opted_in else 'NO'}**) has been "
                f"recorded. You can change it anytime with `/dataconsent`."
            )
        else:
            choice_text = "opted in" if opted_in else "opted out"
            await message.channel.send(
                f"Got it — you've {choice_text}. Recorded. "
                f"Change your mind anytime with `/dataconsent`. "
                f"Thanks for actually reading this. 🌿"
            )

        log.info(
            f"[guild={guild_id}] Consent resolved for user={user_id} — "
            f"opted_in={opted_in}, comprehension_passed={understood}"
        )
        return True

    return False


def _check_comprehension(answer: str) -> bool:
    """
    Loose check for whether the user's free-text answer demonstrates
    they understood that declining doesn't affect moderation treatment.
    Looks for negative framing ("no", "doesn't", "same", "not different")
    rather than requiring exact wording.
    """
    positive_understanding_signals = [
        "no", "doesn't", "does not", "same", "not different", "not worse",
        "no difference", "treated same", "treated the same", "nothing changes",
        "same way", "no change",
    ]
    return any(signal in answer for signal in positive_understanding_signals)


# ========================
# SLASH COMMAND HELPERS
# Used by /dataconsent in commands/utility.py
# ========================

def get_consent_summary(user_id: int, guild_id: int) -> dict:
    """Returns current consent state for display in /dataconsent status."""
    status = get_user_consent_status(user_id, guild_id)
    guild_ready = has_guild_consent(guild_id)
    return {
        "individual_status": status,
        "guild_pipeline_active": guild_ready,
        "effectively_used_for_training": guild_ready and status == "opted_in",
    }


async def manual_opt_in(user_id: int, guild_id: int) -> None:
    """User explicitly opts in via /dataconsent, bypassing the DM flow."""
    set_user_consent(user_id, guild_id, opted_in=True, comprehension_passed=True)


async def manual_opt_out(user_id: int, guild_id: int) -> None:
    """User explicitly opts out or revokes via /dataconsent."""
    revoke_user_consent(user_id, guild_id)
