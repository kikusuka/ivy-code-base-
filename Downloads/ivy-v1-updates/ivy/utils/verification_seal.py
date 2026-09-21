# utils/verification_seal.py
# Ivy Bot - DM Authenticity Verification
#
# Every real strike DM carries a short signature computed from that
# strike's actual record (strike ID + user ID + guild ID) using a
# secret key only this process holds. This is NOT "we included our
# server ID" (that's public information anyone can look up and copy
# into a fake message) — it's a real HMAC. Without the signing secret,
# nobody can compute a code that will pass verification, no matter how
# convincing the rest of a fake message looks.
#
# This directly answers a real threat: a lookalike bot or a prankster
# crafting a fake "you got warned by Ivy" screenshot/DM. /verify lets
# anyone check a code against their own actual records in seconds.

import hmac
import hashlib
from typing import Optional

from config import IVY_SIGNING_SECRET


def generate_seal(strike_id: int, user_id: int, guild_id: int) -> Optional[str]:
    """
    Generates a short verification seal for a strike record.
    Returns None if no signing secret is configured — callers should
    gracefully omit the seal from messages in that case rather than
    show a broken/missing value. This is a strengthening feature, not
    a required one; a server without IVY_SIGNING_SECRET set just
    doesn't get seals, it doesn't break anything else.
    """
    if not IVY_SIGNING_SECRET:
        return None

    payload = f"{strike_id}:{user_id}:{guild_id}".encode("utf-8")
    digest = hmac.new(
        IVY_SIGNING_SECRET.encode("utf-8"), payload, hashlib.sha256
    ).hexdigest()

    # 8 hex chars — short enough to type/read comfortably, still backed
    # by a full 256-bit HMAC underneath. Truncating the display doesn't
    # weaken the guarantee for this threat model (someone trying to
    # guess a valid code without the secret), it just makes it usable
    # by an actual human copying it out of a DM.
    return digest[:8].upper()


def verify_seal(seal: str, strike_id: int, user_id: int, guild_id: int) -> bool:
    """
    Checks a user-provided seal against what the real record would
    produce. Uses constant-time comparison — not because the stakes
    here are nation-state-level, just because it's the correct way to
    compare secrets and costs nothing extra.
    """
    if not seal:
        return False
    expected = generate_seal(strike_id, user_id, guild_id)
    if not expected:
        return False
    return hmac.compare_digest(seal.strip().upper(), expected)
