# ⚔️ CSR System · Powered by Breezy

One bot. All your guild servers. Built to last.

---

## 🚀 Quick Start

```bash
pip install -r requirements.txt
cp .env.example .env     # Fill in your keys
python bot.py
```

Then in Discord:
```
.csrsbor      → Setup SBO:R server
.csrbh2       → Setup Blue Heater 2 server
.csrbf        → Setup Blox Fruits server
.csrwarframe  → Setup Warframe server
```

---

## 📋 Keys You Need

| Key | Where to get it | Cost |
|---|---|---|
| `BOT_TOKEN` | discord.com/developers | Free |
| `GEMINI_API_KEY` | aistudio.google.com | Free |
| `GROQ_API_KEY` | console.groq.com | Free |
| `YOUTUBE_API_KEY` | console.cloud.google.com | Free |
| `STEAM_API_KEY` | steamcommunity.com/dev/apikey | Free |

---

## 🌐 Supported Servers

| Command | Game | Platform |
|---|---|---|
| `.csrsbor` | Sword Blox Online Rebirth | Roblox |
| `.csrbh2` | Blue Heater 2 | Roblox |
| `.csrbf` | Blox Fruits | Roblox |
| `.csrwarframe` | Warframe | Steam + Discord |

---

## ✨ Features

- 🔐 OTP Verification (SQLite stored, survives restarts)
- 🪪 CSR Accounts (cross-server, bcrypt passwords)
- 🎴 ID Card (generated image, DMed on signup)
- 🎮 Game presence tracking (Roblox API / Steam API)
- 🔍 Wiki chatbot (@mention or /ask)
- 📰 Group scraper + Groq phrasing
- 🌐 Portal channel per server
- ⭐ Cross-server reputation system
- 🛡️ Full moderation suite
- 📅 Event system with DM blasts
- 📋 Full logging system
- 💾 SQLite + Discord channel backup
- 🔄 Auto-restore from backup if DB corrupts
- 🚦 Rate limiting everywhere
- 📬 Pending action retry queue

---

## 🛡️ Admin Guide

Use `/adminhelp` in Discord — it's the full bible:

```
/adminhelp setup       → Server setup guide
/adminhelp verify      → Verification system
/adminhelp accounts    → CSR account system
/adminhelp moderation  → Mod commands
/adminhelp presence    → Game tracking
/adminhelp wiki        → Chatbot guide
/adminhelp events      → Event system
/adminhelp crossserver → Cross-server features
/adminhelp logs        → Logging guide
/adminhelp all         → Everything at once
```

---

## 🏠 Hosting on Wispbyte

```bash
# Install deps
pip install -r requirements.txt

# Run
python bot.py
```

Make sure `WEB_BASE_URL` points to your public Wispbyte URL so the verification page works.

---

*CSR System · Powered by Breezy · Built for Champions*
