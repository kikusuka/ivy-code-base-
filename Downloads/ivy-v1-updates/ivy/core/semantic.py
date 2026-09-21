# core/semantic.py
# Ivy Bot - Layer 3: Groq Semantic Moderation
# Handles rule enforcement, scam detection, grooming patterns,
# political content, and context-aware reasoning.
# Rotates across Groq models to spread rate limit pressure equally.
# Most expensive layer — only runs when lower layers don't catch it.

import json
import asyncio
import logging
import itertools
from dataclasses import dataclass, field
from typing import Optional

from groq import Groq
from pydantic import BaseModel, ValidationError

from config import (
    GROQ_API_KEY,
    GROQ_HEAVY_MODELS,
    GROQ_FAST_MODELS,
    GROQ_VISION_MODELS,
    THRESHOLDS,
)
from data import db
from config import HF_REASONING_SPACE_URL, HF_API_KEY
import aiohttp

log = logging.getLogger("ivy.semantic")

# ========================
# PYDANTIC RESPONSE SCHEMAS
# Validates every Groq response at runtime.
# Hallucinated or missing fields raise ValidationError, never KeyError.
# ========================

class _RuleCheckResponse(BaseModel):
    violated: bool
    rule_slug: Optional[str] = None
    confidence: float = 0.0
    category: str = "rule"
    reason: Optional[str] = None
    recommended_action: Optional[str] = "WARN"

class _ScamCheckResponse(BaseModel):
    is_scam: bool
    confidence: float = 0.0
    scam_type: Optional[str] = None
    reason: Optional[str] = None

class _GroomingCheckResponse(BaseModel):
    is_grooming: bool
    confidence: float = 0.0
    severity: str = "low"
    reason: Optional[str] = None

class _PoliticsCheckResponse(BaseModel):
    is_political_heavy: bool
    confidence: float = 0.0
    reason: Optional[str] = None

class _ImageCheckResponse(BaseModel):
    unsafe: bool
    is_scam: bool
    confidence: float = 0.0
    category: str = "safe"
    reason: Optional[str] = None

class _BiasCheckResponse(BaseModel):
    justified: bool
    confidence: float = 0.0
    concern: Optional[str] = None

def _parse_response(raw: str, schema):
    """
    Safely parses and validates a Groq JSON response against a Pydantic schema.
    Returns None if parsing or validation fails — never raises.
    """
    try:
        data = json.loads(raw)
        return schema(**data)
    except json.JSONDecodeError as e:
        log.warning(f"JSON decode error for {schema.__name__}: {e}")
        return None
    except ValidationError as e:
        log.warning(f"Pydantic validation error for {schema.__name__}: {e}")
        return None

async def _summarize_context(messages: list) -> str:
    """
    Summarizes recent message sentiment into one short sentence.
    Saves tokens vs pasting raw messages into every prompt.
    Only called when there are 3+ messages worth summarizing.
    """
    if len(messages) <= 2:
        return "\n".join(f"  > {m}" for m in messages)
    raw = await _groq_call(
        messages=[
            {
                "role": "system",
                "content": (
                    "Summarize the overall sentiment, topic, and tone of these "
                    "Discord messages in one short sentence. Be factual and neutral. "
                    "Respond with only the summary sentence, nothing else."
                )
            },
            {"role": "user", "content": "\n".join(messages[-5:])}
        ],
        use_heavy=False,
    )
    return raw.strip() if raw else "\n".join(f"  > {m}" for m in messages[-5:])




# ========================
# GROQ CLIENT
# ========================

_groq_client: Optional[Groq] = None


def _get_groq() -> Optional[Groq]:
    global _groq_client
    if _groq_client is None and GROQ_API_KEY:
        _groq_client = Groq(api_key=GROQ_API_KEY)
    return _groq_client


