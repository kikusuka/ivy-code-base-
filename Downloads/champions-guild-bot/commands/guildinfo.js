const { SlashCommandBuilder, EmbedBuilder, PermissionFlagsBits } = require('discord.js');
const { db } = require('../utils/db');

module.exports = {
  data: new SlashCommandBuilder()
    .setName('info')
    .setDescription('Learn about this guild.')
    .addSubcommand(sub =>
      sub.setName('view').setDescription('View guild information')
    )
    .addSubcommand(sub =>
      sub.setName('addabout')
        .setDescription('[Admin] Add or update guild info')
        .addStringOption(opt =>
          opt.setName('section').setDescription('Section name (e.g. "About Us", "Rules")').setRequired(true)
        )
        .addStringOption(opt =>
          opt.setName('content').setDescription('Content for this section').setRequired(true)
        )
    )
    .addSubcommand(sub =>
      sub.setName('remove')
        .setDescription('[Admin] Remove a guild info section')
        .addStringOption(opt =>
          opt.setName('section').setDescription('Section name to remove').setRequired(true)
        )
    ),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();

    if (sub === 'view') {
      const info = db.getGuildInfo();
      const sections = info.sections || {};
      const embed = new EmbedBuilder()
        .setColor(0x2d7dd2)
        .setTitle(`🏰 ${process.env.GUILD_NAME || 'Guild Info'}`)
        .setThumbnail(interaction.guild.iconURL({ dynamic: true }))
        .setTimestamp()
        .setFooter({ text: 'Champions of the Shattered Realm' });

      if (Object.keys(sections).length === 0) {
        embed.setDescription('*No guild info has been added yet. Admins can use `/info addabout` to add info.*');
      } else {
        for (const [name, content] of Object.entries(sections)) {
          embed.addFields({ name, value: content.slice(0, 1024) });
        }
      }
      return interaction.reply({ embeds: [embed] });
    }

    if (sub === 'addabout') {
      if (!interaction.member.permissions.has(PermissionFlagsBits.Administrator)) {
        return interaction.reply({ content: '❌ Only admins can add guild info.', ephemeral: true });
      }
      const section = interaction.options.getString('section');
      const content = interaction.options.getString('content');
      const info = db.getGuildInfo();
      if (!info.sections) info.sections = {};
      info.sections[section] = content;
      db.setGuildInfo(info);
      return interaction.reply({ content: `✅ Guild info section **"${section}"** has been saved!`, ephemeral: true });
    }

    if (sub === 'remove') {
      if (!interaction.member.permissions.has(PermissionFlagsBits.Administrator)) {
        return interaction.reply({ content: '❌ Only admins can remove guild info.', ephemeral: true });
      }
      const section = interaction.options.getString('section');
      const info = db.getGuildInfo();
      if (!info.sections?.[section]) {
        return interaction.reply({ content: `❌ Section **"${section}"** not found.`, ephemeral: true });
      }
      delete info.sections[section];
      db.setGuildInfo(info);
      return interaction.reply({ content: `🗑️ Section **"${section}"** removed.`, ephemeral: true });
    }
  },
};
