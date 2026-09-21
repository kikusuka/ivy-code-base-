# core/setup.py
# Ivy Bot - Conversational Server Onboarding
# Guides server owners through setup via natural conversation.
# Persistent state — survives restarts mid-onboarding.
# Ivy can create roles and channels if they don't exist.
# Groq understands natural language answers — no rigid input required.

import json
import asyncio
import logging
import yaml
import os
import discord
from dataclasses import dataclass, field
from typing import Optional

from pydantic import BaseModel, ValidationError

from config import (
    BOT_NAME,
    BOT_VERSION,
    UPDATE_NOTES,
    KIKUSUKA_ID,
)
from core.semantic import _groq_call
from core.rules import parse_rules_from_text
from data import db
from data.db import update_guild, get_guild, is_partner

log = logging.getLogger("ivy.setup")


# ========================
# SETUP STEPS
# Each step has a name, what Ivy needs to collect,
# and whether it's mandatory.
# ========================

SETUP_STEPS = [
    "welcome",          # 0 - Ivy introduces herself
    "logs_channel",     # 1 - Where to post mod logs (mandatory)
    "mod_role",         # 2 - Which role is moderator
    "admin_role",       # 3 - Which role gets dot commands
    "mod_level",        # 4 - Easy/Normal/Medium/Hard/Hardcore
    "verification",     # 5 - Do they want verification? Which channel?
    "rules",            # 6 - Paste server rules
    "exceptions",       # 7 - Channel exceptions
    "review",           # 8 - Review before going live
    "complete",         # 9 - Summary and go live
]

STEP_INDEX = {name: i for i, name in enumerate(SETUP_STEPS)}


# ========================
# GROQ INTENT PARSER
# Understands what the owner is saying in natural language.
# Returns structured intent so Ivy can act on it.
# ========================

class _SetupIntent(BaseModel):
    intent: str                         # what the user is trying to do
    channel_id: Optional[int] = None    # mentioned channel
    role_id: Optional[int] = None       # mentioned role
    role_name: Optional[str] = None     # role name if creating new
    mod_level: Optional[str] = None     # easy/normal/medium/hard/hardcore
    wants_verification: Optional[bool] = None
    wants_to_create: Optional[bool] = None  # wants Ivy to create role/channel
    raw_rules_text: Optional[str] = None    # pasted rules text
    channel_count: Optional[int] = None     # number of exception channels
    is_skip: bool = False               # user wants to skip this step
    is_confused: bool = False           # user seems confused
    off_topic: bool = False             # conversation drifted


async def _parse_setup_intent(
    message_content: str,
    current_step: str,
    guild: discord.Guild,
    conversation_history: list[dict],
) -> Optional[_SetupIntent]:
    """
    Uses Groq to understand what the owner is saying during setup.
    Handles natural language, tangents, confusion, and implicit answers.
    """

    # Build guild context for Groq
    channels_text = ", ".join(
        f"#{c.name}(id={c.id})"
        for c in guild.text_channels[:20]
    )
    roles_text = ", ".join(
        f"@{r.name}(id={r.id})"
        for r in guild.roles
        if not r.is_default()
    )[:500]

    history_text = ""
    if conversation_history:
        history_text = "\nRecent conversation:\n" + "\n".join(
            f"  {'Ivy' if m['is_ivy'] else 'Owner'}: {m['content']}"
            for m in conversation_history[-6:]
        )

    system_prompt = f"""You are parsing a Discord server owner's message during Ivy bot setup.
Current setup step: {current_step}
Available channels: {channels_text}
Available roles: {roles_text}{history_text}

Extract the owner's intent and any relevant data from their message.
If they mention a channel by name, find its ID from the available channels list.
If they mention a role by name, find its ID from the available roles list.

Respond ONLY in this exact JSON format:
{{
  "intent": "providing_answer" | "asking_question" | "wants_to_skip" | "confused" | "off_topic" | "wants_ivy_to_create",
  "channel_id": integer or null,
  "role_id": integer or null,
  "role_name": "name if they want a new role created, else null",
  "mod_level": "easy" | "normal" | "medium" | "hard" | "hardcore" | null,
  "wants_verification": true | false | null,
  "wants_to_create": true | false | null,
  "raw_rules_text": "the actual rules text if they pasted rules, else null",
  "channel_count": integer or null,
  "is_skip": boolean,
  "is_confused": boolean,
  "off_topic": boolean
}}"""

    raw = await _groq_call(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f'Owner message: "{message_content}"'},
        ],
        use_heavy=False,
    )

    if not raw:
        return None

    try:
        data = json.loads(raw)
        return _SetupIntent(**data)
    except (json.JSONDecodeError, ValidationError) as e:
        log.warning(f"Setup intent parse error: {e}")
        return None


# ========================
# ROLE CREATOR
# Creates Discord roles with appropriate permissions.
# Called when owner asks Ivy to create roles during setup.
# ========================

