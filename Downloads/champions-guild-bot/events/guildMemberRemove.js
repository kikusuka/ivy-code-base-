const { EmbedBuilder } = require('discord.js');
const { db } = require('../utils/db');

module.exports = {
  name: 'guildMemberRemove',
  async execute(member, client) {
    const user = member.user;
    if (user.bot) return;

    // Update DB — mark as left but KEEP their record + Guild ID
    db.setUser(user.id, {
      leftAt: new Date().toISOString(),
      currentMember: false,
    });

    // Send goodbye DM with feedback request
    const embed = new EmbedBuilder()
      .setColor(0x2d7dd2)
      .setTitle('💙 We\'re Sorry to See You Go...')
      .setDescription(
        `Hey **${user.username}**,\n\n` +
        `We noticed you left **${process.env.GUILD_NAME || 'the guild'}** and we\'re genuinely sad about it. 😔\n\n` +
        `We\'re always trying to improve, and your feedback means a lot to us. If you\'re willing, we\'d love to know:\n\n` +
        `**› Why did you leave?**\n` +
        `**› What could we have done better?**\n` +
        `**› Any suggestions for improvement?**\n\n` +
        `Just reply to this message — a real person will read it. 💙\n\n` +
        `If you ever want to come back, you\'re always welcome. Your Guild ID **\`#${db.getUser(user.id)?.guildId || '?'}\`** is still yours.\n\n` +
        `*Take care, Champion.* ⚔️`
      )
      .setFooter({ text: 'Champions of the Shattered Realm' })
      .setTimestamp();

    try {
      await user.send({ embeds: [embed] });
    } catch {
      // DMs closed
    }

    // Log departure
    if (process.env.LOG_CHANNEL_ID) {
      const guild = client.guilds.cache.get(process.env.GUILD_ID);
      const ch = guild?.channels.cache.get(process.env.LOG_CHANNEL_ID);
      if (ch) {
        ch.send({
          embeds: [new EmbedBuilder()
            .setColor(0xf87171)
            .setDescription(`📤 **${user.tag}** left the server. Guild ID: \`#${db.getUser(user.id)?.guildId || 'N/A'}\``)
            .setTimestamp()]
        }).catch(() => {});
      }
    }
  },
};
