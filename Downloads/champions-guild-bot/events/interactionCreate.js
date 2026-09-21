const { createOTP } = require('../utils/otp');
const { db } = require('../utils/db');
const { EmbedBuilder, ActionRowBuilder, ButtonBuilder, ButtonStyle } = require('discord.js');

module.exports = {
  name: 'interactionCreate',
  async execute(interaction, client) {

    // ── Slash commands ─────────────────────────────────────────────────────
    if (interaction.isChatInputCommand()) {
      const cmd = client.commands.get(interaction.commandName);
      if (!cmd) return;
      try {
        await cmd.execute(interaction);
      } catch (err) {
        console.error(`Command error [${interaction.commandName}]:`, err);
        const msg = { content: '❌ An error occurred. Try again or contact an admin.', ephemeral: true };
        if (interaction.replied || interaction.deferred) interaction.followUp(msg).catch(() => {});
        else interaction.reply(msg).catch(() => {});
      }
      return;
    }

    // ── Button: Verify Me ──────────────────────────────────────────────────
    if (interaction.isButton() && interaction.customId === 'start_verify') {
      const user = interaction.user;

      // Already verified?
      const dbUser = db.getUser(user.id);
      if (dbUser?.verified) {
        return interaction.reply({ content: '✅ You\'re already verified! Welcome.', ephemeral: true });
      }

      await interaction.deferReply({ ephemeral: true });

      const { code, expiresAt } = createOTP(user.id);
      const verifyUrl = `${process.env.WEB_BASE_URL}/verify`;

      const dmEmbed = new EmbedBuilder()
        .setColor(0x2d7dd2)
        .setTitle('🔐 Your Verification Code')
        .setDescription(
          `Your one-time code:\n\n## \`${code}\`\n\n` +
          `1. Click **Open Verification Page** below\n` +
          `2. Enter the code\n` +
          `3. You\'re in! ✅\n\n` +
          `> ⏰ Expires in **2 minutes** · Single-use only`
        )
        .setFooter({ text: 'Champions of the Shattered Realm · Verification' })
        .setTimestamp();

      const row = new ActionRowBuilder().addComponents(
        new ButtonBuilder()
          .setLabel('Open Verification Page 🔗')
          .setStyle(ButtonStyle.Link)
          .setURL(verifyUrl)
      );

      try {
        await user.send({ embeds: [dmEmbed], components: [row] });
        await interaction.editReply({
          content: '📬 Check your DMs! Your verification code has been sent.\n> Code expires in **2 minutes**.',
        });
      } catch {
        await interaction.editReply({
          content: '❌ I couldn\'t DM you! Please enable **DMs from server members** in your Privacy Settings, then click the button again.',
        });
      }
    }
  },
};