# ========================
# MODEL ROTATION — FAILURE-AWARE
#
# Plain itertools.cycle() treats every model as equally healthy
# forever. In production that meant a decommissioned model silently
# ate 2 of every 3 retry attempts before the pipeline finally landed
# on the one model that still worked — confirmed in live logs, and
# the whole reason GROQ_FAST_MODELS shrank to one entry.
#
# This pool rotates for the RIGHT reason — spreading load across
# models that are actually alive — by telling apart two failure
# modes that a raw retry loop can't distinguish:
#
#   PERMANENT (decommissioned/unknown model): Groq returns a distinct,
#   recognizable error for this. Once seen, that model is benched for
#   the rest of the process — no point burning retries on a corpse —
#   and it's logged loudly exactly once so it gets fixed in config.py,
#   not silently forever.
#
#   TRANSIENT (rate limit, timeout, 5xx, momentary hiccup): does NOT
#   get permanently benched. A model that's just busy right now is
#   still a good model. It gets a short cooldown so rotation prefers
#   other models for a bit, then becomes eligible again automatically.
#
# If every model in a pool is currently ineligible (rare — everything
# down or benched at once), rotation falls back to the raw cycle
# rather than returning nothing. A stale attempt beats total silence.
# ========================

class _GroqModelPool:
    TRANSIENT_COOLDOWN_SECONDS = 60
    TRANSIENT_FAILURE_THRESHOLD = 2  # consecutive fails before a cooldown

    # Substrings seen in Groq's error text when a model is gone for good.
    # Deliberately broad — false-positive here just means an early,
    # loudly-logged bench that's easy to notice and undo; false-negative
    # means silently wasting retries forever, which is the worse failure.
    _DECOMMISSION_SIGNALS = (
        "decommissioned", "model_not_found", "does not exist",
        "has been deprecated", "invalid model id", "model_decommissioned",
        "no longer supported", "unknown model",
    )

    def __init__(self, models: list[str], name: str):
        if not models:
            raise ValueError(f"Groq model pool '{name}' cannot be empty.")
        self.name = name
        self._models = list(models)
        self._failures: dict[str, int] = {m: 0 for m in models}
        self._cooldown_until: dict[str, float] = {m: 0.0 for m in models}
        self._dead: set[str] = set()
        self._warned_dead: set[str] = set()
        self._cycle = itertools.cycle(self._models)

    def _eligible(self, model: str) -> bool:
        if model in self._dead:
            return False
        return asyncio.get_event_loop().time() >= self._cooldown_until[model]

    def next_model(self) -> str:
        """Next eligible model, skipping dead/cooling-down ones. Falls
        back to raw rotation if the whole pool is temporarily down."""
        for _ in range(len(self._models)):
            model = next(self._cycle)
            if self._eligible(model):
                return model
        return next(self._cycle)  # everything down — try anyway

    def record_success(self, model: str) -> None:
        self._failures[model] = 0
        self._cooldown_until[model] = 0.0

    def record_failure(self, model: str, error: Exception) -> None:
        error_text = str(error).lower()

        if any(sig in error_text for sig in self._DECOMMISSION_SIGNALS):
            self._dead.add(model)
            if model not in self._warned_dead:
                self._warned_dead.add(model)
                log.warning(
                    f"⚠️  [Groq pool:{self.name}] '{model}' looks decommissioned "
                    f"(error: {error_text[:120]}) — benching it for the rest of "
                    f"this run so retries stop hitting it. Remove it from "
                    f"config.py once confirmed via console.groq.com/docs/deprecations."
                )
            return

        self._failures[model] = self._failures.get(model, 0) + 1
        if self._failures[model] >= self.TRANSIENT_FAILURE_THRESHOLD:
            self._cooldown_until[model] = (
                asyncio.get_event_loop().time() + self.TRANSIENT_COOLDOWN_SECONDS
            )
            log.info(
                f"[Groq pool:{self.name}] '{model}' cooling down "
                f"{self.TRANSIENT_COOLDOWN_SECONDS}s after "
                f"{self._failures[model]} consecutive transient failures."
            )

    def status(self) -> dict:
        """Inspection helper — current health per model. Handy for a
        future .ivystatus command or just eyeballing logs."""
        now = asyncio.get_event_loop().time()
        return {
            m: {
                "eligible": self._eligible(m),
                "decommissioned": m in self._dead,
                "cooldown_remaining": max(0, round(self._cooldown_until[m] - now)),
            }
            for m in self._models
        }


_heavy_pool = _GroqModelPool(GROQ_HEAVY_MODELS, name="heavy")
_fast_pool = _GroqModelPool(GROQ_FAST_MODELS, name="fast")
_vision_pool = _GroqModelPool(GROQ_VISION_MODELS, name="vision")


# ========================
# SEMANTIC RESULT
# ========================

