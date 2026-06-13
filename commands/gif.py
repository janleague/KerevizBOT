import asyncio
import io
import re
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from services.gif_converter import (
    DEFAULT_MAX_SIZE,
    GifConversionError,
    convert_image_bytes_to_gif,
)


MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024


def _safe_gif_filename(filename: str | None) -> str:
    stem = Path(filename or "image").stem or "image"
    stem = re.sub(r"[^0-9A-Za-z._-]+", "-", stem).strip(".-")
    return f"{stem or 'image'}.gif"


class Gif(commands.Cog):
    """Convert uploaded images to Discord-friendly GIF files."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="gif", description="Convert an uploaded image to a Discord-friendly GIF.")
    @app_commands.describe(
        image="Image attachment to convert.",
        max_size="Largest side in pixels. Lower values make smaller GIFs.",
        private="Only show the converted GIF to you.",
    )
    @app_commands.checks.cooldown(1, 10.0)
    async def gif(
        self,
        interaction: discord.Interaction,
        image: discord.Attachment,
        max_size: app_commands.Range[int, 64, 1024] = DEFAULT_MAX_SIZE,
        private: bool = False,
    ):
        if image.size and image.size > MAX_ATTACHMENT_BYTES:
            return await interaction.response.send_message(
                "That file is too large. Please upload an image under 20 MB.",
                ephemeral=True,
            )

        content_type = (image.content_type or "").lower()
        if content_type and not content_type.startswith("image/"):
            return await interaction.response.send_message(
                "Please upload an image file.",
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=private, thinking=True)

        try:
            image_bytes = await image.read()
            result = await asyncio.to_thread(
                convert_image_bytes_to_gif,
                image_bytes,
                max_size=int(max_size),
            )
        except GifConversionError as exc:
            return await interaction.followup.send(str(exc), ephemeral=True)
        except discord.HTTPException:
            return await interaction.followup.send("I could not download that attachment.", ephemeral=True)
        except Exception as exc:
            print(f"[GIF ERROR] {exc!r}")
            return await interaction.followup.send("Something went wrong while converting that image.", ephemeral=True)

        file = discord.File(io.BytesIO(result.data), filename=_safe_gif_filename(image.filename))
        await interaction.followup.send(
            content=f"Done: {result.width}x{result.height}, {result.frame_count} frame(s).",
            file=file,
        )

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CommandOnCooldown):
            message = f"Please wait {error.retry_after:.1f}s before using `/gif` again."
        else:
            message = "An unexpected `/gif` error occurred. Please try again."
            print(f"[GIF COMMAND ERROR] {error!r}")

        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot):
    cog = Gif(bot)
    await bot.add_cog(cog)
