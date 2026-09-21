require('dotenv').config();
const { Client, GatewayIntentBits, Collection, REST, Routes } = require('discord.js');
const fs = require('fs');
const path = require('path');
const { startWebServer } = require('./web/server');
const { initDB } = require('./utils/db');

const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildMembers,
    GatewayIntentBits.GuildMessages,
    GatewayIntentBits.MessageContent,
    GatewayIntentBits.DirectMessages,
  ],
});

client.commands = new Collection();
client.prefixCommands = new Collection();

// Load slash commands
const commandFiles = fs.readdirSync('./commands').filter(f => f.endsWith('.js'));
for (const file of commandFiles) {
  const cmd = require(`./commands/${file}`);
  if (cmd.data) client.commands.set(cmd.data.name, cmd);
  if (cmd.prefixName) client.prefixCommands.set(cmd.prefixName, cmd);
}

// Load events
const eventFiles = fs.readdirSync('./events').filter(f => f.endsWith('.js'));
for (const file of eventFiles) {
  const event = require(`./events/${file}`);
  if (event.once) client.once(event.name, (...args) => event.execute(...args, client));
  else client.on(event.name, (...args) => event.execute(...args, client));
}

// Prefix command handler (.verifysetup etc)
client.on('messageCreate', async (message) => {
  if (message.author.bot) return;
  if (!message.content.startsWith('.')) return;
  const args = message.content.slice(1).trim().split(/ +/);
  const commandName = args.shift().toLowerCase();
  const cmd = client.prefixCommands.get(commandName);
  if (!cmd) return;
  try {
    await cmd.executePrefixCommand(message, args, client);
  } catch (err) {
    console.error(err);
    message.reply('❌ Something went wrong running that command.');
  }
});

(async () => {
  await initDB(client);
  await startWebServer(client);
  await client.login(process.env.BOT_TOKEN);
  console.log('✅ Bot is online!');
})();
