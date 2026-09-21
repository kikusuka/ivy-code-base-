const { SlashCommandBuilder, PermissionFlagsBits, EmbedBuilder } = require('discord.js');
const { createOTP } = require('../utils/otp');
const { db } = require('../utils/db');

// ── .verifysetup (admin prefix command) ─────────────────────────────────────
// Posts the verification embed in the current channel

const verifysetupEmbed = (guildName) => new EmbedBuilder()
  .setColor(0x2d7dd2)
  .setTitle('🛡️ Guild Verification')
  .setDescription(
    `Welcome to **${guildName || 'the Guild'}**!\n\n` +
    `To gain full access, you need to verify your identity.\n\n` +
    `**How it works:**\n` +
    `1. Click **Verify Me** below\n` +
    `2. The bot will DM you a 6-digit code\n` +
    `3. Enter the code on the verification page\n` +
    `4. Done! You'll get the Verified role instantly ✅\n\n` +
    `> ⏰ Codes expire in **2 minutes** · Make sure your DMs are open`
  )
  .setFooter({ text: 'Champions of the Shattered Realm · Verification System' })
  .setTimestamp();

module.exports = {
  // Prefix command: .verifysetup
  prefixName: 'verifysetup',
  async executePrefixCommand(message) {
    if (!message.member.permissions.has(PermissionFlagsBits.Administrator)) {
      return message.reply('❌ Only admins can run `.verifysetup`.');
    }
    const { ActionRowBuilder, ButtonBuilder, ButtonStyle } = require('discord.js');
    const row = new ActionRowBuilder().addComponents(
      new ButtonBuilder()
        .setCustomId('start_verify')
        .setLabel('Verify Me 🛡️')
        .setStyle(ButtonStyle.Primary)
    );
    await message.channel.send({ embeds: [verifysetupEmbed(process.env.GUILD_NAME)], components: [row] });
    await message.delete().catch(() => {});
  },

  // Slash command: /getverified — triggers the OTP flow for the user
  data: new SlashCommandBuilder()
    .setName('getverified')
    .setDescription('Start the verification process — check your DMs for a code!'),

  async execute(interaction) {
    const member = interaction.member;
    const user = interaction.user;

    // Check if already verified
    const dbUser = db.getUser(user.id);
    if (dbUser?.verified) {
      return interaction.reply({ content: '✅ You are already verified!', ephemeral: true });
    }

    await interaction.deferReply({ ephemeral: true });

    const { code, expiresAt } = createOTP(user.id);
    const verifyUrl = `${process.env.WEB_BASE_URL}/verify`;
    const expiresIn = Math.floor((expiresAt - Date.now()) / 1000);

    const dmEmbed = new EmbedBuilder()
      .setColor(0x2d7dd2)
      .setTitle('🔐 Your Verification Code')
      .setDescription(
        `Here is your one-time verification code:\n\n` +
        `## \`${code}\`\n\n` +
        `**Steps:**\n` +
        `1. Click the button below to open the verification page\n` +
        `2. Enter the code above\n` +
        `3. Get verified!\n\n` +
        `> ⏰ This code expires in **${expiresIn} seconds** (2 minutes)\n` +
        `> 🔒 Single-use only — do not share this code`
      )
      .setFooter({ text: 'Champions of the Shattered Realm' })
      .setTimestamp();

    const { ActionRowBuilder, ButtonBuilder, ButtonStyle } = require('discord.js');
    const row = new ActionRowBuilder().addComponents(
      new ButtonBuilder()
        .setLabel('Open Verification Page 🔗')
        .setStyle(ButtonStyle.Link)
        .setURL(verifyUrl)
    );

    try {
      await user.send({ embeds: [dmEmbed], components: [row] });
      await interaction.editReply({
        content: '📬 Check your DMs! I\'ve sent you your verification code.\n> Make sure your DMs are open if you didn\'t receive it.',
      });
    } catch {
      await interaction.editReply({
        content: '❌ I couldn\'t DM you! Please enable DMs from server members in your Privacy Settings, then try again.',
      });
    }
  },
};