async def _create_role(
    guild: discord.Guild,
    role_name: str,
    permissions: discord.Permissions,
    color: discord.Color = discord.Color.default(),
) -> Optional[discord.Role]:
    """Creates a role in the guild. Returns the role or None on failure."""
    try:
        role = await guild.create_role(
            name=role_name,
            permissions=permissions,
            color=color,
            reason="Ivy Setup: Auto-created during onboarding",
        )
        log.info(f"[guild={guild.id}] Created role '{role_name}' (id={role.id})")
        return role
    except Exception as e:
        log.warning(f"[guild={guild.id}] Failed to create role '{role_name}': {e}")
        return None


async def _ensure_muted_role(guild: discord.Guild) -> Optional[discord.Role]:
    """Creates or recovers the Muted role with all channel overrides."""
    muted = discord.utils.get(guild.roles, name="Muted")
    if not muted:
        muted = await _create_role(
            guild,
            "Muted",
            discord.Permissions(
                send_messages=False,
                add_reactions=False,
                speak=False,
            ),
            color=discord.Color.dark_grey(),
        )

    if muted:
        for channel in guild.channels:
            if isinstance(channel, (discord.TextChannel, discord.VoiceChannel, discord.Thread)):
                try:
                    overwrite = channel.overwrites_for(muted)
                    overwrite.send_messages = False
                    overwrite.add_reactions = False
                    if isinstance(channel, discord.VoiceChannel):
                        overwrite.speak = False
                    await channel.set_permissions(
                        muted,
                        overwrite=overwrite,
                        reason="Ivy Setup: Muted role channel lock",
                    )
                except Exception:
                    pass

    return muted


async def _ensure_verified_roles(guild: discord.Guild) -> tuple[Optional[discord.Role], Optional[discord.Role]]:
    """
    Creates Verified and Unverified roles if they don't exist.
    Returns (verified_role, unverified_role).
    """
    verified = discord.utils.get(guild.roles, name="Verified")
    unverified = discord.utils.get(guild.roles, name="Unverified")

    if not verified:
        verified = await _create_role(
            guild,
            "Verified",
            discord.Permissions(
                send_messages=True,
                read_messages=True,
                read_message_history=True,
                add_reactions=True,
            ),
            color=discord.Color.green(),
        )

    if not unverified:
        unverified = await _create_role(
            guild,
            "Unverified",
            discord.Permissions(
                read_messages=False,
                send_messages=False,
            ),
            color=discord.Color.dark_grey(),
        )

    return verified, unverified




async def _check_bot_permissions(
    guild: discord.Guild,
    channel: discord.TextChannel,
    history: list[dict],
) -> bool:
    """
    Checks if Ivy has manage_roles permission and her role
    is high enough in hierarchy to create/assign roles.
    Returns True if safe to proceed, False if not.
    """
    me = guild.me
    if not me.guild_permissions.manage_roles:
        await _send_ivy(channel, history,
            "I don't have **Manage Roles** permission. "
            "Give me that permission first, then we can continue."
        )
        return False

    # Check hierarchy — Ivy's top role must be above what she'll create
    ivy_top = me.top_role.position
    if ivy_top <= 1:
        await _send_ivy(channel, history,
            "My role is too low in the hierarchy to create or manage roles. "
            "Move my role higher in Server Settings → Roles, then come back."
        )
        return False

    return True


async def _assign_role(
    guild: discord.Guild,
    member: discord.Member,
    role: discord.Role,
    reason: str = "Ivy role assignment",
) -> bool:
    """Assigns a role to a member following hierarchy rules."""
    try:
        if role.position >= guild.me.top_role.position:
            log.warning(
                f"[guild={guild.id}] Cannot assign role '{role.name}' "
                f"— above Ivy's hierarchy."
            )
            return False
        await member.add_roles(role, reason=reason)
        return True
    except Exception as e:
        log.warning(f"[guild={guild.id}] Role assign failed: {e}")
        return False


async def _remove_role(
    guild: discord.Guild,
    member: discord.Member,
    role: discord.Role,
    reason: str = "Ivy role removal",
) -> bool:
    """Removes a role from a member following hierarchy rules."""
    try:
        if role.position >= guild.me.top_role.position:
            log.warning(
                f"[guild={guild.id}] Cannot remove role '{role.name}' "
                f"— above Ivy's hierarchy."
            )
            return False
        await member.remove_roles(role, reason=reason)
        return True
    except Exception as e:
        log.warning(f"[guild={guild.id}] Role remove failed: {e}")
        return False

# ========================
# SETUP MESSAGES
# What Ivy says at each step. Sharp, clear, helpful.
# ========================

