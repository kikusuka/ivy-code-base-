const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');
const { db } = require('../utils/db');

module.exports = {
  data: new SlashCommandBuilder()
    .setName('scout')
    .setDescription('Scout a member — see their profile, roles, and history.')
    .addUserOption(opt =>
      opt.setName('target').setDescription('The member to scout').setRequired(true)
    ),

  async execute(interaction) {
    await interaction.deferReply();
    const target = interaction.options.getMember('target');
    if (!target) return interaction.editReply('❌ Member not found.');

    const user = target.user;
    const dbUser = db.getUser(user.id);
    const guildId = dbUser?.guildId ?? 'Not Registered';
    const verified = dbUser?.verified ? '✅ Verified' : '❌ Unverified';
    const registeredAt = dbUser?.registeredAt
      ? `<t:${Math.floor(new Date(dbUser.registeredAt).getTime() / 1000)}:D>`
      : 'N/A';

    // Role list (up to 15, skip @everyone)
    const roles = target.roles.cache
      .filter(r => r.id !== interaction.guild.id)
      .sort((a, b) => b.position - a.position)
      .map(r => `<@&${r.id}>`)
      .slice(0, 15)
      .join(' ') || '*No roles*';

    // Role history from DB
    const roleHistory = dbUser?.roleHistory?.slice(-5).reverse()
      .map(h => `\`${new Date(h.changedAt).toLocaleDateString()}\` ${h.action} ${h.role}`)
      .join('\n') || '*No history tracked*';

    // Username history
    const usernameHistory = dbUser?.usernameHistory?.slice(-5).reverse()
      .map(h => `\`${new Date(h.changedAt).toLocaleDateString()}\` → \`${h.name}\``)
      .join('\n') || `\`${user.tag}\``;

    const joinedAt = target.joinedAt
      ? `<t:${Math.floor(target.joinedTimestamp / 1000)}:R>`
      : 'Unknown';
    const accountCreated = `<t:${Math.floor(user.createdTimestamp / 1000)}:D>`;

    const embed = new EmbedBuilder()
      .setColor(target.displayHexColor !== '#000000' ? target.displayHexColor : 0x2d7dd2)
      .setTitle(`🔍 Scout: ${user.username}`)
      .setThumbnail(user.displayAvatarURL({ dynamic: true, size: 256 }))
      .addFields(
        { name: '🆔 Discord Tag',    value: `\`${user.tag}\``,      inline: true },
        { name: '🎖️ Guild ID',       value: `\`#${guildId}\``,      inline: true },
        { name: '🔐 Status',         value: verified,               inline: true },
        { name: '📅 Joined Server',  value: joinedAt,               inline: true },
        { name: '🎂 Account Created',value: accountCreated,         inline: true },
        { name: '📝 Registered',     value: registeredAt,           inline: true },
        { name: `📜 Roles [${target.roles.cache.size - 1}]`, value: roles },
        { name: '🔄 Role History (last 5)', value: roleHistory },
        { name: '📛 Username History', value: usernameHistory },
      )
      .setFooter({ text: `Discord ID: ${user.id}` })
      .setTimestamp();

    await interaction.editReply({ embeds: [embed] });
  },
};
