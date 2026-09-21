import discord
from discord.ext import commands
from discord import app_commands
import asyncio
from utils.db import get_server_type, get_server_config, SERVER_TYPES
from utils.wiki_engine import answer_question


class WikiChatCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._cooldowns: dict[str, float] = {}

    def _is_on_cooldown(self, user_id: str) -> bool:
        import time
        last = self._cooldowns.get(user_id, 0)
        return time.time() - last < 5  # 5 second cooldown per user

    def _set_cooldown(self, user_id: str):
        import time
        self._cooldowns[user_id] = time.time()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if not message.guild:
            return

        # Only respond when @mentioned
        if self.bot.user not in message.mentions:
            return

        guild_id = str(message.guild.id)
        cfg = get_server_config(guild_id)
        if not cfg or not cfg.get('setup_complete'):
            return

        server_type = cfg.get('server_type')
        if not server_type:
            return

        # Rate limit
        if self._is_on_cooldown(str(message.author.id)):
            return
        self._set_cooldown(str(message.author.id))

        # Extract question (remove mention)
        question = message.content
        for mention in message.mentions:
            question = question.replace(f'<@{mention.id}>', '').replace(f'<@!{mention.id}>', '')
        question = question.strip()

        if not question:
            game_name = SERVER_TYPES.get(server_type, {}).get('name', 'the game')
            await message.reply(
                f'👋 Hey! Ask me anything about **{game_name}**!\n'
                f'> e.g. `@CSR System what is iron ore` or `@CSR System best sword build`',
                mention_author=False
            )
            return

        async with message.channel.typing():
            result = await answer_question(server_type, question)

        game_name = SERVER_TYPES.get(server_type, {}).get('name', 'the game')
        confidence_bar = '🟦🟦🟦' if result['confidence'] == 1 else ('🟦🟦⬜' if result['confidence'] == 0.5 else '🟦⬜⬜')

        embed = discord.Embed(
            description=result['answer'],
            color=0x00b4d8
        )
        embed.set_author(
            name=f'CSR System · {game_name} Knowledge Base',
            icon_url=self.bot.user.display_avatar.url
        )

        view = discord.ui.View()
        if result.get('source_url'):
            view.add_item(discord.ui.Button(
                label='📖 Read More',
                url=result['source_url'] if result['source_url'].startswith('http') else f'https://{result["source_url"]}',
                style=discord.ButtonStyle.link
            ))
        if result.get('yt_url'):
            view.add_item(discord.ui.Button(
                label='🎥 Watch Guide',
                url=result['yt_url'],
                style=discord.ButtonStyle.link
            ))

        footer = f'Confidence: {confidence_bar} · Powered by Breezy'
        embed.set_footer(text=footer)
        embed.timestamp = discord.utils.utcnow()

        await message.reply(embed=embed, view=view, mention_author=False)

    @app_commands.command(name='ask', description='Ask CSR System about the game')
    @app_commands.describe(question='Your gaming question')
    async def ask(self, interaction: discord.Interaction, question: str):
        guild_id = str(interaction.guild.id)
        cfg = get_server_config(guild_id)
        if not cfg or not cfg.get('setup_complete'):
            return await interaction.response.send_message('❌ Server not set up.', ephemeral=True)

        server_type = cfg.get('server_type')
        await interaction.response.defer()

        result = await answer_question(server_type, question)
        game_name = SERVER_TYPES.get(server_type, {}).get('name', 'the game')
        confidence_bar = '🟦🟦🟦' if result['confidence'] == 1 else ('🟦🟦⬜' if result['confidence'] == 0.5 else '🟦⬜⬜')

        embed = discord.Embed(description=result['answer'], color=0x00b4d8)
        embed.set_author(name=f'CSR System · {game_name} Knowledge Base', icon_url=self.bot.user.display_avatar.url)
        embed.set_footer(text=f'Confidence: {confidence_bar} · Powered by Breezy')
        embed.timestamp = discord.utils.utcnow()

        view = discord.ui.View()
        if result.get('source_url'):
            url = result['source_url'] if result['source_url'].startswith('http') else f'https://{result["source_url"]}'
            view.add_item(discord.ui.Button(label='📖 Read More', url=url, style=discord.ButtonStyle.link))
        if result.get('yt_url'):
            view.add_item(discord.ui.Button(label='🎥 Watch Guide', url=result['yt_url'], style=discord.ButtonStyle.link))

        await interaction.followup.send(embed=embed, view=view)


async def setup(bot):
    await bot.add_cog(WikiChatCog(bot))