STEP_MESSAGES = {
    "welcome": lambda g: (
        f"🌿 **{BOT_NAME} v{BOT_VERSION} — Server Setup**\n\n"
        f"Hey. I'm Ivy. I keep servers clean so you don't have to babysit them.\n"
        f"Before I can do anything useful in **{g.name}**, I need a few things from you.\n\n"
        f"Just talk to me normally — tell me what's what and I'll figure it out. "
        f"If I'm confused I'll ask. If you drift off topic I'll wait. "
        f"Let's start with the most important thing:\n\n"
        f"**Where should I send my moderation logs?** "
        f"Tell me a channel name or mention it with #."
    ),
    "logs_channel": (
        "Got it. Now — **which role are your moderators?** "
        "If you don't have one yet, just tell me what to call it and I'll create it."
    ),
    "mod_role": (
        "Good. **Which role should have access to my admin dot commands?** "
        "This is usually your admin role. Same deal — if it doesn't exist yet, name it and I'll make it."
    ),
    "admin_role": (
        "Perfect. Now the important one — **what moderation level do you want?**\n\n"
        "🟢 **Easy** — Only the worst slurs and explicit threats. Almost nothing gets flagged.\n"
        "🔵 **Normal** — Full badword list, scam links, grooming patterns.\n"
        "🟡 **Medium** — Everything above plus politics and escalating strike counters.\n"
        "🟠 **Hard** — Full AI semantic checking. Everything except the neural classifier.\n"
        "🔴 **Hardcore** — Everything in Hard, plus the neural toxicity classifier when "
        "it's online. Falls back cleanly to Hard-level coverage if that layer's ever "
        "unavailable — you're never left with nothing.\n\n"
        "What's it going to be?"
    ),
    "mod_level": (
        "Nice choice. **Do you want a verification system?** "
        "This makes new members solve a captcha and enter an OTP from their DMs before they can chat. "
        "Kills bots dead. Yes or no — and if yes, which channel should the verification message live in?"
    ),
    "verification": (
        "Now the big one. **Paste your server rules here.** "
        "Don't worry about format — just paste them however you have them. "
        "I'll read them, structure them, and figure out what I can and can't enforce automatically.\n\n"
        "If you don't have rules yet, just say so and I'll set up some sensible defaults."
    ),
    "rules": lambda is_partner: (
        f"Rules locked in. Last thing — **do you want any channels on a different moderation level?** "
        f"You get {'3 channels' if is_partner else '1 channel'} that can run on a completely different level from the rest of the server. "
        f"Say no if you want everything the same, or tell me which channels and what level."
    ),
    "exceptions": (
        "That's everything I need. Let me show you what we've set up before I go live."
    ),
}

DEFAULT_RULES_TEXT = """
1. Be respectful to all members. No harassment, insults, or personal attacks.
2. No spam, flooding, or repeated messages.
3. No NSFW content outside designated channels.
4. No advertising or self-promotion without permission.
5. No sharing of personal information of others.
6. No scams, phishing links, or suspicious content.
7. Follow Discord's Terms of Service at all times.
8. No hate speech, slurs, or discriminatory content.
"""


# ========================
# MAIN SETUP HANDLER
# Called on every message during active onboarding.
# Figures out where we are, processes the response,
# and moves to the next step.
# ========================

# In-memory conversation history per guild
# (guild_id -> list of {is_ivy, content})
_setup_conversations: dict[int, list[dict]] = {}


async def handle_setup_message(
    message: discord.Message,
    bot: discord.Client,
) -> bool:
    """
    Main entry point for setup conversation handling.
    Called from on_message when setup is not complete.
    Returns True if message was handled by setup, False otherwise.
    """
    guild = message.guild
    if not guild:
        return False

    # Only owner can drive setup
    if message.author.id != guild.owner_id:
        return False

    guild_data = get_guild(guild.id)
    if not guild_data:
        return False

    if guild_data["setup_complete"]:
        return False

    current_step = SETUP_STEPS[guild_data["setup_step"]]

    # Maintain conversation history
    history = _setup_conversations.setdefault(guild.id, [])
    history.append({"is_ivy": False, "content": message.content})
    if len(history) > 20:
        history.pop(0)

    # Parse what the owner is saying
    intent = await _parse_setup_intent(
        message.content,
        current_step,
        guild,
        history,
    )

    if not intent:
        await _send_ivy(message.channel, history,
            "Something went wrong on my end. Say that again?"
        )
        return True

    # Handle off-topic gracefully — acknowledge and redirect
    if intent.off_topic and not intent.is_confused:
        await _send_ivy(message.channel, history,
            f"Noted. Now where were we — right, **{_step_question(current_step)}** "
            f"Take your time."
        )
        return True

    # Route to step handler
    handler = _STEP_HANDLERS.get(current_step)
    if handler:
        await handler(message, guild, intent, history, bot)

    return True


def _step_question(step: str) -> str:
    """Short reminder of what Ivy needs at this step."""
    questions = {
        "logs_channel": "which channel should I use for logs",
        "mod_role": "which role are your moderators",
        "admin_role": "which role gets admin dot command access",
        "mod_level": "what moderation level you want",
        "verification": "whether you want a verification system",
        "rules": "your server rules",
        "exceptions": "whether you want any channel exceptions",
    }
    return questions.get(step, "where we were")


