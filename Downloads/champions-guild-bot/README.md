# ⚔️ Champions of the Shattered Realm — Guild Bot

A full-featured Discord bot with verification, member IDs, scouting, moderation, events, and more.

---

## 🚀 Setup Guide

### 1. Prerequisites
- Node.js v18+
- A Discord application with a bot token ([Discord Developer Portal](https://discord.com/developers/applications))

### 2. Install dependencies
```bash
npm install
```

### 3. Configure environment
```bash
cp .env.example .env
```
Fill in every value in `.env`:

| Variable | How to get it |
|---|---|
| `BOT_TOKEN` | Discord Developer Portal → Bot → Token |
| `CLIENT_ID` | Developer Portal → General Info → Application ID |
| `GUILD_ID` | Right-click your server → Copy Server ID (enable Dev Mode in settings) |
| `VERIFIED_ROLE_ID` | Right-click the Verified role → Copy Role ID |
| `UNVERIFIED_ROLE_ID` | Right-click the Unverified role → Copy Role ID |
| `BACKUP_CHANNEL_ID` | Right-click your backup channel → Copy Channel ID |
| `LOG_CHANNEL_ID` | Right-click your log channel → Copy Channel ID |
| `WELCOME_CHANNEL_ID` | Right-click your welcome channel → Copy Channel ID |
| `WEB_PORT` | Port for the verification web page (default: 3000) |
| `WEB_BASE_URL` | URL where the bot is hosted (e.g. `http://localhost:3000` or `https://yourserver.com`) |

### 4. Discord bot permissions
In the Developer Portal, enable these **Privileged Gateway Intents**:
- ✅ Server Members Intent
- ✅ Message Content Intent
- ✅ Presence Intent

Bot requires these permissions:
- Manage Roles, Kick Members, Ban Members, Moderate Members
- Send Messages, Read Message History, Embed Links
- Manage Messages (for purge)

### 5. Run the bot
```bash
npm start
# or for development with auto-restart:
npm run dev
```

---

## 📖 Command Reference

### User Commands
| Command | Description |
|---|---|
| `/getverified` | Start verification — get OTP via DM |
| `/info view` | View guild information |
| `/serverinfo` | Server stats (members, bots, channels) |
| `/scout @user` | Scout a member's profile, roles & history |
| `/event list` | View upcoming events |
| `/help` | User command list |

### Admin Commands
| Command | Description |
|---|---|
| `.verifysetup` | Post verification panel in current channel |
| `/info addabout [section] [content]` | Add/update guild info section |
| `/info remove [section]` | Remove guild info section |
| `/mod ban @user [reason] [days]` | Ban a member |
| `/mod kick @user [reason]` | Kick a member |
| `/mod mute @user [minutes] [reason]` | Timeout a member |
| `/mod unmute @user` | Remove timeout |
| `/mod warn @user [reason]` | Warn via DM |
| `/mod purge [amount]` | Bulk delete messages (1–100) |
| `/event create` | Create event + send DM reminders |
| `/help admin` | Admin command list |
| `/sync` | Re-register all slash commands |

---

## 🔐 How Verification Works

1. Admin runs `.verifysetup` → Verification panel appears in channel
2. Member clicks **Verify Me 🛡️**
3. Bot sends a **6-digit OTP** to member's DMs
4. Member opens verification page (`/verify`) and enters their code
5. Code is validated (expires in **2 minutes**, single-use)
6. Member gets Verified role, Unverified role removed
7. Bot sends a welcome DM confirming verification

---

## 🪪 Guild ID System

- Every member gets a permanent **5-digit Guild ID** (e.g. `#00042`) on join
- IDs are stored in `data/users.json` and backed up to Discord
- IDs persist even if a member leaves and rejoins
- View with `/scout @user`

---

## 💾 Data & Backup

All data is stored locally in `data/`:
- `users.json` — member profiles, IDs, role history
- `guild.json` — guild info sections
- `events.json` — event history

Every write is also posted/updated in your `BACKUP_CHANNEL_ID` Discord channel as a JSON snapshot — so even if the file is lost, your data is recoverable.

---

## 🌐 Hosting

For production, you'll need:
- A server/VPS (e.g. Railway, DigitalOcean, Render)
- A domain or public URL for `WEB_BASE_URL`
- Port 3000 (or your chosen `WEB_PORT`) exposed publicly

For local testing, use [ngrok](https://ngrok.com/):
```bash
ngrok http 3000
# Copy the https URL to WEB_BASE_URL in .env
```
