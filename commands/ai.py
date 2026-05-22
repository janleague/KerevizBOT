import asyncio
import random
from urllib.parse import quote, urlencode

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import BucketType


TEXT_BASE_URL = "https://text.pollinations.ai"
IMAGE_BASE_URL = "https://image.pollinations.ai/prompt"
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=45)
MAX_PROMPT_LENGTH = 1400
MAX_RESPONSE_LENGTH = 1900


class AI(commands.Cog):
    """Free AI commands powered by Pollinations."""

    ai = app_commands.Group(name="ai", description="Free AI tools.")

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _generate_text(self, prompt: str) -> str:
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("Prompt cannot be empty.")
        if len(prompt) > MAX_PROMPT_LENGTH:
            raise ValueError(f"Prompt must be {MAX_PROMPT_LENGTH} characters or fewer.")

        params = {}
        api_key = getattr(self.bot, "POLLINATIONS_API_KEY", None)
        if api_key:
            params["key"] = api_key

        url = f"{TEXT_BASE_URL}/{quote(prompt)}?{urlencode(params)}"
        try:
            async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
                async with session.get(url) as response:
                    if response.status == 429:
                        raise RuntimeError("The free AI service is rate-limited right now. Please try again later.")
                    if response.status >= 500:
                        raise RuntimeError("The free AI service is temporarily unavailable.")
                    if response.status != 200:
                        raise RuntimeError("The AI request failed. Please try again.")
                    text = (await response.text()).strip()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            raise RuntimeError("The free AI service did not respond in time. Please try again later.")

        if not text:
            raise RuntimeError("The AI service returned an empty response.")
        return text[:MAX_RESPONSE_LENGTH]

    def _image_url(self, prompt: str, width: int, height: int) -> str:
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("Prompt cannot be empty.")
        if len(prompt) > 600:
            raise ValueError("Image prompt must be 600 characters or fewer.")

        params = {
            "width": str(width),
            "height": str(height),
            "seed": str(random.randint(1, 2_000_000_000)),
            "nologo": "true",
            "private": "true",
        }
        api_key = getattr(self.bot, "POLLINATIONS_API_KEY", None)
        if api_key:
            params["key"] = api_key
        return f"{IMAGE_BASE_URL}/{quote(prompt)}?{urlencode(params)}"

    @ai.command(name="ask", description="Ask the free AI a question.")
    @app_commands.describe(
        prompt="What should the AI answer?",
        private="Only show the answer to you.",
    )
    @app_commands.checks.cooldown(1, 12.0)
    async def ask(self, interaction: discord.Interaction, prompt: str, private: bool = False):
        await interaction.response.defer(ephemeral=private, thinking=True)
        try:
            answer = await self._generate_text(prompt)
        except (ValueError, RuntimeError) as exc:
            return await interaction.followup.send(str(exc), ephemeral=True)
        await interaction.followup.send(answer)

    @ai.command(name="summarize", description="Summarize text with the free AI.")
    @app_commands.describe(
        text="Text to summarize.",
        private="Only show the summary to you.",
    )
    @app_commands.checks.cooldown(1, 12.0)
    async def summarize(self, interaction: discord.Interaction, text: str, private: bool = True):
        await interaction.response.defer(ephemeral=private, thinking=True)
        prompt = (
            "Summarize the following text in clear English. Keep it concise, accurate, "
            "and use bullet points only if they help.\n\n"
            f"{text}"
        )
        try:
            answer = await self._generate_text(prompt)
        except (ValueError, RuntimeError) as exc:
            return await interaction.followup.send(str(exc), ephemeral=True)
        await interaction.followup.send(answer)

    @ai.command(name="rewrite", description="Rewrite text in a chosen tone.")
    @app_commands.describe(
        text="Text to rewrite.",
        tone="Desired tone, for example: professional, friendly, short, funny.",
        private="Only show the rewrite to you.",
    )
    @app_commands.checks.cooldown(1, 12.0)
    async def rewrite(
        self,
        interaction: discord.Interaction,
        text: str,
        tone: str = "clear and friendly",
        private: bool = True,
    ):
        await interaction.response.defer(ephemeral=private, thinking=True)
        prompt = (
            f"Rewrite the following text in a {tone} tone. Keep the meaning the same "
            "and return only the rewritten text.\n\n"
            f"{text}"
        )
        try:
            answer = await self._generate_text(prompt)
        except (ValueError, RuntimeError) as exc:
            return await interaction.followup.send(str(exc), ephemeral=True)
        await interaction.followup.send(answer)

    @ai.command(name="translate", description="Translate text into another language.")
    @app_commands.describe(
        text="Text to translate.",
        language="Target language, for example: English, Turkish, German.",
        private="Only show the translation to you.",
    )
    @app_commands.checks.cooldown(1, 12.0)
    async def translate(
        self,
        interaction: discord.Interaction,
        text: str,
        language: str = "English",
        private: bool = True,
    ):
        await interaction.response.defer(ephemeral=private, thinking=True)
        prompt = f"Translate the following text to {language}. Return only the translation.\n\n{text}"
        try:
            answer = await self._generate_text(prompt)
        except (ValueError, RuntimeError) as exc:
            return await interaction.followup.send(str(exc), ephemeral=True)
        await interaction.followup.send(answer)

    @ai.command(name="image", description="Generate a free AI image.")
    @app_commands.describe(
        prompt="Describe the image.",
        width="Image width. Defaults to 768.",
        height="Image height. Defaults to 768.",
        private="Only show the image to you.",
    )
    @app_commands.checks.cooldown(1, 30.0)
    async def image(
        self,
        interaction: discord.Interaction,
        prompt: str,
        width: app_commands.Range[int, 256, 1024] = 768,
        height: app_commands.Range[int, 256, 1024] = 768,
        private: bool = False,
    ):
        await interaction.response.defer(ephemeral=private, thinking=True)
        try:
            image_url = self._image_url(prompt, int(width), int(height))
        except ValueError as exc:
            return await interaction.followup.send(str(exc), ephemeral=True)

        embed = discord.Embed(
            title="AI Image",
            description=prompt[:1000],
            color=discord.Color.green(),
        )
        embed.set_image(url=image_url)
        embed.set_footer(text="Generated by a free AI service. Availability may vary.")
        await interaction.followup.send(embed=embed)

    @commands.command(name="ai", help="Show the AI command guide.")
    @commands.guild_only()
    @commands.cooldown(1, 8, BucketType.user)
    async def ai_help(self, ctx: commands.Context):
        embed = discord.Embed(
            title="AI Commands",
            description="Use slash commands for free AI tools.",
            color=discord.Color.green(),
        )
        embed.add_field(name="/ai ask", value="Ask a question.", inline=False)
        embed.add_field(name="/ai summarize", value="Summarize text.", inline=False)
        embed.add_field(name="/ai rewrite", value="Rewrite text in a chosen tone.", inline=False)
        embed.add_field(name="/ai translate", value="Translate text.", inline=False)
        embed.add_field(name="/ai image", value="Generate an image.", inline=False)
        embed.set_footer(text="Free AI services can be rate-limited or temporarily unavailable.")
        await ctx.send(embed=embed)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CommandOnCooldown):
            message = f"Please wait {error.retry_after:.1f}s before using this AI command again."
        else:
            message = "An unexpected AI command error occurred. Please try again."
            print(f"[AI ERROR] {error!r}")

        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(AI(bot))
    guide = bot.get_command("ai")
    if guide:
        guide.category = "AI"
