const express = require('express');
const path = require('path');
const { validateOTP } = require('../utils/otp');
const { db } = require('../utils/db');

let _client = null;
const app = express();

app.use(express.json());
app.use(express.urlencoded({ extended: true }));
app.use(express.static(path.join(__dirname, 'public')));

// ── Serve verification page ──────────────────────────────────────────────────
app.get('/verify', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'verify.html'));
});

// ── OTP submission endpoint ──────────────────────────────────────────────────
app.post('/api/verify', async (req, res) => {
  const { code } = req.body;
  if (!code || typeof code !== 'string') {
    return res.json({ success: false, message: 'Please enter your OTP code.' });
  }

  const result = validateOTP(code.trim());

  if (!result.valid) {
    const messages = {
      invalid: '❌ That code is invalid. Check your DM and try again.',
      expired: '⏰ Your code has expired (2-minute limit). Use .getverified to get a new one.',
      already_used: '✅ This code has already been used.',
    };
    return res.json({ success: false, message: messages[result.reason] || 'Invalid code.' });
  }

  // Assign roles in Discord
  try {
    const guild = await _client.guilds.fetch(process.env.GUILD_ID);
    const member = await guild.members.fetch(result.userId);

    // Remove unverified role, add verified role
    if (process.env.UNVERIFIED_ROLE_ID) {
      await member.roles.remove(process.env.UNVERIFIED_ROLE_ID).catch(() => {});
    }
    if (process.env.VERIFIED_ROLE_ID) {
      await member.roles.add(process.env.VERIFIED_ROLE_ID).catch(() => {});
    }

    // Update DB
    db.setUser(result.userId, { verified: true, verifiedAt: new Date().toISOString() });

    // Send congrats DM
    await member.send(
      '✅ **You\'re verified!** Welcome to **' + (process.env.GUILD_NAME || 'the guild') + '**! ' +
      'You now have full access. Type `/info` in the server to learn more about us. 🎉'
    ).catch(() => {});

    return res.json({ success: true, message: '🎉 Verified! You can close this page and head back to the server.' });
  } catch (err) {
    console.error('Verification error:', err);
    return res.json({ success: false, message: '⚠️ Something went wrong. Please contact an admin.' });
  }
});

async function startWebServer(client) {
  _client = client;
  const port = process.env.WEB_PORT || 3000;
  app.listen(port, () => console.log(`🌐 Verification web server running on port ${port}`));
}

module.exports = { startWebServer };