@dataclass
class SemanticResult:
    flagged: bool
    confidence: float               # 0.0 to 1.0
    action: str                     # "block" | "flag" | "pass"
    category: str                   # "rule" | "scam" | "grooming" | "politics" | "toxicity" | "safe"
    reason: str                     # explanation — voice depends on category, see below
    rule_slug: Optional[str] = None # which rule was violated if category == "rule"
    recommended_action: str = "WARN"# WARN | MUTE | KICK | BAN
    source_model: str = "unknown"

    @property
    def is_safety_critical(self) -> bool:
        """
        Categories where Ivy's personality MUST drop to zero sass.
        Grooming and predatory behavior are never, under any
        circumstance, a place for sarcasm or wit. This is a hard
        rule, not a tunable threshold — there is no confidence level
        at which mocking a potential victim or predator is acceptable.
        """
        return self.category == "grooming"

    def voiced_reason(self) -> str:
        """
        Returns the reason text in the appropriate voice.
        Safety-critical categories get flat, neutral, professional
        language regardless of what the model originally generated.
        Everything else can use Ivy's normal sharp/sassy voice.
        """
        if self.is_safety_critical:
            return (
                f"This message was flagged for a child-safety concern. "
                f"Reason: {self.reason} "
                f"This has been escalated for moderator review. "
                f"No further action will be taken automatically."
            )
        return self.reason


# ========================
# GROQ CALL WRAPPER
# Handles model fallback automatically.
# Tries primary model, falls back to next in pool on failure.
# ========================

async def _groq_call(
    messages: list[dict],
    use_heavy: bool = False,
    max_retries: int = 3,
    temperature: float = 0.0,
) -> Optional[str]:
    """
    Makes a Groq API call with automatic model rotation and retry.
    Returns raw response text or None if all attempts fail.
    """
    client = _get_groq()
    if not client:
        log.warning("Groq client not configured. Semantic layer skipped.")
        return None

    pool = _heavy_pool if use_heavy else _fast_pool
    loop = asyncio.get_event_loop()
    last_error = None

    for attempt in range(max_retries):
        model = pool.next_model()
        try:
            response = await loop.run_in_executor(
                None,
                lambda m=model: client.chat.completions.create(
                    model=m,
                    messages=messages,
                    temperature=temperature,
                    response_format={"type": "json_object"},
                    max_tokens=512,
                )
            )
            result = response.choices[0].message.content
            pool.record_success(model)
            log.info(f"Groq call succeeded on model {model} (attempt {attempt + 1})")
            return result

        except Exception as e:
            last_error = e
            pool.record_failure(model, e)
            log.warning(
                f"Groq model {model} failed (attempt {attempt + 1}): {e}. "
                f"Rotating to next eligible model..."
            )
            await asyncio.sleep(0.5 * (attempt + 1))  # brief backoff

    log.warning(
        f"All Groq attempts exhausted for pool '{pool.name}'. Last error: {last_error}"
    )
    return None




# ========================
# SELF-HOSTED QWEN 1.5B REASONING CALL
# Replaces Groq for rules, scam, and politics detection.
# No external rate limit — runs on your own HF Space.
# Groq is reserved ONLY for grooming detection and bias checks,
# where accuracy matters more than throughput and volume is naturally low.
# ========================

_reasoning_session: Optional["aiohttp.ClientSession"] = None


async def _get_reasoning_session():
    global _reasoning_session
    if _reasoning_session is None or _reasoning_session.closed:
        _reasoning_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10)
        )
    return _reasoning_session


# ========================
# CIRCUIT BREAKER
# Protects against repeated failures hammering a dead Space.
# Closed = normal operation. Open = skip calls entirely for a cooldown
# period, return None immediately so the pipeline falls through to
# flag-only behavior instead of retrying a dead endpoint on every message.
# Half-open = after cooldown, allow exactly one test call through.
# ========================

class _CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, reset_seconds: int = 300):
        self.failure_threshold = failure_threshold
        self.reset_seconds = reset_seconds
        self.failure_count = 0
        self.state = "closed"  # closed | open | half_open
        self.opened_at: Optional[float] = None

    def record_success(self):
        self.failure_count = 0
        self.state = "closed"

    def record_failure(self):
        self.failure_count += 1
        if self.failure_count >= self.failure_threshold:
            self.state = "open"
            self.opened_at = asyncio.get_event_loop().time()
            log.warning(
                f"Circuit breaker OPENED after {self.failure_count} "
                f"consecutive failures. Cooling down for {self.reset_seconds}s."
            )

    def can_attempt(self) -> bool:
        if self.state == "closed":
            return True
        if self.state == "open":
            elapsed = asyncio.get_event_loop().time() - (self.opened_at or 0)
            if elapsed >= self.reset_seconds:
                self.state = "half_open"
                log.info("Circuit breaker entering HALF-OPEN, allowing test call.")
                return True
            return False
        return True  # half_open — allow the one test attempt


