# bot.py
# Ivy Bot - Entry Point
# Ties every module together.
# Starts the bot, registers events, launches background tasks.
# Built by Kikusuka | Part of the Breezy Hub ecosystem.

import asyncio
import logging
import discord
from discord.ext import commands

from config import (
    DISCORD_TOKEN, COMMAND_PREFIX,
    BOT_NAME, BOT_VERSION, UPDATE_NOTES,
    KIKUSUKA_ID,
)
from utils.logger import setup_logging, get_context_logger
from data.db import init_db, get_guild
from data.analytics import daily_rollup_loop, update_member_cache
from core.setup import handle_setup_message, initiate_setup, announce_update
from core.spam import spam_state_cleanup_loop
from core.moderation import process_message, mod_review_escalation_loop
from core.classifier import _LocalONNXClassifier
from leaves.appeal import leaf_expiry_loop, LeafActionsView
from leaves.chat import handle_leaf_message, handle_conflict_leaf
from utils.helpers import is_hey_ivy
from utils.hf_chat import close_session


# ========================
# LOGGING
# ========================

setup_logging(level="INFO")
log = logging.getLogger("ivy.bot")


# ========================
# BOT INTENTS
# ========================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = True


# ========================
# BOT INSTANCE
# ========================

bot = commands.Bot(
    command_prefix=COMMAND_PREFIX,
    intents=intents,
    help_command=None,  # We have our own /help
)

# Registered staff IDs (loaded from DB at startup)
registered_db: list[str] = []


# ========================
# STARTUP
# ========================

@bot.event
async def on_ready():
    log.info(f"🟢 {BOT_NAME} v{BOT_VERSION} online as {bot.user.name} ({bot.user.id})")

    # Register Ivy's own ID as globally trusted so she never
    # accidentally moderates her own messages
    from core.spam import register_trusted_bot
    register_trusted_bot(bot.user.id)

    # Initialize database
    init_db()
    log.info("✅ Database initialized.")

    # Pre-warm local ONNX classifier
    classifier = _LocalONNXClassifier.get()
    classifier.load()

    # Sync slash commands
    try:
        synced = await bot.tree.sync()
        log.info(f"✅ Synced {len(synced)} slash commands.")
    except Exception as e:
        log.warning(f"Slash command sync failed: {e}")

    # Register persistent leaf views for all active leaves
    try:
        from data.db import db_cursor
        with db_cursor() as cursor:
            cursor.execute(
                "SELECT id, channel_id FROM leaves WHERE status IN ('open', 'claimed')"
            )
            active_leaves = cursor.fetchall()
        for leaf in active_leaves:
            bot.add_view(LeafActionsView(
                leaf_id=leaf["id"],
                channel_id=leaf["channel_id"],
            ))
        log.info(f"✅ Registered {len(active_leaves)} persistent leaf views.")
    except Exception as e:
        log.warning(f"Leaf view registration failed: {e}")

    # Update member cache for all guilds
    for guild in bot.guilds:
        try:
            update_member_cache(guild)
        except Exception as e:
            log.warning(f"[guild={guild.id}] Member cache update failed: {e}")

    # Start background tasks
    asyncio.create_task(spam_state_cleanup_loop())
    asyncio.create_task(mod_review_escalation_loop(bot))
    asyncio.create_task(leaf_expiry_loop(bot))
    asyncio.create_task(daily_rollup_loop())
    asyncio.create_task(_member_cache_refresh_loop())
    from core.setup import ghost_account_cleanup_loop
    asyncio.create_task(ghost_account_cleanup_loop(bot))
    log.info("✅ Background tasks started.")

    # Send update announcement to all active guilds
    await announce_update(bot)

    log.info(f"🌿 {BOT_NAME} is fully operational.")


async def _member_cache_refresh_loop():
    """Refreshes member cache every 30 minutes for all guilds."""
    while True:
        await asyncio.sleep(1800)
        for guild in bot.guilds:
            try:
                update_member_cache(guild)
            except Exception as e:
                log.warning(f"[guild={guild.id}] Cache refresh failed: {e}")


# ========================
# GUILD JOIN
# ========================

