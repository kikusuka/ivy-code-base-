import discord
from discord.ext import commands
from discord import app_commands
from utils.db import get_server_type, SERVER_TYPES


SECTIONS = {
    'setup': {
        'title': '⚙️ Server Setup',
        'content': """**Initial Setup Commands (prefix):**
`.csrsbor` → Setup as Sword Blox Online Rebirth server
`.csrbh2` → Setup as Blue Heater 2 server
`.csrbf` → Setup as Blox Fruits server
`.csrwarframe` → Setup as Warframe server

**After setup:**
`.verifysetup` → Post verification panel in current channel
`.portalsetup` → Post game portal embed in current channel

**The setup wizard will ask for:**
› Verified role ID
› Unverified role ID
› In Game role ID
› Log channel ID
› Backup channel ID
› Welcome channel ID
› Updates channel ID
› Portal channel ID

**Tips:**
› All channels and roles must be created BEFORE running setup
› You can re-run setup anytime to update config
› Use `/sync` after setup to register all slash commands"""
    },
    'verify': {
        'title': '🔐 Verification System',
        'content': """**How it works:**
1. Member joins → gets Unverified role automatically
2. Member clicks Verify Me in verification panel
3. Bot DMs them a 6-digit OTP (valid 2 minutes, single-use)
4. Member enters OTP on the verification page
5. Bot assigns Verified role, removes Unverified role
6. Member is prompted to create/login to CSR account

**Commands:**
`/getverified` → Start verification (for members)
`.verifysetup` → Post verification panel (admin prefix)

**OTP Security:**
› Stored in SQLite — survives bot restarts
› 2 minute expiry, single-use only
› Rate limited — 1 OTP per user at a time
› Failed verifications retry automatically

**Troubleshooting:**
› Member says no DM received → they have DMs disabled
› OTP expired → member clicks Verify Me again for new code
› Bot can't assign roles → check bot role hierarchy"""
    },
    'accounts': {
        'title': '🪪 CSR Account System',
        'content': """**What it is:**
Universal accounts that work across ALL CSR servers.
Members sign up once, login anywhere to restore everything.

**Member Commands:**
`/signup` → Create CSR account (requires verification first)
`/login` → Login and restore roles
`/myid` → View your CSR identity card

**CSR ID format:** `CSR-00042` (permanent, never changes)

**What gets restored on login:**
› Verified role
› All saved roles from previous sessions
› Roblox/Steam linked account (no re-linking needed)
› Nickname format restored

**Security features:**
› Passwords hashed with bcrypt (we cannot see them)
› 3 failed logins → 10 minute lockout
› Credentials DMed on signup
› Soft deletes only — data never truly deleted

**Admin notes:**
› You cannot view member passwords (by design)
› CSR accounts are global — not per-server
› Members keep their CSR ID even after leaving"""
    },
    'moderation': {
        'title': '🛡️ Moderation Commands',
        'content': """**All mod commands require Moderate Members permission**

`/mod ban @user [reason] [days]` → Ban + optional message delete
`/mod kick @user [reason]` → Kick member
`/mod mute @user [minutes] [reason]` → Timeout (max 40320 min = 28 days)
`/mod unmute @user` → Remove timeout
`/mod warn @user [reason]` → DM warning to member
`/mod purge [amount]` → Bulk delete 1-100 messages

**All actions:**
› DM the affected member with reason
› Log to your log channel automatically
› Soft-recorded in CSR System database

**Cross-server bans:**
› Coming soon — ban in one CSR server, flagged in all"""
    },
    'presence': {
        'title': '🎮 Game Presence Tracking',
        'content': """**Roblox servers (SBO:R, BH2, Blox Fruits):**
Members link with `/linkroblox [roblox_user_id]`
Bot checks every 5 minutes who's in the specific game
Auto-assigns/removes In Game 🎮 role

**Warframe server:**
Members link with `/linksteam [steam_id]`
Bot checks Steam presence API every 5 minutes
Discord Rich Presence also tracked as fallback

**Commands:**
`/linkroblox [id]` → Link Roblox account
`/unlinkroblox` → Unlink Roblox account
`/linksteam [id]` → Link Steam account (Warframe)
`/unlinksteam` → Unlink Steam account
`/whosplaying` → See who's in-game right now
`/robloxprofile [@user]` → View linked Roblox profile

**Nickname format:**
Once linked, nickname becomes: `DisplayName (@username)`
This is automatic on login/linking

**Rate limiting:**
Roblox API checked in batches of 50 users
Exponential backoff on rate limit errors
IP ban protection built in"""
    },
    'wiki': {
        'title': '🔍 Wiki Chatbot',
        'content': """**How members use it:**
@mention the bot with any gaming question:
> `@CSR System what is iron ore`
> `@CSR System best sword build for beginners`
> `@CSR System how to unlock prime warframes`

Or use the slash command:
`/ask [question]` → Ask a gaming question

**What it searches:**
SBO:R → swordbloxonlinerebirth.fandom.com
Blue Heater 2 → blue-heater-2.fandom.com
Blox Fruits → blox-fruits.fandom.com
Warframe → wiki.warframe.com + warframe.fandom.com

Plus YouTube guides as fallback

**Guardrails:**
› Only answers questions about the server's game
› Confidence score shown (🟦🟦🟦 = high, 🟦⬜⬜ = low)
› Always shows source link so members can read more
› 5 second cooldown per user
› Results cached 24 hours to save requests

**Admin command:**
`/forcescrape` → Manually trigger a group update scrape"""
    },
    'events': {
        'title': '📅 Event System',
        'content': """**Create an event:**
`/event create` → Opens event creation
› Name (required)
› Date/time (required, e.g. "July 20 at 8pm UTC")
› Content/description (optional)
› Who to notify: @everyone or specific role
› Ping in announcements channel? (yes/no)

**View events:**
`/event list` → Shows last 10 events

**What happens on create:**
› Event saved to database
› DMs sent to all targeted members
› Optional ping in updates channel
› Rate-limited DM sending (0.3s between each)

**Requires:** Manage Events permission"""
    },
    'crossserver': {
        'title': '🌐 Cross-Server Features',
        'content': """**Inter-server shoutouts:**
When a member joins a new CSR server, a shoutout is posted
in their other CSR servers:
> "👋 Cookie just joined the Warframe server!"
1 hour cooldown per member to prevent spam

**Reputation system:**
Members earn reputation points across all CSR servers
`/reputation [@user]` → View reputation
`/giverep @user` → Give reputation (1 per day per user)
`/toprep` → Server leaderboard

**Shared CSR accounts:**
Members login once, access everywhere
Roles restore automatically on login
Linked accounts (Roblox/Steam) carry over"""
    },
    'logs': {
        'title': '📋 Logging System',
        'content': """**What gets logged (all to your log channel):**
✅ verify → Member verified
🪪 account → Signup, login, password change
🎮 roblox → Roblox link/unlink
🎮 steam → Steam link/unlink
🟢 presence → In Game role assigned/removed
📰 scraper → Group updates posted
🛡️ mod → All moderation actions
👋 join → Member joined server
📤 leave → Member left server
⭐ reputation → Rep given/received
🔍 wiki → Questions asked (admin only)
⚙️ setup → Server config changes

**Backup channel:**
Database backup status posted/updated automatically
One message per category, edited on each update

**Log colors:**
Blue tones = informational
Green = positive actions
Red = removals/bans/leaves"""
    },
    'all': None  # Special case
}


class AdminHelpCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _build_embed(self, section_key: str, guild_id: str) -> discord.Embed:
        section = SECTIONS[section_key]
        server_type = get_server_type(guild_id)
        info = SERVER_TYPES.get(server_type, {}) if server_type else {}

        embed = discord.Embed(
            title=section['title'],
            description=section['content'],
            color=0x00b4d8
        )
        if server_type:
            embed.set_author(name=f'CSR System · {info.get("name", "Unknown")} Server')
        embed.set_footer(text='CSR System Admin Guide · Powered by Breezy · Admin eyes only 👀')
        embed.timestamp = discord.utils.utcnow()
        return embed

    adminhelp_group = app_commands.Group(
        name='adminhelp',
        description='[Admin] CSR System admin guide',
        default_permissions=discord.Permissions(administrator=True)
    )

    @adminhelp_group.command(name='setup', description='Setup guide')
    async def ah_setup(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=self._build_embed('setup', str(interaction.guild.id)), ephemeral=True)

    @adminhelp_group.command(name='verify', description='Verification system guide')
    async def ah_verify(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=self._build_embed('verify', str(interaction.guild.id)), ephemeral=True)

    @adminhelp_group.command(name='accounts', description='CSR account system guide')
    async def ah_accounts(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=self._build_embed('accounts', str(interaction.guild.id)), ephemeral=True)

    @adminhelp_group.command(name='moderation', description='Moderation commands guide')
    async def ah_mod(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=self._build_embed('moderation', str(interaction.guild.id)), ephemeral=True)

    @adminhelp_group.command(name='presence', description='Game presence tracking guide')
    async def ah_presence(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=self._build_embed('presence', str(interaction.guild.id)), ephemeral=True)

    @adminhelp_group.command(name='wiki', description='Wiki chatbot guide')
    async def ah_wiki(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=self._build_embed('wiki', str(interaction.guild.id)), ephemeral=True)

    @adminhelp_group.command(name='events', description='Event system guide')
    async def ah_events(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=self._build_embed('events', str(interaction.guild.id)), ephemeral=True)

    @adminhelp_group.command(name='crossserver', description='Cross-server features guide')
    async def ah_cross(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=self._build_embed('crossserver', str(interaction.guild.id)), ephemeral=True)

    @adminhelp_group.command(name='logs', description='Logging system guide')
    async def ah_logs(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=self._build_embed('logs', str(interaction.guild.id)), ephemeral=True)

    @adminhelp_group.command(name='all', description='View the full admin guide')
    async def ah_all(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        for key in [k for k in SECTIONS if k != 'all']:
            embed = self._build_embed(key, str(interaction.guild.id))
            await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot):
    cog = AdminHelpCog(bot)
    bot.tree.add_command(cog.adminhelp_group)
    await bot.add_cog(cog)
