# core/moderation.py
# Ivy Bot - Master Moderation Pipeline
# Runs on every single message.
# Wires all three layers together in priority order.
# Fast path first, expensive path last.
# Never blocks the event loop.

import discord
import asyncio
import logging
import datetime
from dataclasses import dataclass
from typing import Optional

from config import MOD_LEVELS, THRESHOLDS, TIMING, SOFT_PRAISE_MESSAGES
from core.spam import check_spam, check_alt_account, check_mention_flood, SpamResult
from core.classifier import classify, ClassifierResult
from core.semantic import run_semantic_scan, SemanticResult
from data import db
from data.analytics import track_message
from data.db import (
    get_guild, get_user, ensure_user, add_strike,
    get_reputation, update_user, get_effective_mod_level,
    create_mod_review, is_user_ignored,
)
from utils.logger import get_context_logger

log = logging.getLogger("ivy.moderation")


# ========================
# PIPELINE RESULT
# What the pipeline returns after processing a message.
# ========================

@dataclass
class PipelineResult:
    action: str                         # "block" | "flag" | "pass" | "skip"
    layer: str                          # "spam" | "badword" | "classifier" | "semantic" | "skip"
    reason: str = ""
    score: float = 0.0
    recommended_action: str = "WARN"    # WARN | MUTE | KICK | BAN
    category: str = "safe"
    rule_slug: Optional[str] = None
    sassy_reason: Optional[str] = None  # Ivy's voice explanation


# ========================
# STAFF CHECK
# Mods and admins get logged but never blocked.
# ========================

def _is_staff(member: discord.Member, guild_data: dict, registered_db: list) -> bool:
    if member.guild_permissions.administrator:
        return True
    if member.guild_permissions.moderate_members:
        return True
    mod_role_id = guild_data.get("mod_role_id")
    admin_role_id = guild_data.get("admin_role_id")
    member_role_ids = [r.id for r in member.roles]
    if mod_role_id and int(mod_role_id) in member_role_ids:
        return True
    if admin_role_id and int(admin_role_id) in member_role_ids:
        return True
    if str(member.id) in registered_db:
        return True
    return False


# ========================
# DM SYSTEM
# Sends strike DMs to users after every action.
# Falls back to public channel ping if DMs closed.
# ========================

async def _send_strike_dm(
    member: discord.Member,
    channel: discord.TextChannel,
    guild_name: str,
    guild_id: int,
    strike_id: int,
    reason: str,
    strike_count: int,
    appeals_left: int,
):
    """Sends strike explanation DM. Falls back to channel ping if DMs closed."""
    from config import STRIKE_DM_TEMPLATE, PUBLIC_STRIKE_DM_CLOSED, APPEAL
    from utils.verification_seal import generate_seal

    next_action = "continued warnings" if strike_count < 3 else "potential mute or escalation"

    dm_text = STRIKE_DM_TEMPLATE.format(
        guild_name=guild_name,
        reason=reason,
        strike_count=strike_count,
        next_action=next_action,
        appeal_hours=TIMING.appeal_window_hours,
        appeals_left=appeals_left,
    )

    # Seal is None if IVY_SIGNING_SECRET isn't configured — degrades
    # gracefully by just omitting the line, doesn't break the DM.
    seal = generate_seal(strike_id, member.id, guild_id)
    seal_line = (
        f"\n\n🔏 Verification seal: `{seal}` — run `/verify {seal}` "
        f"anytime to confirm this genuinely came from me, not an "
        f"impersonator." if seal else ""
    )
    dm_text += seal_line

    try:
        await member.send(dm_text)
    except discord.Forbidden:
        # DMs closed — public ping
        public_text = PUBLIC_STRIKE_DM_CLOSED.format(
            mention=member.mention,
            reason=reason,
            appeal_hours=TIMING.appeal_window_hours,
        )
        public_text += seal_line
        try:
            await channel.send(public_text, delete_after=30)
        except Exception as e:
            log.warning(f"Failed to send public strike notice: {e}")
    except Exception as e:
        log.warning(f"Strike DM failed for {member.id}: {e}")


# ========================
# LOG TO MOD CHANNEL
# Posts moderation events to the guild's logs channel.
# ========================

