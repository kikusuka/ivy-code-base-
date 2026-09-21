const { SlashCommandBuilder, EmbedBuilder, PermissionFlagsBits } = require('discord.js');
const { db } = require('../utils/db');

module.exports = {
  data: new SlashCommandBuilder()
    .setName('event')
    .setDescription('Guild event management')
    .setDefaultMemberPermissions(PermissionFlagsBits.ManageEvents)

    .addSubcommand(sub => sub.setName('create')
      .setDescription('Create an event and send DM reminders')
      .addStringOption(o => o.setName('name').setDescription('Event name').setRequired(true))
      .addStringOption(o => o.setName('date').setDescription('Event date & time (e.g. "July 20 at 8pm UTC")').setRequired(true))
      .addStringOption(o => o.setName('content').setDescription('Optional event description'))
      .addStringOption(o => o.setName('notify')
        .setDescription('Who to notify?').setRequired(true)
        .addChoices(
          { name: '@everyone', value: 'everyone' },
          { name: 'Specific Role', value: 'role' },
        )
      )
      .addRoleOption(o => o.setName('role').setDescription('Role to notify (if "Specific Role" chosen)'))
      .addBooleanOption(o => o.setName('ping').setDescription('Also ping in announcements channel? (default: true)'))
    )
    .addSubcommand(sub => sub.setName('list')
      .setDescription('View all upcoming events')
    ),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();

    if (sub === 'list') {
      const events = db.getEvents();
      if (!events.length) {
        return interaction.reply({ content: '📅 No events scheduled yet.', ephemeral: true });
      }
      const embed = new EmbedBuilder()
        .setColor(0x2d7dd2)
        .setTitle('📅 Upcoming Guild Events')
        .setTimestamp()
        .setFooter({ text: 'Champions of the Shattered Realm' });

      events.slice(-10).forEach(e => {
        embed.addFields({
          name: `🎉 ${e.name}`,
          value: `📅 **${e.date}**${e.content ? `\n${e.content}` : ''}\n🔔 Notified: ${e.notify}`,
        });
      });
      return interaction.reply({ embeds: [embed] });
    }

    if (sub === 'create') {
      await interaction.deferReply({ ephemeral: true });

      const name    = interaction.options.getString('name');
      const date    = interaction.options.getString('date');
      const content = interaction.options.getString('content') || '';
      const notify  = interaction.options.getString('notify');
      const role    = interaction.options.getRole('role');
      const ping    = interaction.options.getBoolean('ping') ?? true;

      if (notify === 'role' && !role) {
        return interaction.editReply('❌ You chose "Specific Role" but didn\'t select a role. Run the command again.');
      }

      // Save event
      const eventData = { name, date, content, notify: notify === 'role' ? role.name : '@everyone', createdBy: interaction.user.tag };
      db.addEvent(eventData);

      // Build DM embed
      const dmEmbed = new EmbedBuilder()
        .setColor(0x2d7dd2)
        .setTitle(`📅 Upcoming Event: ${name}`)
        .setDescription(
          `Hey! There's a new event in **${process.env.GUILD_NAME || 'the guild'}** — don't miss it!\n\n` +
          `**📆 When:** ${date}\n` +
          (content ? `**📝 Details:** ${content}\n` : '') +
          `\nSee you there! 🎉`
        )
        .setFooter({ text: 'Champions of the Shattered Realm · Event Reminder' })
        .setTimestamp();

      // Determine who to DM
      const guild = interaction.guild;
      await guild.members.fetch();

      let targets = guild.members.cache.filter(m => !m.user.bot);
      if (notify === 'role' && role) {
        targets = targets.filter(m => m.roles.cache.has(role.id));
      }

      let sent = 0, failed = 0;
      await interaction.editReply(`📤 Sending DMs to **${targets.size}** member(s)... this may take a moment.`);

      for (const member of targets.values()) {
        try {
          await member.send({ embeds: [dmEmbed] });
          sent++;
          // Rate limit friendly — small delay
          await new Promise(r => setTimeout(r, 300));
        } catch { failed++; }
      }

      // Optionally ping in announcements channel
      if (ping && process.env.WELCOME_CHANNEL_ID) {
        const ch = guild.channels.cache.get(process.env.WELCOME_CHANNEL_ID);
        if (ch) {
          const pingText = notify === 'everyone' ? '@everyone' : `<@&${role.id}>`;
          await ch.send({ content: `${pingText} 📅 **New Event: ${name}** — ${date}${content ? ` · ${content}` : ''}`, embeds: [dmEmbed] }).catch(() => {});
        }
      }

      return interaction.editReply(`✅ Event created! DMs sent: **${sent}** · Failed (DMs closed): **${failed}**`);
    }
  },
};
