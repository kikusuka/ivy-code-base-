const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');

module.exports = {
  data: new SlashCommandBuilder()
    .setName('serverinfo')
    .setDescription('Shows stats about this server — members, bots, channels, and more.'),

  async execute(interaction) {
    await interaction.deferReply();
    const guild = interaction.guild;
    await guild.members.fetch(); // Ensure full member cache

    const totalMembers = guild.memberCount;
    const bots = guild.members.cache.filter(m => m.user.bot).size;
    const humans = totalMembers - bots;
    const online = guild.members.cache.filter(m => m.presence?.status === 'online').size;
    const roles = guild.roles.cache.size - 1; // exclude @everyone
    const channels = guild.channels.cache.size;
    const textChannels = guild.channels.cache.filter(c => c.type === 0).size;
    const voiceChannels = guild.channels.cache.filter(c => c.type === 2).size;
    const createdAt = `<t:${Math.floor(guild.createdTimestamp / 1000)}:D>`;
    const owner = await guild.fetchOwner();

    const embed = new EmbedBuilder()
      .setColor(0x2d7dd2)
      .setTitle(`📊 ${guild.name} — Server Info`)
      .setThumbnail(guild.iconURL({ dynamic: true, size: 256 }))
      .addFields(
        { name: '👥 Total Members', value: `\`${totalMembers}\``, inline: true },
        { name: '🧑 Humans',        value: `\`${humans}\``,       inline: true },
        { name: '🤖 Bots',          value: `\`${bots}\``,         inline: true },
        { name: '🟢 Online',        value: `\`${online}\``,       inline: true },
        { name: '📜 Roles',         value: `\`${roles}\``,        inline: true },
        { name: '📁 Channels',      value: `\`${channels}\` (💬 ${textChannels} · 🔊 ${voiceChannels})`, inline: true },
        { name: '🎖️ Boost Level',   value: `\`Tier ${guild.premiumTier}\` (${guild.premiumSubscriptionCount} boosts)`, inline: true },
        { name: '👑 Owner',         value: `${owner.user.tag}`, inline: true },
        { name: '📅 Created',       value: createdAt, inline: true },
      )
      .setFooter({ text: 'Champions of the Shattered Realm · Server Info' })
      .setTimestamp();

    await interaction.editReply({ embeds: [embed] });
  },
};