async def _send_log(
    guild: discord.Guild,
    embed: discord.Embed,
    guild_data: dict,
):
    """Sends embed to the configured logs channel."""
    log_channel_id = guild_data.get("logs_channel_id")
    if not log_channel_id:
        return
    channel = guild.get_channel(int(log_channel_id))
    if channel:
        try:
            await channel.send(embed=embed)
        except Exception as e:
            log.warning(f"[guild={guild.id}] Log send failed: {e}")


# ========================
# ACTION EXECUTOR
# Takes a pipeline result and executes the Discord action.
# Warns, mutes, logs. Never kicks or bans automatically.
# ========================

async def _execute_action(
    message: discord.Message,
    result: PipelineResult,
    guild_data: dict,
    is_staff_member: bool,
):
    """
    Executes moderation action based on pipeline result.

    Staff members: log only, never act.
    Block: delete message, add strike, warn/mute based on history.
    Flag: log to mod channel for human review, keep message.
    Pass: do nothing.
    """
    member = message.author
    guild = message.guild
    uid = str(member.id)

    ctx_log = get_context_logger(
        "ivy.moderation",
        guild_id=guild.id,
        user_id=member.id,
        channel_id=message.channel.id,
    )

    # Staff bypass — log only
    if is_staff_member:
        if result.action in ("block", "flag"):
            embed = discord.Embed(
                title=f"👁️ Staff Activity Logged [{result.layer.upper()}]",
                color=0x3498DB,
            )
            embed.add_field(name="Member", value=f"{member.mention} (`{member.name}`)", inline=True)
            embed.add_field(name="Layer", value=f"`{result.layer}`", inline=True)
            embed.add_field(name="Score", value=f"`{result.score:.2f}`", inline=True)
            embed.add_field(name="Reason", value=result.reason[:500], inline=False)
            embed.add_field(name="Message", value=f"||{message.content[:300]}||", inline=False)
            embed.add_field(name="Note", value="No action taken — staff clearance.", inline=False)
            embed.set_footer(text="Ivy Moderation Log")
            await _send_log(guild, embed, guild_data)
            ctx_log.info(f"Staff bypass — {result.layer} triggered by {member.name}")
        return

    # FLAG — post to mod review queue, don't delete
    if result.action == "flag":
        review_id = create_mod_review(
            guild_id=guild.id,
            user_id=member.id,
            channel_id=message.channel.id,
            message_content=message.content,
            score=result.score,
            message_id=message.id,
            reason=result.reason,
        )
        db.track_event(guild.id, "mod_reviews_total")

        embed = discord.Embed(
            title="⚠️ Message Flagged for Review",
            color=0xF39C12,
        )
        embed.add_field(name="Member", value=f"{member.mention} (`{member.name}`)", inline=True)
        embed.add_field(name="Score", value=f"`{result.score:.2f}`", inline=True)
        embed.add_field(name="Layer", value=f"`{result.layer}`", inline=True)
        embed.add_field(name="Reason", value=result.reason[:500], inline=False)
        embed.add_field(name="Message", value=f"||{message.content[:300]}||", inline=False)
        embed.add_field(
            name="Jump",
            value=f"[Go to message]({message.jump_url})",
            inline=False,
        )
        embed.add_field(
            name="Action",
            value=f"Review ID: `{review_id}` — use `.ivyreview {review_id} approve/reject`",
            inline=False,
        )
        embed.set_footer(text="Ivy Mod Review Queue")

        log_channel_id = guild_data.get("logs_channel_id")
        if log_channel_id:
            mod_role_id = guild_data.get("mod_role_id")
            log_channel = guild.get_channel(int(log_channel_id))
            if log_channel:
                mod_mention = f"<@&{mod_role_id}>" if mod_role_id else ""
                try:
                    await log_channel.send(
                        content=f"{mod_mention} Message flagged for review.",
                        embed=embed,
                    )
                except Exception as e:
                    ctx_log.warning(f"Flag log send failed: {e}")

        ctx_log.info(f"Message flagged — review_id={review_id} score={result.score:.2f}")
        return

    # BLOCK — delete, strike, warn/mute
    if result.action == "block":
        # Delete message
        try:
            await message.delete()
        except Exception as e:
            ctx_log.warning(f"Message delete failed: {e}")

        # Add strike to DB
        strike_id = add_strike(
            user_id=member.id,
            guild_id=guild.id,
            reason=result.reason,
            layer=result.layer,
            action_taken="WARN",
            channel_id=message.channel.id,
            message_content=message.content[:500],
            score=result.score,
        )

        # Get updated reputation
        rep = get_reputation(member.id, guild.id)
        strike_count = rep["strike_count"]
        cycle_strikes = rep["cycle_strikes"]
        appeals_used = rep.get("appeals_used", 0)

        # Calculate appeals left this cycle
        from config import APPEAL
        cycle = (cycle_strikes // APPEAL.strike_cycle)
        max_appeals = (cycle + 1) * APPEAL.max_appeals_per_cycle
        appeals_left = max(0, max_appeals - appeals_used)

        # Determine Discord action based on strike count
        discord_action = "warned"
        if strike_count >= 4:
            # Recommend ban — don't auto-ban
            discord_action = "at ban threshold"
        elif strike_count >= 3 or result.recommended_action == "MUTE":
            try:
                await member.timeout(
                    datetime.timedelta(minutes=15),
                    reason=f"Ivy Auto-Mod: {result.reason[:100]}"
                )
                discord_action = "muted (15 min)"
                update_user(member.id, guild.id, current_status="muted")
            except Exception as e:
                ctx_log.warning(f"Timeout failed: {e}")
        else:
            update_user(member.id, guild.id, current_status="warned")

        # Post announcement in violation channel
        sassy = result.sassy_reason or result.reason
        announcement = (
            f"💅 **Ivy Active Shield**\n"
            f"{member.mention} — {sassy}\n\n"
            f"• **Layer**: `{result.layer}` | **Action**: {discord_action}\n"
            f"• **Strikes**: `{strike_count}` | **Rep**: `{rep['score']}/100`\n"
            f"• You have `{appeals_left}` appeal(s) left. Use `/leaf create` within "
            f"{TIMING.appeal_window_hours}h to dispute."
        )
        try:
            await message.channel.send(announcement)
        except Exception as e:
            ctx_log.warning(f"Announcement send failed: {e}")

        # Log to mod channel
        embed = discord.Embed(
            title=f"🛡️ Message Blocked [{result.layer.upper()}]",
            color=0xE74C3C,
        )
        embed.add_field(name="Member", value=f"{member.mention} (`{member.name}`)", inline=True)
        embed.add_field(name="Action", value=discord_action, inline=True)
        embed.add_field(name="Score", value=f"`{result.score:.2f}`", inline=True)
        embed.add_field(name="Reason", value=result.reason[:500], inline=False)
        embed.add_field(name="Message", value=f"||{message.content[:300]}||", inline=False)
        embed.add_field(
            name="Rep After Strike",
            value=f"★ {rep['score']}/100 ({strike_count} strikes)",
            inline=True,
        )
        if strike_count >= 3:
            embed.add_field(
                name="⚠️ Recommendation",
                value=f"User has {strike_count} strikes. Consider manual review.",
                inline=False,
            )
        embed.set_footer(text=f"Strike ID: {strike_id}")
        await _send_log(guild, embed, guild_data)

        # DM the user
        await _send_strike_dm(
            member, message.channel, guild.name, guild.id, strike_id,
            result.reason, strike_count, appeals_left,
        )

        # First real moderation action on this member — this is the
        # concrete, relevant moment to ask about training-data consent,
        # not a cold DM on join. Fire-and-forget, never blocks moderation.
        from core.consent import maybe_request_consent
        asyncio.create_task(maybe_request_consent(member, guild))

        ctx_log.info(
            f"Message blocked — layer={result.layer} "
            f"score={result.score:.2f} strikes={strike_count}"
        )


# ========================
# SOFT PRAISE SYSTEM
# Occasionally sends a quiet positive DM to users
# who've been clean for 30+ days.
# The one soft moment Ivy allows herself.
# ========================

async def _check_milestone(member: discord.Member, guild_id: int):
    """Checks if user deserves a quiet milestone acknowledgment."""
    import random
    user = get_user(member.id, guild_id)
    if not user:
        return
    if user["strike_count"] > 0:
        return

    # Check if they've been around 30+ days with zero strikes
    try:
        joined = user["joined_at"]
    except (IndexError, KeyError):
        joined = ""
    if not joined:
        return
    try:
        joined_dt = datetime.datetime.strptime(joined, "%Y-%m-%dT%H:%M:%S")
        days_clean = (datetime.datetime.utcnow() - joined_dt).days
        if days_clean == 30 and user["good_points"] >= 0:
            msg = random.choice(SOFT_PRAISE_MESSAGES)
            try:
                await member.send(f"🌿 {msg}")
            except Exception:
                pass
    except Exception:
        pass


# ========================
# MAIN PIPELINE
# Entry point called from on_message.
# ========================

async def process_message(
    message: discord.Message,
    bot: discord.Client,
    registered_db: list,
) -> PipelineResult:
    """
    Main moderation pipeline. Called on every message.

    Layer order:
    0. Guards (bot, DM, setup incomplete)
    1. Spam detection (local, instant)
    2. Badword list check (local, instant)
    3. ONNX classifier (HF Space, fast)
    4. Groq semantic scan (expensive, last resort)

    Each layer only runs if the previous didn't catch it.
    Returns PipelineResult describing what happened.
    """
    # Guard: bots — but only TRUSTED bots get a free pass.
    # Unknown/untrusted bot accounts still go through moderation,
    # since a malicious or compromised bot is just as capable of
    # harm as a malicious human account.
    if message.author.bot:
        from config import KIKUSUKA_ID
        trusted_bot_names = {"breezy", "ivy"}  # case-insensitive name match
        is_trusted = (
            message.author.id == bot.user.id  # Ivy herself
            or message.author.name.lower() in trusted_bot_names
        )
        if is_trusted:
            return PipelineResult(action="skip", layer="skip", reason="Trusted bot message")
        # Untrusted bot — fall through to normal moderation below.

    # Guard: DMs
    if not message.guild:
        return PipelineResult(action="skip", layer="skip", reason="DM message")

    guild = message.guild
    member = message.author
    content = message.content.strip()

    # Guard: empty content with no attachments
    if not content and not message.attachments:
        return PipelineResult(action="skip", layer="skip", reason="Empty message")

    ctx_log = get_context_logger(
        "ivy.moderation",
        guild_id=guild.id,
        user_id=member.id,
        channel_id=message.channel.id,
    )

    # Load guild config
    guild_data = get_guild(guild.id)
    if not guild_data or not guild_data["setup_complete"]:
        return PipelineResult(action="skip", layer="skip", reason="Setup incomplete")

    # Track analytics
    track_message(guild.id, message.channel.id, member.id)

    # Ensure user record exists
    ensure_user(
        member.id, guild.id,
        username=member.name,
        display_name=member.display_name,
    )

    # Get effective mod level for this channel
    mod_level_key = get_effective_mod_level(guild.id, message.channel.id)
    mod_config = MOD_LEVELS.get(mod_level_key)
    if not mod_config:
        return PipelineResult(action="skip", layer="skip", reason="Invalid mod level")

    # Convert Pydantic model to dict for config checks
    mod_cfg = mod_config.dict()

    # Staff check
    is_staff_member = _is_staff(member, dict(guild_data), registered_db)

    # Milestone check (async, non-blocking, fire and forget)
    asyncio.create_task(_check_milestone(member, guild.id))

    # ========================
    # LAYER 0: MENTION FLOOD (highest priority — runs before everything)
    # @everyone/@here abuse is instant, maximum-severity damage
    # regardless of message content. Checked first, always.
    # ========================
    has_mention_perm = member.guild_permissions.mention_everyone
    if check_mention_flood(content, has_mention_perm):
        db.track_event(guild.id, "spam_blocked")
        result = PipelineResult(
            action="block",
            layer="mention_flood",
            reason="Unauthorized mass mention (@everyone/@here) detected.",
            score=1.0,
            recommended_action="MUTE",
            category="spam",
            sassy_reason=(
                "Mass pinging the whole server? Bold. Also deleted, "
                "and also exactly why you don't have that permission."
            ),
        )
        await _execute_action(message, result, dict(guild_data), is_staff_member)
        return result

    # ========================
    # LAYER 1: SPAM DETECTION
    # ========================
    if mod_cfg.get("spam"):
        spam_result = check_spam(member.id, guild.id, content)
        if spam_result.is_spam:
            db.track_event(guild.id, "spam_blocked")
            result = PipelineResult(
                action="block",
                layer="spam",
                reason=spam_result.reason or "Spam detected.",
                score=1.0,
                recommended_action="MUTE",
                category="spam",
                sassy_reason=(
                    f"Really? {spam_result.reason} "
                    f"Copy-paste machine goes quiet now."
                ),
            )
            await _execute_action(message, result, dict(guild_data), is_staff_member)
            return result

        # Mention flood check — runs alongside spam, not inside it,
        # because the harm mechanism is different: the ping is the damage
        # regardless of whether the surrounding text looks like spam
        from core.spam import check_mention_flood
        mention_result = check_mention_flood(content, is_staff_member)
        if mention_result:
            db.track_event(guild.id, "spam_blocked")
            result = PipelineResult(
                action="block",
                layer="spam",
                reason=mention_result.reason or "Mass mention detected.",
                score=1.0,
                recommended_action="MUTE",
                category="spam",
                sassy_reason=(
                    "Mass pinging everyone? Bold choice. "
                    "Message deleted. Try using your words instead."
                ),
            )
            await _execute_action(message, result, dict(guild_data), is_staff_member)
            return result

    # ========================
    # LAYER 1B: BADWORD LIST
    # ========================
    if mod_cfg.get("badwords"):
        from core.rules import _check_badwords
        bad_match = _check_badwords(content, mod_cfg["badwords"])
        if bad_match:
            db.track_event(guild.id, "badword_triggers")
            result = PipelineResult(
                action="block",
                layer="badword",
                reason=f"Blocked term detected: '{bad_match}'",
                score=1.0,
                recommended_action="WARN",
                category="slur",
                sassy_reason=(
                    f"Bold choice using `{bad_match}` here. "
                    f"Message deleted. Try words that don't get you flagged."
                ),
            )
            await _execute_action(message, result, dict(guild_data), is_staff_member)
            return result

    # ========================
    # LAYER 1C: PARTNER-EXCLUSIVE CUSTOM RULE KEYWORDS
    # Runs at EVERY tier, but only for partner servers. Non-partner
    # servers only get custom rule enforcement on Hard/Hardcore via
    # the semantic layer below. This is instant local keyword matching,
    # zero AI cost, against rules the owner explicitly typed in.
    # ========================
    if db.is_partner(guild.id):
        from core.rules import check_partner_custom_rules
        custom_match = check_partner_custom_rules(content, guild.id)
        if custom_match:
            db.track_event(guild.id, "badword_triggers")
            result = PipelineResult(
                action="block",
                layer="custom_rule",
                reason=(
                    f"Violated server rule '{custom_match['name']}' "
                    f"(matched: '{custom_match['matched_keyword']}')"
                ),
                score=1.0,
                recommended_action=custom_match["action"],
                category="rule",
                rule_slug=custom_match["slug"],
                sassy_reason=(
                    f"That broke this server's own rule, '{custom_match['name']}'. "
                    f"Not even my call — your owner wrote that one."
                ),
            )
            await _execute_action(message, result, dict(guild_data), is_staff_member)
            return result

    # ========================
    # LAYER 2: ONNX CLASSIFIER
    # Only runs on Hardcore or if content has suspicious signals
    # ========================
    ai_enabled = guild_data["setup_complete"]  # AI layers only when fully set up

    if ai_enabled and mod_cfg.get("onnx_classifier"):
        # Quick suspicious signal check before spending API call
        suspicious = _has_suspicious_signals(content)
        if suspicious:
            # Typing indicator covers Space cold-start latency —
            # a cold HF Space can take several seconds to wake up,
            # and silence during that gap reads as Ivy being broken.
            # A visible "thinking" signal keeps the channel feeling alive.
            async with message.channel.typing():
                classifier_result = await classify(content)
            if classifier_result:
                db.update_avg_toxicity(guild.id, classifier_result.score)
                if classifier_result.action in ("block", "flag"):
                    db.track_event(guild.id, "toxic_blocked")
                    result = PipelineResult(
                        action=classifier_result.action,
                        layer="onnx",
                        reason=(
                            f"Neural classifier: {classifier_result.score*100:.1f}% "
                            f"toxicity detected."
                        ),
                        score=classifier_result.score,
                        recommended_action="WARN",
                        category="toxicity",
                        sassy_reason=(
                            f"My neural net flagged that at "
                            f"{classifier_result.score*100:.1f}% toxic. "
                            f"Impressive. Not in a good way."
                        ),
                    )
                    await _execute_action(message, result, dict(guild_data), is_staff_member)
                    return result

    # ========================
    # LAYER 3: GROQ SEMANTIC SCAN
    # Most expensive. Only runs on Hard/Hardcore or when
    # classifier was uncertain.
    # ========================
    if ai_enabled and mod_cfg.get("semantic_ai"):
        # Collect image data if attachments present
        image_data = None
        if message.attachments:
            for att in message.attachments:
                is_image = (
                    att.content_type and att.content_type.startswith("image/")
                ) or any(
                    att.filename.lower().endswith(ext)
                    for ext in [".png", ".jpg", ".jpeg", ".webp", ".gif"]
                )
                if is_image and att.size < 5 * 1024 * 1024:
                    try:
                        img_bytes = await att.read()
                        image_data = (img_bytes, att.content_type or "image/jpeg", att.filename)
                    except Exception as e:
                        ctx_log.warning(f"Attachment read failed: {e}")
                    break

        # Cheap local pre-filter BEFORE any AI vision call — pHash
        # repost detection and OCR+scam-regex, zero API cost. Only
        # falls through to the expensive vision AI if this comes back
        # clean. This was fully built earlier but never wired in —
        # every image was going straight to paid vision calls before.
        if image_data:
            from core.image_filter import prefilter_image
            img_bytes, _, img_filename = image_data
            try:
                prefilter_result = await prefilter_image(img_bytes, guild.id)
                if prefilter_result.flagged:
                    db.track_event(guild.id, "scam_blocked")
                    result = PipelineResult(
                        action="block",
                        layer="image_prefilter",
                        reason=prefilter_result.reason,
                        score=prefilter_result.confidence,
                        recommended_action="WARN",
                        category=prefilter_result.category,
                        sassy_reason=(
                            f"Nice try with the image. {prefilter_result.reason} "
                            f"Caught before I even had to look properly."
                        ),
                    )
                    await _execute_action(message, result, dict(guild_data), is_staff_member)
                    return result

                # If OCR extracted text, run it through the existing
                # text badword checker too — scam text rendered as an
                # image to dodge text filters gets caught by
                # infrastructure that already exists, no new AI call.
                if prefilter_result.extracted_text:
                    from core.rules import _check_badwords
                    ocr_match = _check_badwords(
                        prefilter_result.extracted_text,
                        mod_cfg.get("badwords", "full"),
                    )
                    if ocr_match:
                        db.track_event(guild.id, "badword_triggers")
                        result = PipelineResult(
                            action="block",
                            layer="image_prefilter",
                            reason=f"Image contains rendered text matching blocked term: '{ocr_match}'",
                            score=1.0,
                            recommended_action="WARN",
                            category="badword",
                            sassy_reason=(
                                "Putting the bad word in an image instead of typing it "
                                "doesn't make it not the bad word. Nice try though."
                            ),
                        )
                        await _execute_action(message, result, dict(guild_data), is_staff_member)
                        return result
            except Exception as e:
                ctx_log.warning(f"Image pre-filter failed, falling through to AI vision: {e}")

        async with message.channel.typing():
            semantic_result = await run_semantic_scan(
                content=content,
                guild_id=guild.id,
                mod_level_config=mod_cfg,
                user_id=member.id,
                channel_id=message.channel.id,
                has_attachments=bool(message.attachments),
                image_data=image_data,
            )

        if semantic_result and semantic_result.flagged:
            if semantic_result.category == "grooming":
                db.track_event(guild.id, "grooming_blocked")
            elif semantic_result.category == "scam":
                db.track_event(guild.id, "scam_blocked")
            else:
                db.track_event(guild.id, "toxic_blocked")

            result = PipelineResult(
                action=semantic_result.action,
                layer="semantic",
                reason=semantic_result.reason,
                score=semantic_result.confidence,
                recommended_action=semantic_result.recommended_action,
                category=semantic_result.category,
                rule_slug=semantic_result.rule_slug,
                sassy_reason=semantic_result.voiced_reason(),
            )
            await _execute_action(message, result, dict(guild_data), is_staff_member)
            return result

    # All layers passed — message is clean
    return PipelineResult(action="pass", layer="none", reason="Clean")


# ========================
# SUSPICIOUS SIGNAL CHECK
# Fast local pre-check before spending ONNX API call.
# Only messages with these signals go through the classifier.
# ========================

_SUSPICIOUS_WORDS = {
    "shut", "kill", "idiot", "hate", "scam", "hack", "dumb",
    "fag", "nigg", "fugg", "stfu", "bitch", "bastard", "retard",
    "dick", "asshole", "trash", "garbage", "fuck", "damn", "cunt",
    "racist", "nazi", "terror", "bomb", "die", "kys",
}


def _has_suspicious_signals(content: str) -> bool:
    """
    Fast local check — returns True if content warrants a classifier call.

    Two paths:
    1. English content: checked against the English suspicious-word list
       plus a leetspeak regex, same as before — fast, free, no API call.
    2. Non-English content: the English word list is meaningless here,
       so instead of silently skipping the multilingual classifier
       entirely (the original bug), we let it through to Layer 2 by
       default. This preserves cost control for English (the highest
       volume language in most servers) while not blinding the
       multilingual model to every other language.

    Language detection itself is local and instant (langdetect), no
    API call — only the classifier call afterward has real cost.
    """
    import re
    content_lower = content.lower()

    # Very short messages aren't worth a language detection call —
    # langdetect is unreliable under ~3 words anyway. Treat as
    # suspicious-checkable directly via English heuristics; if it's
    # truly foreign and short, it'll likely still trip Layer 1 badwords
    # or get caught by the semantic layer on stricter tiers regardless.
    if len(content.split()) < 3:
        if any(word in content_lower for word in _SUSPICIOUS_WORDS):
            return True
        if re.search(r'[n|f][a-z]{1,4}[g|k|u]', content_lower):
            return True
        return False

    from utils.helpers import detect_dominant_language
    lang = detect_dominant_language(content)

    if lang == "en":
        if any(word in content_lower for word in _SUSPICIOUS_WORDS):
            return True
        if re.search(r'[n|f][a-z]{1,4}[g|k|u]', content_lower):
            return True
        return False

    # Non-English — skip the English word list entirely (it would
    # never match anyway) and let it through to the multilingual
    # classifier by default. This is the actual fix: previously,
    # non-English toxic content with none of these specific English
    # trigger words would never reach Layer 2 at all.
    return True


# ========================
# MOD REVIEW ESCALATION LOOP
# Background task that pings mods about pending reviews.
# Escalates to DMs if ignored too long.
# ========================

async def mod_review_escalation_loop(bot: discord.Client):
    """
    Background task. Runs every 30 minutes.
    Pings mods about pending reviews that haven't been actioned.
    Escalates to owner DMs if still ignored after 3 pings.
    """
    while True:
        await asyncio.sleep(TIMING.mod_review_ping_interval)

        for guild in bot.guilds:
            try:
                guild_data = get_guild(guild.id)
                if not guild_data or not guild_data["setup_complete"]:
                    continue

                pending = db.get_pending_reviews(guild.id)
                if not pending:
                    continue

                log_channel_id = guild_data.get("logs_channel_id")
                mod_role_id = guild_data.get("mod_role_id")
                if not log_channel_id:
                    continue

                log_channel = guild.get_channel(int(log_channel_id))
                if not log_channel:
                    continue

                for review in pending:
                    ping_count = review["ping_count"]

                    if ping_count >= 3:
                        # Escalate to owner DM
                        owner = guild.owner
                        if owner:
                            try:
                                await owner.send(
                                    f"🚨 **{guild.name}** has {len(pending)} pending "
                                    f"mod review(s) that have been ignored for over "
                                    f"{ping_count * 30} minutes. "
                                    f"Please check the logs channel."
                                )
                            except Exception:
                                pass
                        break  # One DM per cycle per guild

                    # Ping in logs channel
                    mod_mention = f"<@&{mod_role_id}>" if mod_role_id else "Moderators"
                    try:
                        await log_channel.send(
                            f"⏰ {mod_mention} — **{len(pending)}** message(s) still "
                            f"pending review. Use `.ivyreview <id> approve/reject` to action them."
                        )
                        db.increment_review_ping(review["id"])
                    except Exception as e:
                        log.warning(f"[guild={guild.id}] Review ping failed: {e}")

            except Exception as e:
                log.warning(f"[guild={guild.id}] Review escalation error: {e}")