_qwen_circuit = _CircuitBreaker(failure_threshold=3, reset_seconds=300)


async def _qwen_reasoning_call(
    system_prompt: str,
    user_content: str,
    max_retries: int = 2,
) -> Optional[str]:
    """
    Calls the self-hosted Qwen 1.5B reasoning Space for rules/scam/
    politics detection. If no Space is configured (v1, zero-Space
    setup), falls back to Groq's fast model rotation instead — the
    same infrastructure already proven reliable for grooming detection
    and bias-checking. This widens Groq's scope temporarily; the
    self-hosted Space stays the real upgrade path once capacity exists,
    it's just not required to ship v1.
    """
    if not HF_REASONING_SPACE_URL:
        log.info("No reasoning Space configured — routing through Groq instead.")
        return await _groq_call(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f'Message to evaluate: "{user_content}"'},
            ],
            use_heavy=False,
            max_retries=max_retries,
        )

    if not _qwen_circuit.can_attempt():
        # Circuit is open — don't even try, fall through immediately.
        # Pipeline treats this exactly like a None response: the
        # calling layer (check_rules/check_scam/check_politics) simply
        # returns None, meaning that check is skipped for this message
        # until the breaker resets. Other layers (spam, badwords,
        # classifier) still run normally — only this specific layer
        # is temporarily degraded, not the whole pipeline.
        return None

    full_prompt = (
        f"{system_prompt}\n\nUser message to evaluate: \"{user_content}\"\n\n"
        f"Respond with ONLY the JSON object, nothing else."
    )

    for attempt in range(max_retries):
        try:
            session = await _get_reasoning_session()
            headers = {"Authorization": f"Bearer {HF_API_KEY}"} if HF_API_KEY else {}

            async with session.post(
                HF_REASONING_SPACE_URL,
                json={
                    "inputs": full_prompt,
                    "parameters": {
                        "max_new_tokens": 300,
                        "temperature": 0.1,
                        "return_full_text": False,
                    }
                },
                headers=headers,
            ) as resp:
                if resp.status != 200:
                    log.warning(f"Qwen reasoning Space HTTP {resp.status} (attempt {attempt+1})")
                    continue

                data = await resp.json()

                if isinstance(data, dict):
                    text = data.get("generated_text") or data.get("text")
                elif isinstance(data, list) and data:
                    first = data[0]
                    text = first.get("generated_text") if isinstance(first, dict) else str(first)
                else:
                    text = None

                if text:
                    _qwen_circuit.record_success()
                    return text.strip()

        except asyncio.TimeoutError:
            log.warning(f"Qwen reasoning Space timed out (attempt {attempt+1})")
        except Exception as e:
            log.warning(f"Qwen reasoning Space error (attempt {attempt+1}): {e}")

        await asyncio.sleep(0.3 * (attempt + 1))

    _qwen_circuit.record_failure()
    log.warning("Qwen reasoning Space exhausted all retries.")
    return None

# ========================
# RULE ENFORCEMENT
# Checks message against server-specific rules parsed during onboarding.
# Groq reads the rules and reasons about semantic intent — not just keywords.
# ========================

