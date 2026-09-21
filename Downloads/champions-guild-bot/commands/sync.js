const { SlashCommandBuilder, PermissionFlagsBits, REST, Routes } = require('discord.js');
const fs = require('fs');
const path = require('path');

module.exports = {
  data: new SlashCommandBuilder()
    .setName('sync')
    .setDescription('Re-register all slash commands (Admin only)')
    .setDefaultMemberPermissions(PermissionFlagsBits.Administrator),

  async execute(interaction) {
    await interaction.deferReply({ ephemeral: true });

    const commands = [];
    const commandFiles = fs.readdirSync(path.join(__dirname)).filter(f => f.endsWith('.js'));
    for (const file of commandFiles) {
      const cmd = require(`./${file}`);
      if (cmd.data) commands.push(cmd.data.toJSON());
    }

    const rest = new REST({ version: '10' }).setToken(process.env.BOT_TOKEN);
    try {
      await rest.put(
        Routes.applicationGuildCommands(process.env.CLIENT_ID, process.env.GUILD_ID),
        { body: commands }
      );
      await interaction.editReply(`✅ Synced **${commands.length}** slash commands to this server!`);
    } catch (err) {
      console.error(err);
      await interaction.editReply('❌ Sync failed. Check console for details.');
    }
  },
};
