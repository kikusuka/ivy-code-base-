import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import json
from utils.db import (
    get_account, create_account, update_account, get_account_by_csr,
    verify_password_by_csr, check_login_rate_limit, record_login_attempt,
    get_server_config, get_saved_roles, upsert_member_data,
    get_server_type, SERVER_TYPES
)
from utils.logger import log
from utils.id_card import generate_id_card


class PasswordModal(discord.ui.Modal, title='Set Your Password'):
    password = discord.ui.TextInput(
        label='Password',
        placeholder='Choose a strong password...',
        min_length=6, max_length=64,
        style=discord.TextStyle.short
    )
    confirm = discord.ui.TextInput(
        label='Confirm Password',
        placeholder='Repeat your password...',
        min_length=6, max_length=64,
        style=discord.TextStyle.short
    )

    async def on_submit(self, interaction: discord.Interaction):
        if self.password.value != self.confirm.value:
            return await interaction.response.send_message('❌ Passwords don\'t match. Try again.', ephemeral=True)
        self.interaction = interaction
        self.stop()


class LoginModal(discord.ui.Modal, title='CSR System Login'):
    csr_id = discord.ui.TextInput(
        label='Your CSR ID',
        placeholder='e.g. CSR-00042',
        min_length=9, max_length=12,
        style=discord.TextStyle.short
    )
    password = discord.ui.TextInput(
        label='Password',
        placeholder='Your password...',
        min_length=6, max_length=64,
        style=discord.TextStyle.short
    )

    async def on_submit(self, interaction: discord.Interaction):
        self.interaction = interaction
        self.stop()


class AccountCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _csr_embed(self, account: dict, guild_id: str) -> discord.Embed:
        server_type = get_server_type(guild_id)
        info = SERVER_TYPES.get(server_type, {})
        platform = info.get('platform', 'roblox')

        linked_str = ''
        if account.get('roblox_linked'):
            linked_str += f'🎮 Roblox: `{account["roblox_username"]}`\n'
        if account.get('steam_linked'):
            linked_str += f'🎮 Steam: `{account["steam_username"]}`\n'
        if not linked_str:
            linked_str = '*No accounts linked yet*'

        embed = discord.Embed(
            title=f'🪪 CSR Identity Card',
            color=0x00b4d8
        )
        embed.add_field(name='CSR ID',      value=f'`{account["csr_id"]}`',      inline=True)
        embed.add_field(name='Discord',     value=f'`{account["tag"]}`',          inline=True)
        embed.add_field(name='Reputation',  value=f'⭐ `{account["reputation"]}`', inline=True)
        embed.add_field(name='Verified',    value='✅ Yes' if account['verified'] else '❌ No', inline=True)
        embed.add_field(name='Member Since',value=f'`{account["created_at"][:10]}`', inline=True)
        embed.add_field(name='Linked Accounts', value=linked_str, inline=False)
        embed.set_footer(text='CSR System · Powered by Breezy')
        embed.timestamp = discord.utils.utcnow()
        return embed

    @app_commands.command(name='signup', description='Create your CSR System account')
    async def signup(self, interaction: discord.Interaction):
        existing = get_account(str(interaction.user.id))
        if existing:
            return await interaction.response.send_message(
                f'✅ You already have a CSR account! Your ID is `{existing["csr_id"]}`.\nUse `/login` to restore your roles.',
                ephemeral=True
            )

        # Check verified
        guild_id = str(interaction.guild.id)
        cfg = get_server_config(guild_id)
        verified_role_id = cfg.get('verified_role_id') if cfg else None
        if verified_role_id:
            role = interaction.guild.get_role(int(verified_role_id))
            if role and role not in interaction.user.roles:
                return await interaction.response.send_message(
                    '❌ You need to be **verified** first before creating a CSR account.\n> Use `/getverified` to start.',
                    ephemeral=True
                )

        # Show password modal
        modal = PasswordModal()
        await interaction.response.send_modal(modal)
        await modal.wait()

        if not hasattr(modal, 'interaction'):
            return

        account = create_account(str(interaction.user.id), str(interaction.user), modal.password.value)

        # DM credentials
        dm_embed = discord.Embed(
            title='🪪 Your CSR Account Details',
            description=(
                f'**Keep these safe — do not share them!**\n\n'
                f'🆔 **CSR ID:** `{account["csr_id"]}`\n'
                f'🔐 **Password:** `{modal.password.value}`\n\n'
                f'Use `/login` in any CSR server to restore your roles.\n\n'
                f'> Your account works across all CSR System servers — SBO:R, Warframe, Blox Fruits, Blue Heater 2 and more!'
            ),
            color=0x00b4d8
        )
        dm_embed.set_footer(text='CSR System · Powered by Breezy · Save this message!')
        dm_embed.timestamp = discord.utils.utcnow()

        try:
            await interaction.user.send(embed=dm_embed)
        except discord.Forbidden:
            pass

        # Save current roles
        role_ids = [str(r.id) for r in interaction.user.roles if r.id != interaction.guild.id]
        upsert_member_data(guild_id, str(interaction.user.id), saved_roles=json.dumps(role_ids))

        await log(self.bot, guild_id, 'account',
                  f'🪪 **{interaction.user}** created CSR account `{account["csr_id"]}`')

        embed = self._csr_embed(account, guild_id)
        await modal.interaction.response.send_message(
        # Send ID card
        try:
            server_type = get_server_type(guild_id) or "sbor"
            card_bytes = await generate_id_card(
                username=str(interaction.user),
                csr_id=account["csr_id"],
                guild_name=interaction.guild.name,
                avatar_url=str(interaction.user.display_avatar.url),
                server_type=server_type
            )
            card_file = discord.File(card_bytes, filename="csr_id_card.png")
            await interaction.user.send(
                "🪪 **Your CSR Membership Card** — keep this safe!",
                file=card_file
            )
        except Exception as e:
            print(f"ID card error: {e}")

            f'🎉 Welcome to CSR System, **{interaction.user.name}**! Your ID is `{account["csr_id"]}`.\nCheck your DMs for your credentials!',
            embed=embed,
            ephemeral=True
        )

    @app_commands.command(name='login', description='Login with your CSR ID to restore your roles')
    async def login(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)
        cfg = get_server_config(guild_id)
        if not cfg or not cfg.get('setup_complete'):
            return await interaction.response.send_message('❌ This server hasn\'t been set up yet.', ephemeral=True)

        # Rate limit check
        allowed, cooldown = check_login_rate_limit(str(interaction.user.id))
        if not allowed:
            return await interaction.response.send_message(
                f'🔒 Too many failed attempts. Try again in **{cooldown // 60}m {cooldown % 60}s**.',
                ephemeral=True
            )

        modal = LoginModal()
        await interaction.response.send_modal(modal)
        await modal.wait()

        if not hasattr(modal, 'interaction'):
            return

        csr_id = modal.csr_id.value.strip().upper()
        password = modal.password.value

        account = get_account_by_csr(csr_id)
        if not account or not verify_password_by_csr(csr_id, password):
            record_login_attempt(str(interaction.user.id), False)
            return await modal.interaction.response.send_message(
                '❌ Invalid CSR ID or password. Check your credentials and try again.',
                ephemeral=True
            )

        # Check if this Discord account matches
        if account['discord_id'] != str(interaction.user.id):
            record_login_attempt(str(interaction.user.id), False)
            return await modal.interaction.response.send_message(
                '❌ This CSR account belongs to a different Discord account.',
                ephemeral=True
            )

        record_login_attempt(str(interaction.user.id), True)

        # Restore roles
        saved_role_ids = get_saved_roles(guild_id, str(interaction.user.id))
        restored = []
        for role_id in saved_role_ids:
            role = interaction.guild.get_role(int(role_id))
            if role and role not in interaction.user.roles:
                try:
                    await interaction.user.add_roles(role, reason='CSR login role restore')
                    restored.append(role.name)
                except Exception:
                    pass

        # Restore verified role
        verified_id = cfg.get('verified_role_id')
        if verified_id and account.get('verified'):
            role = interaction.guild.get_role(int(verified_id))
            if role and role not in interaction.user.roles:
                try:
                    await interaction.user.add_roles(role, reason='CSR login — verified restore')
                    restored.append(role.name)
                except Exception:
                    pass

        # Update nickname with linked account
        server_type = cfg.get('server_type')
        await self._update_nickname(interaction.user, interaction.guild, account, server_type)

        await log(self.bot, guild_id, 'account',
                  f'🔑 **{interaction.user}** logged in as `{csr_id}` — restored {len(restored)} role(s)')

        embed = self._csr_embed(account, guild_id)
        embed.add_field(
            name='✅ Roles Restored',
            value=', '.join(f'`{r}`' for r in restored) if restored else '*No roles to restore*',
            inline=False
        )
        await modal.interaction.response.send_message(
            f'✅ Welcome back! Logged in as `{csr_id}`.',
            embed=embed,
            ephemeral=True
        )

    async def _update_nickname(self, member: discord.Member, guild: discord.Guild, account: dict, server_type: str):
        """Set nickname to DisplayName (@roblox_user) or (@steam_user)."""
        try:
            platform = SERVER_TYPES.get(server_type, {}).get('platform', 'roblox')
            suffix = None
            if platform == 'roblox' and account.get('roblox_username'):
                suffix = account['roblox_username']
            elif platform == 'steam' and account.get('steam_username'):
                suffix = account['steam_username']

            if suffix:
                base = member.display_name
                # Remove existing suffix if any
                if '(@' in base:
                    base = base[:base.index('(@')].strip()
                new_nick = f'{base} (@{suffix})'[:32]  # Discord 32 char limit
                await member.edit(nick=new_nick, reason='CSR nickname sync')
        except Exception:
            pass

    @app_commands.command(name='myid', description='View your CSR identity card')
    async def myid(self, interaction: discord.Interaction):
        account = get_account(str(interaction.user.id))
        if not account:
            return await interaction.response.send_message(
                '❌ You don\'t have a CSR account yet. Use `/signup` to create one!',
                ephemeral=True
            )
        embed = self._csr_embed(account, str(interaction.guild.id))
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(AccountCog(bot))