async def check_rules(
    content: str,
    guild_id: int,
    context_messages: list[str] = None,
) -> Optional[SemanticResult]:
    """
    Checks message against guild's custom rules semantically.
    Returns SemanticResult if a rule is violated, None if clean.
    """
    rules = db.get_rules(guild_id)
    if not rules:
        return None

    # Build rules summary for prompt
    rules_text = ""
    for rule in rules:
        keywords = json.loads(rule["keywords"]) if rule["keywords"] else []
        rules_text += (
            f"- Slug: '{rule['slug']}'\n"
            f"  Name: {rule['name']}\n"
            f"  Description: {rule['description']}\n"
            f"  Action: {rule['action']}\n"
            f"  Severity: {rule['severity']}/3\n"
            f"  Keywords: {', '.join(keywords)}\n\n"
        )

    context_text = ""
    if context_messages:
        summary = await _summarize_context(context_messages)
        context_text = f"\nRecent conversation context summary: {summary}"

    system_prompt = f"""You are Ivy's semantic rule enforcement engine.
Analyze the message against these server rules and determine if any are violated.
Consider semantic intent, not just keywords. Someone can violate a rule without using trigger words.{context_text}

SERVER RULES:
{rules_text}

Respond ONLY in this exact JSON format:
{{
  "violated": boolean,
  "rule_slug": "slug of violated rule or null",
  "confidence": float between 0.0 and 1.0,
  "category": "rule",
  "reason": "sassy Ivy-voiced explanation of the violation, or null if clean",
  "recommended_action": "WARN" | "MUTE" | "KICK" | "BAN" or null
}}

Be highly accurate. Only flag genuine violations. Ambiguous messages should have confidence below 0.7."""

    # Self-hosted reasoning Space is now the primary path — this check
    # runs on EVERY message once semantic_ai is enabled, making it the
    # single highest-volume call in Layer 3. That volume needs to scale
    # with message count, not sit behind Groq's per-minute quota.
    # _qwen_reasoning_call already falls back to Groq's fast pool on
    # its own if HF_REASONING_SPACE_URL isn't configured — so this is
    # safe to ship even before the Space is deployed, it just upgrades
    # automatically the moment the URL is set, same as Layer 2.
    raw = await _qwen_reasoning_call(system_prompt, content)

    if not raw:
        return None

    parsed = _parse_response(raw, _RuleCheckResponse)
    if not parsed or not parsed.violated:
        return None

    action = _confidence_to_action(parsed.confidence)
    return SemanticResult(
        flagged=True,
        confidence=parsed.confidence,
        action=action,
        category="rule",
        reason=parsed.reason or "Rule violation detected.",
        rule_slug=parsed.rule_slug,
        recommended_action=parsed.recommended_action or "WARN",
        source_model="reasoning_layer",
    )


# ========================
# SCAM DETECTION
# Detects crypto scams, fake giveaways, phishing links,
# impersonation (MrBeast, Discord staff, etc), suspicious investment content.
# ========================

async def check_scam(
    content: str,
    has_attachments: bool = False,
    attachment_descriptions: list[str] = None,
) -> Optional[SemanticResult]:
    """
    Detects scam content semantically.
    Covers: crypto pumps, fake giveaways, phishing, impersonation,
    suspicious investment advice, "too good to be true" offers.
    """
    attachment_text = ""
    if has_attachments and attachment_descriptions:
        attachment_text = (
            f"\nAttachment descriptions: {', '.join(attachment_descriptions)}"
        )

    system_prompt = """You are Ivy's scam detection engine.
Analyze this Discord message for scam patterns including:
- Cryptocurrency pump and dump schemes
- Fake giveaways (free Nitro, fake MrBeast, fake Discord staff)
- Phishing links or suspicious URLs
- Investment advice promising unrealistic returns
- Impersonation of known figures or Discord staff
- "DM me for free X" patterns
- NFT or token promotion that feels promotional/unsolicited

Respond ONLY in this exact JSON format:
{
  "is_scam": boolean,
  "confidence": float between 0.0 and 1.0,
  "scam_type": "crypto" | "giveaway" | "phishing" | "impersonation" | "investment" | "other" | null,
  "reason": "brief sassy explanation of why this is a scam, or null if clean"
}

Only flag genuine scams. Discussing crypto casually is NOT a scam."""

    raw = await _qwen_reasoning_call(system_prompt, f'{content}{attachment_text}')

    if not raw:
        return None

    parsed = _parse_response(raw, _ScamCheckResponse)
    if not parsed or not parsed.is_scam or parsed.confidence < 0.65:
        return None

    return SemanticResult(
        flagged=True,
        confidence=parsed.confidence,
        action=_confidence_to_action(parsed.confidence),
        category="scam",
        reason=parsed.reason or "Scam content detected.",
        recommended_action="MUTE",
        source_model="reasoning_layer",
    )


# ========================
# GROOMING DETECTION
# Semantic layer on top of the keyword-based grooming check in spam.py.
# Catches subtle grooming patterns that don't use trigger phrases.
# ========================