@bot.event
async def on_guild_join(guild: discord.Guild):
    log.info(f"🏰 Joined new guild: {guild.name} (ID: {guild.id})")
    await initiate_setup(guild, bot)


# ========================
# MEMBER JOIN
# ========================

@bot.event
async def on_member_join(member: discord.Member):
    if member.bot:
        return

    guild = member.guild
    guild_data = get_guild(guild.id)
    if not guild_data or not guild_data["setup_complete"]:
        return

    ctx_log = get_context_logger("ivy.bot", guild_id=guild.id, user_id=member.id)

    # Ensure user record
    from data.db import ensure_user
    ensure_user(member.id, guild.id, member.name, member.display_name)

    # Alt account detection
    from core.spam import check_alt_account
    from utils.embeds import alt_account_embed
    from data.db import get_pending_reviews

    # Check for recent bans to compare join timing
    recent_ban_time = None
    try:
        async for entry in guild.audit_logs(
            action=discord.AuditLogAction.ban, limit=5
        ):
            if (discord.utils.utcnow() - entry.created_at).total_seconds() < 3600:
                recent_ban_time = entry.created_at.replace(tzinfo=None)
                break
    except Exception:
        pass

    alt_result = check_alt_account(
        account_created_at=member.created_at.replace(tzinfo=None),
        has_avatar=member.avatar is not None,
        guild_join_time=discord.utils.utcnow().replace(tzinfo=None),
        recent_ban_time=recent_ban_time,
    )

    if alt_result.is_suspicious:
        log_channel_id = guild_data["logs_channel_id"]
        if log_channel_id:
            log_channel = guild.get_channel(int(log_channel_id))
            if log_channel:
                mod_role_id = guild_data.get("mod_role_id")
                embed = alt_account_embed(
                    member=member,
                    reasons=alt_result.reasons,
                    confidence=alt_result.confidence,
                    mod_role_id=mod_role_id,
                )
                try:
                    await log_channel.send(embed=embed)
                except Exception as e:
                    ctx_log.warning(f"Alt account alert failed: {e}")

    # Assign unverified role if verification is enabled
    verify_channel_id = guild_data.get("verify_channel_id")
    if verify_channel_id:
        unverified_role = discord.utils.get(guild.roles, name="Unverified")
        if unverified_role:
            try:
                await member.add_roles(
                    unverified_role,
                    reason="Ivy: New member — pending verification",
                )
            except Exception as e:
                ctx_log.warning(f"Unverified role assign failed: {e}")

    ctx_log.info(f"Member joined: {member.name}")


# ========================
# MESSAGE HANDLER
# ========================

@bot.event
async def on_message(message: discord.Message):
    # Bot exemption — but NOT universal anymore.
    # Trusted bots (Ivy herself, Breezy, guild allowlist) are skipped.
    # Unknown bots pass through spam and mention-flood checks.
    if message.author.bot:
        if not message.guild:
            return
        from core.spam import should_moderate_bot
        if not should_moderate_bot(message.author, message.guild.id):
            return
        # Unknown bot — fall through to mention-flood check only,
        # skip the full pipeline since bots can't do leaf appeals etc.
        from core.spam import check_mention_flood
        mention_result = check_mention_flood(message.content, is_staff=False)
        if mention_result:
            try:
                await message.delete()
            except Exception:
                pass
            try:
                await message.channel.send(
                    f"🛡️ **Ivy**: Mass mention from an unknown bot blocked. "
                    f"Server admins — check which bot sent this."
                )
            except Exception:
                pass
        return

    # ── DM handling (consent conversations live here) ──
    # This must run BEFORE the guild-only check below, since consent
    # DMs and owner consent replies happen outside any guild context.
    if not message.guild:
        from core.consent import handle_consent_dm_reply
        consumed = await handle_consent_dm_reply(message)
        if consumed:
            return
        return  # Other DM content is not processed by Ivy

    content = message.content.strip()
    guild = message.guild
    guild_data = get_guild(guild.id)

    # ── Owner data-consent reply (post-setup follow-up question) ──
    from core.setup import handle_owner_consent_reply
    if await handle_owner_consent_reply(message):
        return

    # ── Setup flow (intercepts all messages during setup) ──
    if guild_data and not guild_data["setup_complete"]:
        if message.author.id == guild.owner_id:
            await handle_setup_message(message, bot)
        return

    # ── Leaf channel handler ──
    if guild_data:
        from data.db import get_leaf_by_channel
        leaf = get_leaf_by_channel(message.channel.id)
        if leaf:
            await handle_leaf_message(message, leaf["id"])
            return

    # ── Hey Ivy handler ──
    triggered, query = is_hey_ivy(
        content, bot.user.id, message.mentions
    )
    if triggered:
        await _handle_hey_ivy(message, query, guild_data)
        return

    # ── Main moderation pipeline ──
    if guild_data and guild_data["setup_complete"]:
        await process_message(message, bot, registered_db)

    # ── Process prefix commands ──
    await bot.process_commands(message)