async def _send_ivy(
    channel: discord.TextChannel,
    history: list[dict],
    message: str,
    embed: discord.Embed = None,
):
    """Sends an Ivy message and records it in conversation history."""
    try:
        await channel.send(message, embed=embed)
        history.append({"is_ivy": True, "content": message})
    except Exception as e:
        log.warning(f"Failed to send setup message: {e}")


# ========================
# STEP HANDLERS
# One async function per setup step.
# Each one processes the owner's response and advances the step.
# ========================

async def _handle_welcome(message, guild, intent, history, bot):
    """Step 0 — Just send the welcome message and advance."""
    await _send_ivy(
        message.channel, history,
        STEP_MESSAGES["welcome"](guild)
    )
    update_guild(guild.id, setup_step=STEP_INDEX["logs_channel"])


async def _handle_logs_channel(message, guild, intent, history, bot):
    """Step 1 — Get the logs channel."""
    channel_id = intent.channel_id

    # Try to find mentioned channel in message directly
    if not channel_id and message.channel_mentions:
        channel_id = message.channel_mentions[0].id

    if not channel_id:
        await _send_ivy(message.channel, history,
            "I couldn't find that channel. "
            "Try mentioning it with # or give me the exact name."
        )
        return

    channel = guild.get_channel(channel_id)
    if not channel:
        await _send_ivy(message.channel, history,
            "That channel doesn't seem to exist. "
            "Point me to an existing one."
        )
        return

    update_guild(guild.id,
        logs_channel_id=channel_id,
        setup_step=STEP_INDEX["mod_role"]
    )
    await _send_ivy(message.channel, history,
        f"✅ Logs going to {channel.mention}. Good.\n\n"
        + STEP_MESSAGES["logs_channel"]
    )


async def _handle_mod_role(message, guild, intent, history, bot):
    """Step 2 — Get or create the moderator role."""
    if not await _check_bot_permissions(guild, message.channel, history):
        return
    role = None

    if intent.role_id:
        role = guild.get_role(intent.role_id)
    elif message.role_mentions:
        role = message.role_mentions[0]
    elif intent.wants_to_create and intent.role_name:
        role = await _create_role(
            guild,
            intent.role_name,
            discord.Permissions(
                moderate_members=True,
                manage_messages=True,
                kick_members=True,
                ban_members=False,
            ),
            color=discord.Color.blue(),
        )
        if role:
            await _send_ivy(message.channel, history,
                f"Created **@{role.name}** with moderator permissions. "
                f"Assign it to your mods when you're ready."
            )

    if not role:
        # No role found and no create request — ask if they want Ivy to handle it
        await _send_ivy(message.channel, history,
            "I couldn't find that role. Want me to create a Moderator role for you? "
            "Just say yes and tell me what to call it, or mention an existing role."
        )
        return

    update_guild(guild.id,
        mod_role_id=role.id,
        setup_step=STEP_INDEX["admin_role"]
    )
    await _send_ivy(message.channel, history,
        f"✅ **@{role.name}** locked in as the mod role.\n\n"
        + STEP_MESSAGES["mod_role"]
    )


async def _handle_admin_role(message, guild, intent, history, bot):
    """Step 3 — Get or create the admin role."""
    if not await _check_bot_permissions(guild, message.channel, history):
        return
    role = None

    if intent.role_id:
        role = guild.get_role(intent.role_id)
    elif message.role_mentions:
        role = message.role_mentions[0]
    elif intent.wants_to_create and intent.role_name:
        role = await _create_role(
            guild,
            intent.role_name,
            discord.Permissions(
                administrator=True,
            ),
            color=discord.Color.gold(),
        )
        if role:
            await _send_ivy(message.channel, history,
                f"Created **@{role.name}** with administrator permissions."
            )

    if not role:
        await _send_ivy(message.channel, history,
            "Couldn't find that role. Want me to create an Admin role? "
            "Or mention an existing one."
        )
        return

    update_guild(guild.id,
        admin_role_id=role.id,
        setup_step=STEP_INDEX["mod_level"]
    )
    await _send_ivy(message.channel, history,
        f"✅ **@{role.name}** gets dot command access.\n\n"
        + STEP_MESSAGES["admin_role"]
    )


async def _handle_mod_level(message, guild, intent, history, bot):
    """Step 4 — Get moderation level."""
    level = intent.mod_level

    # Try to detect level from raw message if Groq missed it
    if not level:
        content_lower = message.content.lower()
        for lvl in ["easy", "normal", "medium", "hard", "hardcore"]:
            if lvl in content_lower:
                level = lvl
                break

    if not level:
        await _send_ivy(message.channel, history,
            "I didn't catch that. Which level — Easy, Normal, Medium, Hard, or Hardcore?"
        )
        return

    update_guild(guild.id,
        mod_level=level,
        setup_step=STEP_INDEX["verification"]
    )
    await _send_ivy(message.channel, history,
        f"✅ Moderation level set to **{level.capitalize()}**.\n\n"
        + STEP_MESSAGES["mod_level"]
    )