async def check_grooming(
    content: str,
    user_history: list[str] = None,
) -> Optional[SemanticResult]:
    """
    Detects grooming and predatory behavior semantically.
    More thorough than keyword matching — catches subtle manipulation patterns.
    """
    history_text = ""
    if user_history:
        history_text = (
            f"\nThis user's recent messages in this server:\n"
            + "\n".join(f"  > {m}" for m in user_history[-10:])
        )

    system_prompt = f"""You are Ivy's child safety enforcement engine.
Analyze this message for grooming and predatory behavior patterns including:
- Attempts to isolate a user from friends, family, or other server members
- Age solicitation in any form
- Requests for personal information (location, school, phone number)
- Attempts to move conversation to private platforms
- Establishing "special" exclusive relationships with minors
- Gift offers contingent on keeping secrets
- Sexual undertones directed at potentially young users
- "Cuddling", "kitten", "daddy/mommy" dynamics in recruitment context{history_text}

Respond ONLY in this exact JSON format:
{{
  "is_grooming": boolean,
  "confidence": float between 0.0 and 1.0,
  "severity": "extreme" | "moderate" | "low",
  "reason": "brief explanation of the pattern detected, or null if clean"
}}

Be very accurate. False positives here cause real harm. Only flag genuine predatory patterns."""

    # Primary: self-hosted reasoning Space. This runs on every message
    # that reaches Layer 3 for Normal+ tiers on Hard/Hardcore — same
    # volume argument as rules/scam/politics, it needs to scale with
    # message count, not a per-minute API quota.
    raw = await _qwen_reasoning_call(system_prompt, content)
    source = "reasoning_layer"

    # Fallback: Groq's heavy model, ONLY as an emergency safety net if
    # the self-hosted Space is unreachable. Every other Layer 3 check
    # is fine silently skipping when the Space is down — the circuit
    # breaker is explicitly designed to degrade just that one check
    # while the rest of the pipeline keeps running. Child safety is
    # the one category where "detection silently didn't run this
    # time" isn't an acceptable failure mode, so it keeps the
    # strongest available fallback even though Groq is no longer the
    # primary path for any per-message check.
    if not raw:
        log.warning(
            "Grooming check: self-hosted reasoning Space unavailable — "
            "falling back to Groq heavy model as a safety net."
        )
        raw = await _groq_call(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f'Message: "{content}"'},
            ],
            use_heavy=True,
        )
        source = "groq_heavy_fallback"

    if not raw:
        return None

    parsed = _parse_response(raw, _GroomingCheckResponse)
    if not parsed or not parsed.is_grooming or parsed.confidence < 0.55:
        return None

    if parsed.severity == "extreme":
        recommended, action = "BAN", "block"
    elif parsed.severity == "moderate":
        recommended = "MUTE"
        action = "block" if parsed.confidence >= 0.80 else "flag"
    else:
        recommended, action = "WARN", "flag"

    return SemanticResult(
        flagged=True,
        confidence=parsed.confidence,
        action=action,
        category="grooming",
        reason=parsed.reason or "Predatory behavior pattern detected.",
        recommended_action=recommended,
        source_model=source,
    )


# ========================
# POLITICS / HEAVY CONTENT DETECTION
# Only active on Medium+ moderation levels.
# Detects heavy political content, not casual political discussion.
# ========================

async def check_politics(content: str) -> Optional[SemanticResult]:
    """
    Detects heavy political content that disrupts server atmosphere.
    Casual political mentions are fine — this catches inflammatory content.
    """
    system_prompt = """You are Ivy's content atmosphere engine.
Analyze this message for heavy political content that could disrupt server harmony.

Flag ONLY:
- Extremist political rhetoric (any direction)
- Content that incites division or hatred based on political beliefs
- Election misinformation or voter suppression content
- Heavy propaganda or radicalization content

Do NOT flag:
- Casual political opinions
- News sharing
- General political discussion
- Mild disagreements

Respond ONLY in this exact JSON format:
{
  "is_political_heavy": boolean,
  "confidence": float between 0.0 and 1.0,
  "reason": "brief explanation or null if clean"
}

Most political messages should return false. Only flag genuinely disruptive content."""

    raw = await _qwen_reasoning_call(system_prompt, content)

    if not raw:
        return None

    parsed = _parse_response(raw, _PoliticsCheckResponse)
    if not parsed or not parsed.is_political_heavy or parsed.confidence < 0.75:
        return None

    return SemanticResult(
        flagged=True,
        confidence=parsed.confidence,
        action=_confidence_to_action(parsed.confidence),
        category="politics",
        reason=parsed.reason or "Heavy political content detected.",
        recommended_action="WARN",
        source_model="reasoning_layer",
    )


