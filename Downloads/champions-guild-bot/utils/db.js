/**
 * Lightweight JSON "database" with Discord channel backup.
 * All data lives in ./data/*.json locally.
 * On every write, a snapshot is posted/edited in the backup channel.
 */

const fs = require('fs');
const path = require('path');

const DATA_DIR = path.join(__dirname, '../data');
if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });

const FILES = {
  users:  path.join(DATA_DIR, 'users.json'),
  guild:  path.join(DATA_DIR, 'guild.json'),
  events: path.join(DATA_DIR, 'events.json'),
};

let _client = null;
// Map of filename → Discord message ID for backup messages
const backupMsgIds = {};

function readFile(file) {
  if (!fs.existsSync(file)) return {};
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); }
  catch { return {}; }
}

function writeFileSync(file, data) {
  fs.writeFileSync(file, JSON.stringify(data, null, 2));
}

// ── Public API ──────────────────────────────────────────────────────────────

const db = {
  // USERS
  getUser(userId) {
    const users = readFile(FILES.users);
    return users[userId] || null;
  },
  setUser(userId, data) {
    const users = readFile(FILES.users);
    users[userId] = { ...users[userId], ...data };
    writeFileSync(FILES.users, users);
    backupData('users', users);
    return users[userId];
  },
  getAllUsers() { return readFile(FILES.users); },

  // Assign a permanent numeric ID if not already assigned
  assignGuildId(userId, tag) {
    let user = this.getUser(userId);
    if (user?.guildId) return user.guildId;
    const users = readFile(FILES.users);
    const nextId = Object.keys(users).length + 1;
    const guildId = String(nextId).padStart(5, '0'); // e.g. 00042
    this.setUser(userId, {
      discordId: userId,
      tag,
      guildId,
      registeredAt: new Date().toISOString(),
      verified: false,
      roles: [],
      roleHistory: [],
      usernameHistory: [{ name: tag, changedAt: new Date().toISOString() }],
    });
    return guildId;
  },

  // GUILD INFO
  getGuildInfo() { return readFile(FILES.guild); },
  setGuildInfo(data) {
    const current = readFile(FILES.guild);
    const updated = { ...current, ...data };
    writeFileSync(FILES.guild, updated);
    backupData('guild', updated);
    return updated;
  },

  // EVENTS
  getEvents() {
    const e = readFile(FILES.events);
    return Array.isArray(e.list) ? e.list : [];
  },
  addEvent(eventData) {
    const data = readFile(FILES.events);
    if (!data.list) data.list = [];
    eventData.id = Date.now().toString();
    data.list.push(eventData);
    writeFileSync(FILES.events, data);
    backupData('events', data);
    return eventData;
  },
};

// ── Backup to Discord channel ────────────────────────────────────────────────

async function backupData(label, data) {
  if (!_client || !process.env.BACKUP_CHANNEL_ID) return;
  try {
    const channel = await _client.channels.fetch(process.env.BACKUP_CHANNEL_ID);
    if (!channel) return;
    const content = `\`\`\`json\n[BACKUP:${label.toUpperCase()}] ${new Date().toISOString()}\n${JSON.stringify(data, null, 2).slice(0, 1800)}\n\`\`\``;
    if (backupMsgIds[label]) {
      try {
        const msg = await channel.messages.fetch(backupMsgIds[label]);
        await msg.edit(content);
        return;
      } catch { /* message deleted, post a new one */ }
    }
    const msg = await channel.send(content);
    backupMsgIds[label] = msg.id;
  } catch (err) {
    console.warn('Backup channel write failed:', err.message);
  }
}

async function initDB(client) {
  _client = client;
  // Ensure all data files exist
  for (const file of Object.values(FILES)) {
    if (!fs.existsSync(file)) {
      fs.writeFileSync(file, label === 'events' ? '{"list":[]}' : '{}');
    }
  }
  console.log('✅ DB initialised');
}

module.exports = { db, initDB };
