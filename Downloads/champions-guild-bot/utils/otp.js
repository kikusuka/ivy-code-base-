/**
 * In-memory OTP store.
 * Each OTP is valid for exactly 2 minutes and single-use.
 */

const otpStore = new Map();
const OTP_TTL_MS = 2 * 60 * 1000; // 2 minutes

function generateOTP() {
  return Math.floor(100000 + Math.random() * 900000).toString(); // 6-digit
}

function createOTP(userId) {
  // Invalidate any previous OTP for this user
  for (const [code, data] of otpStore.entries()) {
    if (data.userId === userId) otpStore.delete(code);
  }

  const code = generateOTP();
  const expiresAt = Date.now() + OTP_TTL_MS;

  otpStore.set(code, { userId, expiresAt, used: false });

  // Auto-cleanup after TTL
  setTimeout(() => otpStore.delete(code), OTP_TTL_MS + 5000);

  return { code, expiresAt };
}

function validateOTP(code) {
  const entry = otpStore.get(code);
  if (!entry) return { valid: false, reason: 'invalid' };
  if (entry.used) return { valid: false, reason: 'already_used' };
  if (Date.now() > entry.expiresAt) {
    otpStore.delete(code);
    return { valid: false, reason: 'expired' };
  }
  // Mark used immediately (single-use)
  entry.used = true;
  return { valid: true, userId: entry.userId };
}

function getRemainingSeconds(code) {
  const entry = otpStore.get(code);
  if (!entry) return 0;
  return Math.max(0, Math.ceil((entry.expiresAt - Date.now()) / 1000));
}

module.exports = { createOTP, validateOTP, getRemainingSeconds };