# ========================
# IMAGE SCAM / NSFW DETECTION
# Uses Groq vision model for image attachments.
# Checks for NSFW content AND crypto/scam images in one pass.
# ========================

async def check_image(
    image_bytes: bytes,
    content_type: str = "image/jpeg",
    filename: str = "image",
) -> Optional[SemanticResult]:
    """
    Scans image attachments for NSFW content and scam imagery.
    Uses Groq vision model — only for images under 5MB.
    """
    client = _get_groq()
    if not client:
        return None

    try:
        import base64
        encoded = base64.b64encode(image_bytes).decode("utf-8")

        system_prompt = """You are Ivy's vision safety scanner.
Analyze this image for TWO categories:

1. NSFW/Unsafe: nudity, explicit content, graphic violence, gore
2. SCAM: cryptocurrency tickers, fake giveaway screenshots, pump-and-dump promotions,
   fake Discord Nitro offers, suspicious QR codes, phishing content

Respond ONLY in this exact JSON format:
{
  "unsafe": boolean,
  "is_scam": boolean,
  "confidence": float between 0.0 and 1.0,
  "category": "nsfw" | "scam" | "both" | "safe",
  "reason": "brief explanation or null if safe"
}"""

        loop = asyncio.get_event_loop()
        model = _vision_pool.next_model()

        try:
            response = await loop.run_in_executor(
                None,
                lambda: client.chat.completions.create(
                    model=model,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": system_prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:{content_type};base64,{encoded}"
                                    }
                                }
                            ]
                        }
                    ],
                    temperature=0.0,
                    max_tokens=256,
                )
            )
        except Exception as e:
            # Previously this fell straight to the outer except and the
            # vision pool never learned anything failed — a dead vision
            # model would silently eat every image check forever with
            # zero rotation. Now it's recorded like every other pool.
            _vision_pool.record_failure(model, e)
            raise

        _vision_pool.record_success(model)
        raw = response.choices[0].message.content.strip()
        parsed = _parse_response(raw, _ImageCheckResponse)
        if not parsed or (not parsed.unsafe and not parsed.is_scam):
            return None
        if parsed.confidence < 0.65:
            return None

        recommended = "MUTE" if parsed.category == "scam" else "WARN"
        return SemanticResult(
            flagged=True,
            confidence=parsed.confidence,
            action=_confidence_to_action(parsed.confidence),
            category=parsed.category,
            reason=parsed.reason or "Unsafe image content detected.",
            recommended_action=recommended,
            source_model=model,
        )

    except Exception as e:
        log.warning(f"Image check failed: {e}")
        return None


# ========================
# BIAS DETECTION FOR HEY IVY COMMANDS
# Before executing a destructive Hey Ivy command,
# check if the reason given is biased or unjustified.
# If biased, flag for mod review instead of executing.
# ========================

async def check_command_bias(
    command: str,
    target_username: str,
    reason: str,
    requester_username: str,
    target_strike_history: list[dict] = None,
) -> dict:
    """
    Evaluates if a Hey Ivy destructive command (ban/kick/mute)
    has a justified reason or appears biased/personal.

    Returns:
        {
            "justified": bool,
            "confidence": float,
            "concern": str or None  — explanation if not justified
        }
    """
    history_text = ""
    if target_strike_history:
        history_text = (
            f"\nTarget user's strike history:\n"
            + "\n".join(
                f"  - {s['reason']} ({s['timestamp']})"
                for s in target_strike_history[:5]
            )
        )

    system_prompt = f"""You are Ivy's command bias evaluator.
A server admin just issued a moderation command. Evaluate if the reason given is legitimate
or if it appears biased, personal, or unjustified.{history_text}

Legitimate reasons include: harassment, rule violations, spam, threats, scams, toxic behavior.
Unjustified reasons include: personal dislike, "I don't like them", vague reasons with no context,
targeting someone for protected characteristics, or reasons that contradict the strike history.

Respond ONLY in this exact JSON format:
{{
  "justified": boolean,
  "confidence": float between 0.0 and 1.0,
  "concern": "brief explanation of concern if not justified, null if justified"
}}"""

    raw = await _groq_call(
        messages=[
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"Command: {command}\n"
                    f"Requester: {requester_username}\n"
                    f"Target: {target_username}\n"
                    f"Reason given: {reason}"
                )
            },
        ],
        use_heavy=True,  # Bias detection needs careful reasoning
    )

    if not raw:
        # If Groq fails here, default to requiring human review
        return {
            "justified": False,
            "confidence": 0.0,
            "concern": "Could not evaluate reason — defaulting to mod review for safety."
        }

    parsed = _parse_response(raw, _BiasCheckResponse)
    if not parsed:
        return {"justified": False, "confidence": 0.0,
                "concern": "Evaluation failed — defaulting to mod review."}
    return {"justified": parsed.justified, "confidence": parsed.confidence,
            "concern": parsed.concern}