async def _handle_verification(message, guild, intent, history, bot):
    """Step 5 — Verification system setup."""
    wants_verification = intent.wants_verification

    # Try to detect from raw message
    if wants_verification is None:
        content_lower = message.content.lower()
        if any(w in content_lower for w in ["yes", "yeah", "yep", "sure", "definitely", "yup"]):
            wants_verification = True
        elif any(w in content_lower for w in ["no", "nah", "nope", "skip", "don't", "dont"]):
            wants_verification = False

    if wants_verification is None:
        await _send_ivy(message.channel, history,
            "Just a yes or no — do you want a verification system for new members?"
        )
        return

    if not wants_verification:
        update_guild(guild.id, setup_step=STEP_INDEX["rules"])
        await _send_ivy(message.channel, history,
            f"✅ No verification system. "
            f"New members can jump straight in.\n\n"
            + STEP_MESSAGES["verification"]
        )
        return

    # They want verification — need a channel
    channel_id = intent.channel_id
    if not channel_id and message.channel_mentions:
        channel_id = message.channel_mentions[0].id

    if not channel_id:
        await _send_ivy(message.channel, history,
            "Got it, verification system it is. "
            "Which channel should I post the verification message in? "
            "Mention it with # or give me the name."
        )
        return

    channel = guild.get_channel(channel_id)
    if not channel:
        await _send_ivy(message.channel, history,
            "Can't find that channel. Try mentioning it with #."
        )
        return

    # Create Verified and Unverified roles
    verified, unverified = await _ensure_verified_roles(guild)

    update_guild(guild.id,
        verify_channel_id=channel_id,
        setup_step=STEP_INDEX["rules"]
    )

    role_text = ""
    if verified and unverified:
        role_text = (
            f"\nI've also created **@Verified** and **@Unverified** roles. "
            f"Make sure unverified members can only see {channel.mention}."
        )

    await _send_ivy(message.channel, history,
        f"✅ Verification system active in {channel.mention}.{role_text}\n\n"
        + STEP_MESSAGES["verification"]
    )


async def _handle_rules(message, guild, intent, history, bot):
    """Step 6 — Parse server rules."""
    content = message.content.strip()
    has_no_rules = any(
        phrase in content.lower()
        for phrase in ["don't have", "dont have", "no rules", "not yet", "don't have rules"]
    )

    rules_text = intent.raw_rules_text or (None if has_no_rules else content)

    if has_no_rules or not rules_text:
        # Use default rules
        rules_text = DEFAULT_RULES_TEXT
        await _send_ivy(message.channel, history,
            "No rules yet? Fair enough. I'll set up some sensible defaults for you. "
            "You can always update them later with `.ivyrules`."
        )

    await _send_ivy(message.channel, history,
        "📋 Reading your rules and structuring them now. One moment..."
    )

    result = await parse_rules_from_text(rules_text, guild.id)

    if not result.success:
        await _send_ivy(message.channel, history,
            f"Something went wrong parsing your rules: {result.error}\n"
            f"Try again or paste them differently."
        )
        return

    partner = is_partner(guild.id)
    update_guild(guild.id, setup_step=STEP_INDEX["exceptions"])

    response = (
        f"✅ Parsed and locked in **{result.rules_saved}** enforceable rules."
    )

    if result.unenforceable:
        response += (
            f"\n\n⚠️ **{len(result.unenforceable)} rule(s) I can't automatically enforce:**\n"
            + "\n".join(f"• {r}" for r in result.unenforceable)
            + "\nYour mods will need to handle these manually."
        )

    if result.notes:
        response += f"\n\n📝 Note: {result.notes}"

    response += f"\n\n" + STEP_MESSAGES["rules"](partner)

    await _send_ivy(message.channel, history, response)


async def _handle_exceptions(message, guild, intent, history, bot):
    """Step 7 — Channel exceptions."""
    content_lower = message.content.lower()
    no_exceptions = any(
        w in content_lower
        for w in ["no", "nah", "nope", "skip", "same", "don't", "dont", "none"]
    )

    if no_exceptions:
        update_guild(guild.id, setup_step=STEP_INDEX["review"])
        await _show_review(message.channel, guild, history)
        return

    # Try to parse exception channels from the message
    channel_mentions = message.channel_mentions
    partner = is_partner(guild.id)
    max_allowed = 3 if partner else 1

    if not channel_mentions:
        await _send_ivy(message.channel, history,
            f"Mention the channel(s) you want on a different level "
            f"(up to {max_allowed} for your server) and tell me what level each should be."
        )
        return

    level = intent.mod_level
    if not level:
        content_lower = message.content.lower()
        for lvl in ["easy", "normal", "medium", "hard", "hardcore"]:
            if lvl in content_lower:
                level = lvl
                break

    if not level:
        await _send_ivy(message.channel, history,
            f"Got the channels. What moderation level should they run on? "
            f"Easy, Normal, Medium, Hard, or Hardcore?"
        )
        return

    saved = 0
    for ch in channel_mentions[:max_allowed]:
        success = db.add_channel_exception(guild.id, ch.id, level)
        if success:
            saved += 1

    update_guild(guild.id, setup_step=STEP_INDEX["review"])
    await _send_ivy(message.channel, history,
        f"✅ Set {saved} channel(s) to **{level.capitalize()}** mode."
    )
    await _show_review(message.channel, guild, history)




