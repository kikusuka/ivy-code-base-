const { REST, Routes } = require('discord.js');
const fs = require('fs');
const path = require('path');

module.exports = {
  name: 'ready',
  once: true,
  async execute(client) {
    console.log(`✅ Logged in as ${client.user.tag}`);
    client.user.setPresence({
      activities: [{ name: '⚔️ Champions of the Shattered Realm', type: 0 }],
      status: 'online',
    });

    // Auto-register slash commands on startup
    const commands = [];
    const commandFiles = fs.readdirSync(path.join(__dirname, '../commands')).filter(f => f.endsWith('.js'));
    for (const file of commandFiles) {
      const cmd = require(`../commands/${file}`);
      if (cmd.data) commands.push(cmd.data.toJSON());
    }

    const rest = new REST({ version: '10' }).setToken(process.env.BOT_TOKEN);
    try {
      await rest.put(
        Routes.applicationGuildCommands(process.env.CLIENT_ID, process.env.GUILD_ID),
        { body: commands }
      );
      console.log(`✅ Registered ${commands.length} slash commands`);
    } catch (err) {
      console.error('Failed to register commands:', err);
    }
  },
};