# ========================
# FULL SEMANTIC SCAN
# Master function called by the moderation pipeline.
# Runs all relevant checks based on guild's moderation level.
# Returns the highest-confidence result found, or None if clean.
# ========================

async def run_semantic_scan(
    content: str,
    guild_id: int,
    mod_level_config: dict,
    user_id: int = None,
    channel_id: int = None,
    has_attachments: bool = False,
    image_data: tuple = None,       # (bytes, content_type, filename)
    context_messages: list[str] = None,
) -> Optional[SemanticResult]:
    """
    Runs all applicable semantic checks based on mod level config.
    Returns the most severe result found, or None if everything is clean.

    Checks run in priority order:
    1. Grooming (always, if enabled — child safety first)
    2. Rules (always — server-specific)
    3. Scam (if enabled)
    4. Image (if attachment present)
    5. Politics (if enabled)
    """
    from utils.logger import get_context_logger
    ctx_log = get_context_logger(
        "ivy.semantic",
        guild_id=guild_id,
        user_id=user_id,
        channel_id=channel_id,
    )

    results: list[SemanticResult] = []
    tasks = []

    # 1. Grooming — always runs if grooming is enabled
    if mod_level_config.get("grooming"):
        user_history = []
        if user_id:
            # Fetch last 10 messages from this user for context
            strikes = db.get_user_strikes(user_id, guild_id, limit=5)
            user_history = [s["message_content"] for s in strikes if s["message_content"]]
        tasks.append(check_grooming(content, user_history))

    # 2. Rules — always runs (every server has rules)
    tasks.append(check_rules(content, guild_id, context_messages))

    # 3. Scam detection
    if mod_level_config.get("scam"):
        tasks.append(check_scam(content, has_attachments))

    # 4. Politics
    if mod_level_config.get("politics"):
        tasks.append(check_politics(content))

    # Run all checks concurrently to minimize latency
    scan_results = await asyncio.gather(*tasks, return_exceptions=True)

    for res in scan_results:
        if isinstance(res, Exception):
            ctx_log.warning(f"Semantic check raised exception: {res}")
            continue
        if res is not None and res.flagged:
            results.append(res)

    # 5. Image scan (separate — needs binary data)
    if has_attachments and image_data:
        img_bytes, content_type, filename = image_data
        if len(img_bytes) < 5 * 1024 * 1024:  # under 5MB only
            try:
                img_result = await check_image(img_bytes, content_type, filename)
                if img_result and img_result.flagged:
                    results.append(img_result)
            except Exception as e:
                ctx_log.warning(f"Image scan failed: {e}")

    if not results:
        return None

    # Return the result with highest confidence
    best = max(results, key=lambda r: r.confidence)
    ctx_log.info(
        f"Semantic scan complete — "
        f"category={best.category} "
        f"confidence={best.confidence:.2f} "
        f"action={best.action} "
        f"model={best.source_model}"
    )

    # Log to metrics DB for observability
    try:
        db.update_avg_toxicity(guild_id, best.confidence)
        db.track_event(guild_id, "toxic_blocked" if best.action == "block" else "mod_reviews_total")
    except Exception as e:
        ctx_log.warning(f"Metrics logging failed: {e}")

    return best


# ========================
# UTILITY
# ========================

def _confidence_to_action(confidence: float) -> str:
    """Maps confidence score to pipeline action."""
    if confidence >= THRESHOLDS.block:
        return "block"
    elif confidence >= THRESHOLDS.flag:
        return "flag"
    return "pass"