# ========================
# HEY IVY HANDLER
# Natural language command router.
# Admins get full command access.
# Regular users get moderation chat only.
# ========================

# Track Hey Ivy casual abuse per user
_casual_strikes: dict[int, list] = {}


# Rate limiting for Hey Ivy admin commands — prevents API budget drain
# and duplicate actions from rapid-fire command spam.
_hey_ivy_last_command: dict[int, float] = {}  # user_id -> unix timestamp
HEY_IVY_COOLDOWN_SECONDS = 3


async def _handle_hey_ivy(
    message: discord.Message,
    query: str,
    guild_data: dict,
):
    from utils.hf_chat import chat as hf_chat, is_ivy_domain
    from config import BREEZY_REDIRECT, IGNORED_CASUAL_MESSAGE, TIMING
    import time

    if not guild_data:
        return

    member = message.author
    uid = member.id

    # Check ignore list
    from data.db import is_user_ignored, ignore_user
    if is_user_ignored(uid, message.guild.id):
        return  # Silently ignore

    # Determine if admin
    is_admin = (
        member.guild_permissions.administrator
        or member.guild_permissions.moderate_members
        or str(uid) in registered_db
    )

    admin_role_id = guild_data.get("admin_role_id")
    if admin_role_id:
        if int(admin_role_id) in [r.id for r in member.roles]:
            is_admin = True

    # Rate limit check — admins only, since this is the path that
    # actually executes commands and costs API calls
    if is_admin:
        now = time.time()
        last = _hey_ivy_last_command.get(uid, 0)
        if now - last < HEY_IVY_COOLDOWN_SECONDS:
            await message.channel.send(
                f"💅 **Ivy**: Slow down. One command at a time. "
                f"Try again in a second.",
                delete_after=5,
            )
            return
        _hey_ivy_last_command[uid] = now

    if not query:
        await message.channel.send(
            f"💅 **Ivy**: Yes? Ask me something useful."
        )
        return

    # Non-admin trying casual chat
    if not is_admin and not is_ivy_domain(query):
        # Track casual abuse
        import time
        now = time.time()
        strikes = _casual_strikes.get(uid, [])
        strikes = [t for t in strikes if now - t < 86400]
        strikes.append(now)
        _casual_strikes[uid] = strikes

        if len(strikes) >= 3:
            ignore_user(uid, message.guild.id, hours=TIMING.casual_ignore_hours)
            await message.channel.send(
                f"💅 **Ivy**: {message.author.mention} {IGNORED_CASUAL_MESSAGE}"
            )
            return

        await message.channel.send(
            f"💅 **Ivy**: {BREEZY_REDIRECT}"
        )
        return

    # Admin command parsing via Groq
    if is_admin:
        conflict_detected = await _check_conflict(message.guild.id, member.id, query)
        if conflict_detected:
            return  # Both commands cancelled, conflict leaf opened
        await _execute_admin_nlp(message, query, guild_data)
        return

    # Non-admin moderation query via HF
    async with message.channel.typing():
        response = await hf_chat(
            query,
            skip_domain_check=True,
        )
    await message.channel.send(f"💅 **Ivy**: {response}")