async def _show_review(
    channel: discord.TextChannel,
    guild: discord.Guild,
    history: list[dict],
):
    """Shows current setup config for owner to review before going live."""
    guild_data = get_guild(guild.id)
    if not guild_data:
        return

    logs_ch = guild.get_channel(guild_data["logs_channel_id"]) if guild_data["logs_channel_id"] else None
    mod_role = guild.get_role(guild_data["mod_role_id"]) if guild_data["mod_role_id"] else None
    admin_role = guild.get_role(guild_data["admin_role_id"]) if guild_data["admin_role_id"] else None
    verify_ch = guild.get_channel(guild_data["verify_channel_id"]) if guild_data["verify_channel_id"] else None
    rules = db.get_rules(guild.id)
    exceptions = db.get_channel_exceptions(guild.id)

    embed = discord.Embed(
        title="🌿 Setup Review — Does this look right?",
        description="Here's everything before I go live. Say **yes** to confirm or tell me what needs changing.",
        color=0x3498DB,
    )
    embed.add_field(name="📋 Logs Channel", value=logs_ch.mention if logs_ch else "❌ Not set", inline=True)
    embed.add_field(name="🛡️ Mod Role", value=mod_role.mention if mod_role else "None (Ivy handles it)", inline=True)
    embed.add_field(name="⚙️ Admin Role", value=admin_role.mention if admin_role else "❌ Not set", inline=True)
    embed.add_field(name="🔒 Mod Level", value=f"`{guild_data['mod_level'].capitalize()}`", inline=True)
    embed.add_field(name="✅ Verification", value=verify_ch.mention if verify_ch else "Disabled", inline=True)
    embed.add_field(name="📜 Rules", value=f"{len(rules)} enforceable rules", inline=True)

    if exceptions:
        ex_text = "\n".join(f"• <#{e['channel_id']}>: `{e['mod_level']}`" for e in exceptions)
        embed.add_field(name="🔀 Channel Exceptions", value=ex_text, inline=False)

    await _send_ivy(channel, history, "Almost there. Quick review:", embed=embed)

async def _handle_review(message, guild, intent, history, bot):
    """Step 8 — Optional review before going live."""
    content_lower = message.content.lower()
    confirmed = any(
        w in content_lower
        for w in ["yes", "yeah", "yep", "looks good", "good", "confirm",
                  "go", "go ahead", "proceed", "perfect", "correct", "yup"]
    )
    wants_change = any(
        w in content_lower
        for w in ["no", "change", "wrong", "fix", "wait", "hold on", "actually"]
    )

    if wants_change:
        await _send_ivy(message.channel, history,
            "No problem. What do you want to change? "
            "Tell me and I'll fix it before we go live."
        )
        # Drop back to relevant step based on what they say
        return

    if not confirmed:
        await _send_ivy(message.channel, history,
            "Does everything look right? Just say yes to go live, "
            "or tell me what needs changing."
        )
        return

    update_guild(guild.id, setup_step=STEP_INDEX["complete"])
    await _complete_setup(message.channel, guild, history, bot)

