const { EmbedBuilder } = require('discord.js');
const { db } = require('../utils/db');

module.exports = {
  name: 'guildMemberAdd',
  async execute(member, client) {
    const user = member.user;
    const guild = member.guild;

    // 1. Assign permanent Guild ID
    const guildId = db.assignGuildId(user.id, user.tag);

    // 2. Assign Unverified role
    if (process.env.UNVERIFIED_ROLE_ID) {
      await member.roles.add(process.env.UNVERIFIED_ROLE_ID).catch(() => {});
    }

    // 3. Send welcome DM
    const welcomeEmbed = new EmbedBuilder()
      .setColor(0x2d7dd2)
      .setTitle(`⚔️ Welcome to ${process.env.GUILD_NAME || guild.name}!`)
      .setThumbnail(guild.iconURL({ dynamic: true }))
      .setDescription(
        `Hey **${user.username}**, we're glad you're here! 🎉\n\n` +
        `**${process.env.GUILD_NAME || guild.name}** is a community of champions ready for battle.\n\n` +
        `🪪 **Your Guild ID:** \`#${guildId}\` — this is permanent and unique to you!\n\n` +
        `**Getting started:**\n` +
        `› Use \`/info view\` to learn about the guild\n` +
        `› Use \`/serverinfo\` to see server stats\n` +
        `› Head to the server and get verified to unlock all channels!\n\n` +
        `See you on the battlefield! ⚔️`
      )
      .setFooter({ text: 'Champions of the Shattered Realm' })
      .setTimestamp();

    // 4. Send verification nudge DM (slight delay)
    const verifyEmbed = new EmbedBuilder()
      .setColor(0x4facf7)
      .setTitle('🔐 One More Step — Verify Your Identity')
      .setDescription(
        `To unlock all channels in **${process.env.GUILD_NAME || guild.name}**, you need to verify!\n\n` +
        `**How:**\n` +
        `1. Go to the **#verification** channel in the server\n` +
        `2. Click the **Verify Me 🛡️** button\n` +
        `3. Enter the 6-digit code I'll send you\n` +
        `4. Done! 🎉\n\n` +
        `> Codes are valid for **2 minutes** only — have the page ready before clicking!`
      )
      .setFooter({ text: 'Champions of the Shattered Realm · Verification' });

    try {
      await user.send({ embeds: [welcomeEmbed] });
      // Wait 3 seconds then send the verify nudge
      await new Promise(r => setTimeout(r, 3000));
      await user.send({ embeds: [verifyEmbed] });
    } catch {
      // DMs closed — silently fail, they'll see the verification panel in server
    }

    // 5. Log to backup/welcome channel
    if (process.env.WELCOME_CHANNEL_ID) {
      const ch = guild.channels.cache.get(process.env.WELCOME_CHANNEL_ID);
      if (ch) {
        ch.send({
          embeds: [new EmbedBuilder()
            .setColor(0x2d7dd2)
            .setDescription(`👋 **${user.tag}** has joined! Guild ID: \`#${guildId}\``)]
        }).catch(() => {});
      }
    }
  },
};
