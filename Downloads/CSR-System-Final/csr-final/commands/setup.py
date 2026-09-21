import discord
from discord.ext import commands
from utils.db import upsert_server_config, get_server_config, SERVER_TYPES
from utils.logger import log


class SetupView(discord.ui.View):
    def __init__(self, guild_id: str, server_type: str, bot):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.server_type = server_type
        self.bot = bot
        self.config = {}

    async def ask(self, interaction: discord.Interaction, question: str) -> str | None:
        """Ask admin for a channel/role ID interactively."""
        embed = discord.Embed(
            description=f'⚙️ {question}\n\n> Reply with the ID or mention. Type `skip` to skip.',
            color=0x00b4d8
        )
        await interaction.channel.send(embed=embed)

        def check(m):
            return m.author == interaction.user and m.channel == interaction.channel

        try:
            msg = await self.bot.wait_for('message', check=check, timeout=60)
            if msg.content.lower() == 'skip':
                return None
            # Extract ID from mention or raw ID
            content = msg.content.strip().replace('<#', '').replace('<@&', '').replace('>', '')
            return content if content.isdigit() else None
        except Exception:
            return None


class SetupCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def run_setup(self, ctx, server_type: str):
        info = SERVER_TYPES.get(server_type)
        if not info:
            return await ctx.reply('❌ Unknown server type.')

        if not ctx.author.guild_permissions.administrator:
            return await ctx.reply('❌ Admins only.')

        guild_id = str(ctx.guild.id)

        embed = discord.Embed(
            title=f'⚙️ CSR System Setup — {info["name"]}',
            description=(
                f'Setting up **{info["name"]}** configuration.\n\n'
                f'I\'ll ask you for channel and role IDs one by one.\n'
                f'Type `skip` for anything you want to set up later.\n\n'
                f'> This will take about 2 minutes ⏱️'
            ),
            color=0x00b4d8
        )
        embed.set_footer(text='CSR System · Powered by Breezy')
        await ctx.send(embed=embed)

        view = SetupView(guild_id, server_type, self.bot)

        questions = [
            ('verified_role_id',   '🟢 What\'s the **Verified role** ID?'),
            ('unverified_role_id', '🔴 What\'s the **Unverified role** ID?'),
            ('in_game_role_id',    '🎮 What\'s the **In Game role** ID?'),
            ('log_channel_id',     '📋 What\'s the **Log channel** ID?'),
            ('backup_channel_id',  '💾 What\'s the **Backup channel** ID?'),
            ('welcome_channel_id', '👋 What\'s the **Welcome channel** ID?'),
            ('updates_channel_id', '📰 What\'s the **Updates channel** ID?'),
            ('portal_channel_id',  '🌐 What\'s the **Portal channel** ID?'),
        ]

        config = {
            'server_type': server_type,
            'guild_name': ctx.guild.name,
            'setup_complete': 0,
        }

        for key, question in questions:
            # Create a fake interaction-like object for the view
            embed = discord.Embed(
                description=f'⚙️ **Setup [{questions.index((key, question)) + 1}/{len(questions)}]**\n\n{question}\n\n> Reply with the ID or mention, or type `skip`',
                color=0x00b4d8
            )
            embed.set_footer(text='CSR System Setup · Powered by Breezy')
            await ctx.send(embed=embed)

            def check(m):
                return m.author == ctx.author and m.channel == ctx.channel

            try:
                msg = await self.bot.wait_for('message', check=check, timeout=60)
                if msg.content.lower() != 'skip':
                    content = msg.content.strip().replace('<#', '').replace('<@&', '').replace('>', '')
                    if content.isdigit():
                        config[key] = content
            except Exception:
                await ctx.send('⏰ Timed out. Run setup again.')
                return

        # Save config
        upsert_server_config(guild_id, **config, setup_complete=1)

        # Post verification panel if channel set
        await log(self.bot, guild_id, 'setup', f'✅ Server configured as **{info["name"]}** by {ctx.author}')

        embed = discord.Embed(
            title='✅ CSR System Ready!',
            description=(
                f'**{info["name"]}** server is configured! Here\'s what to do next:\n\n'
                f'`/adminhelp setup` → Full admin guide\n'
                f'`.verifysetup` → Post verification panel\n'
                f'`.portalsetup` → Post game portal\n\n'
                f'> Members can now sign up with `/signup` or login with `/login`'
            ),
            color=0x2ecc71
        )
        embed.set_footer(text='CSR System · Powered by Breezy')
        await ctx.send(embed=embed)

    @commands.command(name='csrsbor')
    async def setup_sbor(self, ctx):
        await self.run_setup(ctx, 'sbor')

    @commands.command(name='csrbh2')
    async def setup_bh2(self, ctx):
        await self.run_setup(ctx, 'bh2')

    @commands.command(name='csrbf')
    async def setup_bf(self, ctx):
        await self.run_setup(ctx, 'bf')

    @commands.command(name='csrwarframe')
    async def setup_warframe(self, ctx):
        await self.run_setup(ctx, 'warframe')


async def setup(bot):
    await bot.add_cog(SetupCog(bot))