async def _execute_admin_nlp(
    message: discord.Message,
    query: str,
    guild_data: dict,
):
    """Parses and executes natural language admin commands via Groq."""
    from core.semantic import _groq_call
    import json

    system_prompt = """You are Ivy's NLP command parser for admin commands.
Parse the admin's natural language command into a structured action.

Supported actions: WARN, MUTE, UNMUTE, KICK_RECOMMEND, BAN_RECOMMEND, THANK,
PURGE, ANALYTICS, PROFILE, RULES, LEVEL, CHAT, REVIEW_LIST

For "chat_reply": Ivy's tone is sharp and sassy, but NEVER uses actual
profanity or slurs, even playfully, even as a joke. Sassy means witty,
direct, a little cutting — not vulgar. "That guy's out of here" not
"that guy's ass is out of here." Wit over crudeness, always.

Respond ONLY in JSON:
{
  "action": "ACTION_NAME",
  "target_username": "username or null",
  "target_user_id": "id string or null",
  "reason": "reason or null",
  "minutes": integer or null,
  "amount": integer or null,
  "level": "easy|normal|medium|hard|hardcore or null",
  "chat_reply": "sassy but clean response if action is CHAT, else null"
}"""

    raw = await _groq_call(
        messages=[
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"Admin: {message.author.name} (ID: {message.author.id})\n"
                    f"Guild: {message.guild.name}\n"
                    f"Command: \"{query}\""
                )
            },
        ],
        use_heavy=False,
    )

    if not raw:
        await message.channel.send("⚠️ **Ivy**: Groq is unreachable. Try again.")
        return

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        await message.channel.send("⚠️ **Ivy**: Couldn't parse that command.")
        return

    action = result.get("action", "CHAT")
    chat_reply = result.get("chat_reply")
    reason = result.get("reason") or "Unspecified"

    if action == "CHAT":
        reply = chat_reply or "I have nothing to say to that."
        # Safety net — prompt instructions don't guarantee clean output.
        # If the model slipped anyway, swap to a generic clean line
        # rather than trust the AI's word choice unchecked.
        from core.rules import contains_badword
        if contains_badword(reply):
            reply = "That's out of line even for me to repeat. Handled, moving on."
        await message.channel.send(f"💅 **Ivy**: {reply}")
        return

    if action == "ANALYTICS":
        from data.analytics import build_analytics_embed
        embed, _ = await build_analytics_embed(message.guild)
        await message.channel.send(embed=embed)
        return

    if action == "RULES":
        from core.rules import format_rules_for_embed
        rules_text, count = format_rules_for_embed(message.guild.id)
        embed = discord.Embed(
            title=f"📜 Server Rules ({count})",
            description=rules_text[:4000],
            color=0x34495E,
        )
        await message.channel.send(embed=embed)
        return

    if action == "REVIEW_LIST":
        from data.db import get_pending_reviews
        pending = get_pending_reviews(message.guild.id)
        if not pending:
            await message.channel.send("💅 **Ivy**: No pending reviews. Clean queue.")
            return
        await message.channel.send(
            f"💅 **Ivy**: {len(pending)} pending review(s). "
            f"Use `.ivyreview` to see the full list."
        )
        return

    if action == "PURGE":
        amount = result.get("amount") or 10
        amount = min(max(1, amount), 100)
        try:
            deleted = await message.channel.purge(limit=amount)
            await message.channel.send(
                f"🧹 Purged **{len(deleted)}** messages.",
                delete_after=5,
            )
        except Exception as e:
            await message.channel.send(f"❌ Purge failed: {e}")
        return

    # Actions that need a target member
    from utils.helpers import resolve_member
    target_name = result.get("target_username")
    target_id = result.get("target_user_id")

    target = await resolve_member(
        message.guild,
        target_id or target_name or "",
        [m for m in message.mentions if not m.bot],
    )

    if not target and action not in ("CHAT", "ANALYTICS", "RULES", "PURGE"):
        await message.channel.send(
            f"❌ **Ivy**: Couldn't find `{target_name or target_id}` in this server."
        )
        return

    if action == "WARN":
        from data.db import add_strike, ensure_user, get_user_strikes
        ensure_user(target.id, message.guild.id, target.name, target.display_name)

        # Check for bias before acting
        from core.semantic import check_command_bias
        strikes_hist = [
            {"reason": s["reason"], "timestamp": s["timestamp"]}
            for s in (get_user_strikes(target.id, message.guild.id, limit=5) or [])
        ]
        bias = await check_command_bias(
            "WARN", target.name, reason,
            message.author.name, strikes_hist,
        )
        if not bias.get("justified", True):
            await message.channel.send(
                f"⚠️ **Ivy**: I'm not doing that. {bias.get('concern')}\n"
                f"Other mods have been notified to review this request."
            )
            return

        strike_id = add_strike(
            user_id=target.id, guild_id=message.guild.id,
            reason=f"NLP warn by {message.author.name}: {reason}",
            layer="manual", action_taken="WARN",
            channel_id=message.channel.id,
        )
        from data.db import get_reputation
        rep = get_reputation(target.id, message.guild.id)
        await message.channel.send(
            f"⚠️ **{target.mention}** warned. "
            f"Reason: *{reason}* | "
            f"Rep: ★ {rep['score']}/100 ({rep['strikes']} strikes)"
        )

    elif action == "MUTE":
        minutes = result.get("minutes") or 15
        bias = await check_command_bias(
            "MUTE", target.name, reason, message.author.name, []
        )
        if not bias.get("justified", True):
            await message.channel.send(
                f"⚠️ **Ivy**: {bias.get('concern')} Escalating to mod review."
            )
            return
        try:
            import datetime
            await target.timeout(
                datetime.timedelta(minutes=minutes),
                reason=f"Hey Ivy NLP by {message.author.name}: {reason}",
            )
            await message.channel.send(
                f"🔇 **{target.mention}** muted for {minutes} minutes. Reason: *{reason}*"
            )
        except discord.Forbidden:
            await message.channel.send(f"❌ Can't mute {target.mention} — check hierarchy.")

    elif action == "UNMUTE":
        # This action didn't exist anywhere in the codebase before —
        # not here, not as a dot command. "hey ivy unmute @user" had
        # no valid action to map to, so Groq either hallucinated an
        # action this dispatch chain silently ignored, or picked the
        # closest thing it had (MUTE) and did the opposite of what
        # was asked. Either way it's now a real, first-class action.
        if target.timed_out_until is None:
            await message.channel.send(f"❌ {target.mention} isn't currently muted.")
            return
        try:
            await target.timeout(
                None,
                reason=f"Hey Ivy NLP by {message.author.name}: {reason}",
            )
            await message.channel.send(
                f"🔊 **{target.mention}** unmuted. Reason: *{reason}*"
            )
        except discord.Forbidden:
            await message.channel.send(f"❌ Can't unmute {target.mention} — check hierarchy.")

    elif action in ("KICK_RECOMMEND", "BAN_RECOMMEND"):
        action_word = "kick" if action == "KICK_RECOMMEND" else "ban"
        bias = await check_command_bias(
            action_word.upper(), target.name, reason, message.author.name, []
        )
        if not bias.get("justified", True):
            await message.channel.send(
                f"⚠️ **Ivy**: That {action_word} request looks biased. "
                f"Flagging for other mods to review."
            )
            return
        await message.channel.send(
            f"📋 **Ivy recommends {action_word}ing {target.mention}**\n"
            f"Reason: *{reason}*\n"
            f"I don't auto-{action_word}. A mod needs to make that call manually."
        )

    elif action == "THANK":
        from data.db import add_good_point
        if target.id == message.author.id:
            await message.channel.send("❌ Really? Praising yourself? No.")
            return
        add_good_point(target.id, message.guild.id)
        from data.db import get_reputation
        rep = get_reputation(target.id, message.guild.id)
        await message.channel.send(
            f"🌟 **{target.mention}** credited a good point by {message.author.mention}. "
            f"Rep: ★ {rep['score']}/100"
        )

    elif action == "PROFILE":
        from data.db import get_reputation, get_user_strikes
        rep = get_reputation(target.id, message.guild.id)
        strikes = get_user_strikes(target.id, message.guild.id, limit=3)
        strike_text = "\n".join(
            f"• [{s['timestamp'][:10]}] {s['reason'][:60]}"
            for s in strikes
        ) or "Clean record."
        embed = discord.Embed(
            title=f"📊 {target.display_name}",
            color=0x3498DB,
        )
        embed.add_field(name="Rep", value=f"★ {rep['score']}/100", inline=True)
        embed.add_field(name="Strikes", value=f"`{rep['strikes']}`", inline=True)
        embed.add_field(name="Recent History", value=strike_text, inline=False)
        await message.channel.send(embed=embed)

    elif action == "LEVEL":
        level = result.get("level")
        if level:
            from data.db import update_guild
            update_guild(message.guild.id, mod_level=level)
            await message.channel.send(
                f"✅ Moderation level set to **{level.capitalize()}**."
            )
        else:
            await message.channel.send("❌ Specify a level: easy/normal/medium/hard/hardcore")

    else:
        # Previously: any action Groq returned that wasn't handled above
        # did nothing at all — no error, no reply, no log line. From the
        # admin's side that's indistinguishable from the whole feature
        # being dead, and it's exactly why "hey ivy mute/unmute isn't
        # working" was reported with nothing to go on. Now it's visible
        # and traceable instead of a silent black hole.
        log.warning(
            f"[guild={message.guild.id}] Hey Ivy NLP returned unrecognized "
            f"action '{action}' for command: \"{query}\""
        )
        await message.channel.send(
            f"⚠️ **Ivy**: I parsed that as `{action}`, which isn't something "
            f"I know how to execute yet. Logged for review — try rephrasing, "
            f"or use the equivalent dot command instead."
        )