async def _complete_setup(
    channel: discord.TextChannel,
    guild: discord.Guild,
    history: list[dict],
    bot: discord.Client,
):
    """Step 8 — Show summary and go live."""
    guild_data = get_guild(guild.id)
    if not guild_data:
        return

    # Build summary embed
    embed = discord.Embed(
        title="🌿 Ivy is Ready",
        description=(
            f"Setup complete for **{guild.name}**. "
            f"I'm active and watching. Don't make me regret it."
        ),
        color=0x2ECC71,
    )

    logs_ch = guild.get_channel(guild_data["logs_channel_id"])
    mod_role = guild.get_role(guild_data["mod_role_id"]) if guild_data["mod_role_id"] else None
    admin_role = guild.get_role(guild_data["admin_role_id"]) if guild_data["admin_role_id"] else None
    verify_ch = guild.get_channel(guild_data["verify_channel_id"]) if guild_data["verify_channel_id"] else None

    embed.add_field(
        name="📋 Logs Channel",
        value=logs_ch.mention if logs_ch else "Not set",
        inline=True,
    )
    embed.add_field(
        name="🛡️ Mod Role",
        value=mod_role.mention if mod_role else "None (Ivy handles it)",
        inline=True,
    )
    embed.add_field(
        name="⚙️ Admin Role",
        value=admin_role.mention if admin_role else "Not set",
        inline=True,
    )
    embed.add_field(
        name="🔒 Moderation Level",
        value=f"`{guild_data['mod_level'].capitalize()}`",
        inline=True,
    )
    embed.add_field(
        name="✅ Verification",
        value=verify_ch.mention if verify_ch else "Disabled",
        inline=True,
    )

    rules = db.get_rules(guild.id)
    embed.add_field(
        name="📜 Rules Loaded",
        value=f"{len(rules)} enforceable rules",
        inline=True,
    )

    exceptions = db.get_channel_exceptions(guild.id)
    if exceptions:
        ex_text = "\n".join(
            f"• <#{e['channel_id']}>: `{e['mod_level']}`"
            for e in exceptions
        )
        embed.add_field(name="🔀 Channel Exceptions", value=ex_text, inline=False)

    embed.set_footer(text=f"Ivy v{BOT_VERSION} • Use .ivyhelp for commands")

    # Mark setup complete
    update_guild(guild.id, setup_complete=1, setup_step=STEP_INDEX["complete"])

    # Create Muted role silently in background
    asyncio.create_task(_ensure_muted_role(guild))

    await _send_ivy(channel, history, "Here's what we set up:", embed=embed)

    # Clear conversation history
    _setup_conversations.pop(guild.id, None)

    # One more thing — ask the owner about the training-data pipeline.
    # This is separate from everything above and never blocks Ivy going
    # live. Asked last, after trust has been built through the whole
    # setup conversation, not buried in the middle of configuration.
    await asyncio.sleep(2)
    await _ask_owner_data_consent(channel, guild)


async def _ask_owner_data_consent(channel: discord.TextChannel, guild: discord.Guild):
    """
    Asks the server owner, once, whether they consent to the training-data
    pipeline existing for this server at all. This is layer one of two —
    individual members separately consent for their own messages later,
    only when Ivy actually takes an action on them specifically.
    Declining this never affects moderation in any way.
    """
    from core.consent import CONSENT_EXPLANATION
    _owner_consent_pending[guild.id] = channel.id

    await channel.send(
        "🌿 One more thing, separate from everything above.\n\n"
        "I want to get better over time by learning from real moderation "
        "decisions — but only with real consent, never assumed.\n\n"
        "**This question is just about whether the option exists in your "
        "server at all.** Even if you say yes, no individual member's "
        "messages are used unless THEY also separately agree, "
        "which I ask them directly, only when it's actually relevant.\n\n"
        "Saying no here doesn't reduce anything Ivy does for you — "
        "moderation works exactly the same either way.\n\n"
        "Do you consent to this option existing in your server? **YES** or **NO**."
    )


# In-memory tracker: guild_id -> channel_id, for owners with a pending
# data-consent question after setup completion.
_owner_consent_pending: dict[int, int] = {}


async def handle_owner_consent_reply(message: discord.Message) -> bool:
    """
    Catches the owner's yes/no reply to the post-setup consent question.
    Called from on_message before other routing. Returns True if consumed.
    """
    guild = message.guild
    if not guild or guild.id not in _owner_consent_pending:
        return False
    if message.author.id != guild.owner_id:
        return False
    if message.channel.id != _owner_consent_pending[guild.id]:
        return False

    content = message.content.strip().lower()
    from data.db import set_guild_data_consent

    if content in ("yes", "y", "yeah", "yep", "sure", "i agree", "agree"):
        set_guild_data_consent(guild.id, message.author.id)
        _owner_consent_pending.pop(guild.id, None)
        await message.channel.send(
            "✅ Recorded. The option now exists for this server. "
            "Individual members still choose for themselves — nothing "
            "happens automatically. Thanks for actually considering this."
        )
        return True
    elif content in ("no", "n", "nope", "decline"):
        _owner_consent_pending.pop(guild.id, None)
        await message.channel.send(
            "✅ Understood. The training pipeline stays off for this server. "
            "Everything else works exactly the same. You can change this "
            "later with `.ivydataconsent enable` if you ever reconsider."
        )
        return True
    else:
        await message.channel.send("Just need a clear **YES** or **NO** here.")
        return True


# ========================
# STEP HANDLER ROUTING TABLE
# ========================

_STEP_HANDLERS = {
    "welcome":      _handle_welcome,
    "logs_channel": _handle_logs_channel,
    "mod_role":     _handle_mod_role,
    "admin_role":   _handle_admin_role,
    "mod_level":    _handle_mod_level,
    "verification": _handle_verification,
    "rules":        _handle_rules,
    "exceptions":   _handle_exceptions,
    "review":       _handle_review,
}


# ========================
# SETUP INITIATOR
# Called when Ivy joins a new guild.
# Creates guild record and sends the welcome message.
# ========================

