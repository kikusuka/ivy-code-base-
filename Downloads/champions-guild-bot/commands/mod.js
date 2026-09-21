const { SlashCommandBuilder, EmbedBuilder, PermissionFlagsBits } = require('discord.js');

const modEmbed = (action, target, mod, reason, color) =>
  new EmbedBuilder()
    .setColor(color)
    .setTitle(`🛡️ ${action}`)
    .addFields(
      { name: 'Member', value: `${target.user.tag} (\`${target.id}\`)`, inline: true },
      { name: 'Moderator', value: mod.tag, inline: true },
      { name: 'Reason', value: reason || 'No reason provided' }
    )
    .setThumbnail(target.user.displayAvatarURL())
    .setTimestamp();

const logAction = async (guild, embed) => {
  if (!process.env.LOG_CHANNEL_ID) return;
  const ch = guild.channels.cache.get(process.env.LOG_CHANNEL_ID);
  if (ch) ch.send({ embeds: [embed] }).catch(() => {});
};

module.exports = {
  data: new SlashCommandBuilder()
    .setName('mod')
    .setDescription('Moderation tools')
    .setDefaultMemberPermissions(PermissionFlagsBits.ModerateMembers)

    .addSubcommand(sub => sub.setName('ban')
      .setDescription('Ban a member')
      .addUserOption(o => o.setName('target').setDescription('Member to ban').setRequired(true))
      .addStringOption(o => o.setName('reason').setDescription('Reason for ban'))
      .addIntegerOption(o => o.setName('days').setDescription('Delete messages (days, 0-7)').setMinValue(0).setMaxValue(7))
    )
    .addSubcommand(sub => sub.setName('kick')
      .setDescription('Kick a member')
      .addUserOption(o => o.setName('target').setDescription('Member to kick').setRequired(true))
      .addStringOption(o => o.setName('reason').setDescription('Reason for kick'))
    )
    .addSubcommand(sub => sub.setName('mute')
      .setDescription('Timeout (mute) a member')
      .addUserOption(o => o.setName('target').setDescription('Member to mute').setRequired(true))
      .addIntegerOption(o => o.setName('minutes').setDescription('Duration in minutes (max 40320)').setRequired(true).setMinValue(1).setMaxValue(40320))
      .addStringOption(o => o.setName('reason').setDescription('Reason'))
    )
    .addSubcommand(sub => sub.setName('unmute')
      .setDescription('Remove a timeout from a member')
      .addUserOption(o => o.setName('target').setDescription('Member to unmute').setRequired(true))
    )
    .addSubcommand(sub => sub.setName('warn')
      .setDescription('Warn a member (DMs them)')
      .addUserOption(o => o.setName('target').setDescription('Member to warn').setRequired(true))
      .addStringOption(o => o.setName('reason').setDescription('Warning reason').setRequired(true))
    )
    .addSubcommand(sub => sub.setName('purge')
      .setDescription('Delete messages in bulk')
      .addIntegerOption(o => o.setName('amount').setDescription('Number of messages (1-100)').setRequired(true).setMinValue(1).setMaxValue(100))
    ),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const mod = interaction.user;

    if (sub === 'ban') {
      const target = interaction.options.getMember('target');
      const reason = interaction.options.getString('reason') || 'No reason provided';
      const days = interaction.options.getInteger('days') ?? 0;
      if (!target.bannable) return interaction.reply({ content: '❌ I cannot ban this member.', ephemeral: true });
      await target.user.send(`🔨 You have been **banned** from **${interaction.guild.name}**.\nReason: ${reason}`).catch(() => {});
      await target.ban({ deleteMessageSeconds: days * 86400, reason });
      const embed = modEmbed('Member Banned 🔨', target, mod, reason, 0xe74c3c);
      await logAction(interaction.guild, embed);
      return interaction.reply({ embeds: [embed] });
    }

    if (sub === 'kick') {
      const target = interaction.options.getMember('target');
      const reason = interaction.options.getString('reason') || 'No reason provided';
      if (!target.kickable) return interaction.reply({ content: '❌ I cannot kick this member.', ephemeral: true });
      await target.user.send(`👟 You have been **kicked** from **${interaction.guild.name}**.\nReason: ${reason}`).catch(() => {});
      await target.kick(reason);
      const embed = modEmbed('Member Kicked 👟', target, mod, reason, 0xe67e22);
      await logAction(interaction.guild, embed);
      return interaction.reply({ embeds: [embed] });
    }

    if (sub === 'mute') {
      const target = interaction.options.getMember('target');
      const minutes = interaction.options.getInteger('minutes');
      const reason = interaction.options.getString('reason') || 'No reason provided';
      if (!target.moderatable) return interaction.reply({ content: '❌ I cannot mute this member.', ephemeral: true });
      await target.timeout(minutes * 60 * 1000, reason);
      await target.user.send(`🔇 You have been **muted** in **${interaction.guild.name}** for ${minutes} minute(s).\nReason: ${reason}`).catch(() => {});
      const embed = modEmbed(`Member Muted 🔇 (${minutes}min)`, target, mod, reason, 0xf39c12);
      await logAction(interaction.guild, embed);
      return interaction.reply({ embeds: [embed] });
    }

    if (sub === 'unmute') {
      const target = interaction.options.getMember('target');
      await target.timeout(null);
      await target.user.send(`🔊 Your timeout in **${interaction.guild.name}** has been removed.`).catch(() => {});
      const embed = modEmbed('Timeout Removed 🔊', target, mod, 'Unmuted by moderator', 0x2ecc71);
      await logAction(interaction.guild, embed);
      return interaction.reply({ embeds: [embed] });
    }

    if (sub === 'warn') {
      const target = interaction.options.getMember('target');
      const reason = interaction.options.getString('reason');
      await target.user.send(
        `⚠️ **Warning from ${interaction.guild.name}**\n\nReason: **${reason}**\n\n*Please review the server rules to avoid further action.*`
      ).catch(() => {});
      const embed = modEmbed('Member Warned ⚠️', target, mod, reason, 0xf1c40f);
      await logAction(interaction.guild, embed);
      return interaction.reply({ embeds: [embed] });
    }

    if (sub === 'purge') {
      const amount = interaction.options.getInteger('amount');
      await interaction.channel.bulkDelete(amount, true);
      return interaction.reply({ content: `🗑️ Deleted **${amount}** messages.`, ephemeral: true });
    }
  },
};
