const { SlashCommandBuilder, EmbedBuilder, PermissionFlagsBits } = require('discord.js');

const USER_COMMANDS = [
  { cmd: '/getverified',    desc: 'Start verification — get your OTP via DM' },
  { cmd: '/info view',      desc: 'View guild information' },
  { cmd: '/serverinfo',     desc: 'Server stats — members, bots, channels, etc.' },
  { cmd: '/scout @user',    desc: 'Scout a member\'s profile, roles & history' },
  { cmd: '/event list',     desc: 'See upcoming guild events' },
  { cmd: '/help',           desc: 'Shows this help message' },
];

const ADMIN_COMMANDS = [
  { cmd: '.verifysetup',           desc: 'Post the verification panel in current channel' },
  { cmd: '/info addabout',         desc: 'Add or update a guild info section' },
  { cmd: '/info remove',           desc: 'Remove a guild info section' },
  { cmd: '/mod ban @user',         desc: 'Ban a member' },
  { cmd: '/mod kick @user',        desc: 'Kick a member' },
  { cmd: '/mod mute @user',        desc: 'Timeout (mute) a member' },
  { cmd: '/mod unmute @user',      desc: 'Remove timeout' },
  { cmd: '/mod warn @user',        desc: 'Warn a member via DM' },
  { cmd: '/mod purge [amount]',    desc: 'Bulk delete messages' },
  { cmd: '/event create',          desc: 'Create an event & send DM reminders' },
  { cmd: '/sync',                  desc: 'Re-register slash commands' },
  { cmd: '/adminhelp',             desc: 'Shows this admin reference' },
];

module.exports = {
  data: new SlashCommandBuilder()
    .setName('help')
    .setDescription('View all available bot commands')
    .addSubcommand(sub => sub.setName('user').setDescription('Show user commands (default)'))
    .addSubcommand(sub => sub.setName('admin').setDescription('Show admin commands')),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand(false) ?? 'user';
    const isAdmin = interaction.member.permissions.has(PermissionFlagsBits.Administrator);

    if (sub === 'admin') {
      if (!isAdmin) return interaction.reply({ content: '❌ Admin commands are for admins only.', ephemeral: true });
      const embed = new EmbedBuilder()
        .setColor(0x2d7dd2)
        .setTitle('⚙️ Admin Command Reference')
        .setDescription(ADMIN_COMMANDS.map(c => `\`${c.cmd}\`\n└ ${c.desc}`).join('\n\n'))
        .setFooter({ text: 'Champions of the Shattered Realm · Admin Help' })
        .setTimestamp();
      return interaction.reply({ embeds: [embed], ephemeral: true });
    }

    const embed = new EmbedBuilder()
      .setColor(0x2d7dd2)
      .setTitle('📖 Bot Commands')
      .setDescription(USER_COMMANDS.map(c => `\`${c.cmd}\`\n└ ${c.desc}`).join('\n\n') +
        (isAdmin ? '\n\n*Use `/help admin` to see admin commands.*' : ''))
      .setFooter({ text: 'Champions of the Shattered Realm · Help' })
      .setTimestamp();

    return interaction.reply({ embeds: [embed], ephemeral: true });
  },
};