# ========================
# VERIFICATION HANDLER
# Intercepts messages in the verification channel.
# ========================

@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    """Re-process edited messages through moderation pipeline."""
    if after.author.bot or not after.guild:
        return
    guild_data = get_guild(after.guild.id)
    if guild_data and guild_data["setup_complete"]:
        await process_message(after, bot, registered_db)


# ========================
# MEMBER UPDATE
# Track username changes.
# ========================

@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    if before.name != after.name:
        from data.db import ensure_user
        ensure_user(after.id, after.guild.id, after.name, after.display_name)


# ========================
# GUILD MEMBER CACHE UPDATE
# When someone joins/leaves keep member count accurate.
# ========================

@bot.event
async def on_member_remove(member: discord.Member):
    update_member_cache(member.guild)


# ========================
# LOAD COGS
# ========================

async def load_cogs():
    await bot.load_extension("commands.admin")
    await bot.load_extension("commands.utility")
    log.info("✅ Cogs loaded.")


# ========================
# GRACEFUL SHUTDOWN
# ========================

async def shutdown():
    log.info("🔴 Ivy shutting down...")
    await close_session()
    await bot.close()


# ========================
# CONFLICT DETECTION
# Tracks recent Hey Ivy commands to detect conflicts.
# ========================

_recent_admin_commands: dict[int, tuple[int, str]] = {}
# guild_id -> (admin_id, command_str)


async def _check_conflict(
    guild_id: int,
    admin_id: int,
    command: str,
) -> bool:
    """
    Returns True if a conflicting command was just issued by another admin.
    Opens a conflict leaf and returns True to cancel both commands.
    """
    import time
    now = time.time()

    if guild_id in _recent_admin_commands:
        prev_admin_id, prev_command = _recent_admin_commands[guild_id]
        # If different admin within 3 seconds
        if prev_admin_id != admin_id:
            guild = bot.get_guild(guild_id)
            if guild:
                admin1 = guild.get_member(prev_admin_id)
                admin2 = guild.get_member(admin_id)
                if admin1 and admin2:
                    await handle_conflict_leaf(
                        guild, admin1, admin2,
                        prev_command, command, bot,
                    )
                    del _recent_admin_commands[guild_id]
                    return True

    _recent_admin_commands[guild_id] = (admin_id, command)
    # Auto-clear after 3 seconds
    async def _clear():
        await asyncio.sleep(3)
        _recent_admin_commands.pop(guild_id, None)
    asyncio.create_task(_clear())
    return False


# ========================
# MAIN
# ========================

async def main():
    async with bot:
        await load_cogs()
        await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Ivy stopped by keyboard interrupt.")