async def initiate_setup(guild: discord.Guild, bot: discord.Client):
    """
    Called from on_guild_join.
    Creates DB record and sends opening setup message.
    """
    # Find general channel
    general = (
        guild.system_channel
        or next(
            (c for c in guild.text_channels
             if "general" in c.name.lower()
             and c.permissions_for(guild.me).send_messages),
            None
        )
        or next(
            (c for c in guild.text_channels
             if c.permissions_for(guild.me).send_messages),
            None
        )
    )

    if not general:
        log.warning(f"[guild={guild.id}] No accessible channel found for setup.")
        return

    # Create guild record
    db.create_guild(
        guild_id=guild.id,
        owner_id=guild.owner_id,
        general_channel_id=general.id,
    )

    log.info(f"[guild={guild.id}] Initiating setup in #{general.name}")

    history = _setup_conversations.setdefault(guild.id, [])
    await _send_ivy(
        general, history,
        STEP_MESSAGES["welcome"](guild)
    )
    update_guild(guild.id, setup_step=STEP_INDEX["logs_channel"])


# ========================
# UPDATE ANNOUNCEMENT
# Sent once on startup to all fully-set-up guilds.
# Version and update notes are set in config.py.
# ========================

async def announce_update(bot: discord.Client):
    """
    Sends the update announcement to all active guild general channels.
    Called once at startup. Never repeats.
    """
    log.info("Broadcasting update announcement to all guilds...")
    for guild in bot.guilds:
        guild_data = get_guild(guild.id)
        if not guild_data or not guild_data["setup_complete"]:
            continue

        general_id = guild_data["general_channel_id"]
        if not general_id:
            continue

        channel = guild.get_channel(int(general_id))
        if not channel:
            continue

        try:
            embed = discord.Embed(
                title=f"🌿 Ivy v{BOT_VERSION} — Update",
                description=UPDATE_NOTES,
                color=0x2ECC71,
            )
            embed.set_footer(text="Same Ivy. Sharper edges.")
            await channel.send(embed=embed)
            await asyncio.sleep(1)  # Rate limit courtesy
        except Exception as e:
            log.warning(f"[guild={guild.id}] Update announcement failed: {e}")


# ========================
# GHOST ACCOUNT CLEANUP
# Accounts that join, get assigned Unverified, and never complete
# verification sit forever otherwise. After a long grace window with
# zero activity, they're quietly kicked — not banned, since they never
# had real access to begin with. This isn't punitive, it's housekeeping.
#
# Default window: 30 days. Deliberately long — this is about clearing
# genuine ghost accounts (abandoned bots, people who joined and forgot),
# not pressuring slow humans. Configurable per guild if ever needed.
# ========================

GHOST_ACCOUNT_GRACE_DAYS = 30


async def ghost_account_cleanup_loop(bot: discord.Client):
    """
    Background task. Runs once every 24 hours.
    Sweeps all guilds with verification enabled, finds members who still
    hold the Unverified role after the grace period, and removes them.
    """
    while True:
        await asyncio.sleep(86400)  # once per day
        log.info("Running ghost account cleanup sweep...")

        for guild in bot.guilds:
            guild_data = get_guild(guild.id)
            if not guild_data or not guild_data.get("verify_channel_id"):
                continue  # Verification not enabled here, nothing to sweep

            unverified_role = discord.utils.get(guild.roles, name="Unverified")
            if not unverified_role:
                continue

            cutoff = datetime.timedelta(days=GHOST_ACCOUNT_GRACE_DAYS)
            now = datetime.datetime.now(datetime.timezone.utc)
            removed_count = 0

            for member in list(unverified_role.members):
                if member.bot:
                    continue
                joined = member.joined_at
                if not joined:
                    continue
                if now - joined < cutoff:
                    continue  # Still within grace period

                try:
                    await member.kick(
                        reason=(
                            f"Ivy: Never completed verification within "
                            f"{GHOST_ACCOUNT_GRACE_DAYS} days. Ghost account cleanup."
                        )
                    )
                    removed_count += 1
                except discord.Forbidden:
                    log.warning(
                        f"[guild={guild.id}] No permission to kick ghost "
                        f"account {member.id}."
                    )
                except Exception as e:
                    log.warning(
                        f"[guild={guild.id}] Ghost cleanup kick failed for "
                        f"{member.id}: {e}"
                    )

                await asyncio.sleep(1)  # gentle rate limit courtesy

            if removed_count > 0:
                log.info(
                    f"[guild={guild.id}] Ghost cleanup removed {removed_count} "
                    f"unverified account(s) after {GHOST_ACCOUNT_GRACE_DAYS} day grace period."
                )
                # Log to mod channel too, this is worth being visible about
                log_channel_id = guild_data.get("logs_channel_id")
                if log_channel_id:
                    log_channel = guild.get_channel(int(log_channel_id))
                    if log_channel:
                        try:
                            await log_channel.send(
                                f"🧹 Ghost account cleanup: removed **{removed_count}** "
                                f"account(s) that never completed verification within "
                                f"{GHOST_ACCOUNT_GRACE_DAYS} days. Not bans — just housekeeping."
                            )
                        except Exception:
                            pass
