import asyncio
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import discord
from discord.ext import commands

from services.deleted_image_store import DeletedImageStore


DEFAULT_LOG_CHANNEL_ID = 1411317215579607132
CACHE_DIR = Path("deleted_image_cache")
IMAGE_EXTENSIONS = {".apng", ".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_FILES_PER_MESSAGE = 10
DEFAULT_CACHE_RETENTION_DAYS = 30
DEFAULT_CACHE_CLEANUP_INTERVAL_SECONDS = 24 * 60 * 60
DEFAULT_CACHE_CLEANUP_BATCH_SIZE = 200


def deleted_image_log_channel_id() -> int:
    raw_value = os.getenv("DELETED_IMAGE_LOG_CHANNEL_ID")
    if raw_value:
        try:
            return int(raw_value)
        except ValueError:
            pass
    return DEFAULT_LOG_CHANNEL_ID


def env_int(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value:
        try:
            return int(raw_value.strip().strip('"'))
        except ValueError:
            pass
    return default


def deleted_image_cache_cutoff(retention_days: int, *, current_time: datetime | None = None) -> datetime:
    now = current_time or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc) - timedelta(days=max(1, int(retention_days)))


def safe_filename(filename: str) -> str:
    clean_name = re.sub(r"[^A-Za-z0-9._-]+", "_", filename).strip("._")
    return clean_name[:120] or "deleted-image"


class DeletedImageLogs(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = DeletedImageStore()
        self.log_channel_id = deleted_image_log_channel_id()
        self._memory_cache: dict[int, dict[str, Any]] = {}
        self.retention_days = env_int("DELETED_IMAGE_CACHE_RETENTION_DAYS", DEFAULT_CACHE_RETENTION_DAYS)
        self.cleanup_interval = env_int(
            "DELETED_IMAGE_CACHE_CLEANUP_INTERVAL",
            DEFAULT_CACHE_CLEANUP_INTERVAL_SECONDS,
        )
        self.cleanup_batch_size = env_int("DELETED_IMAGE_CACHE_CLEANUP_BATCH_SIZE", DEFAULT_CACHE_CLEANUP_BATCH_SIZE)
        self._cleanup_task: asyncio.Task | None = None

    def cog_unload(self) -> None:
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        self._start_cleanup_runner()

    def _start_cleanup_runner(self) -> None:
        if self._cleanup_task and not self._cleanup_task.done():
            return
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def _cleanup_loop(self) -> None:
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                await self.cleanup_old_cache_entries()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[DELETED-IMAGE-LOGS] Cache cleanup failed: {exc}")
            await asyncio.sleep(max(3600, int(self.cleanup_interval)))

    @staticmethod
    def _is_image_attachment(attachment: discord.Attachment) -> bool:
        content_type = (attachment.content_type or "").lower()
        if content_type.startswith("image/"):
            return True
        return Path(attachment.filename).suffix.lower() in IMAGE_EXTENSIONS

    def _image_attachments(self, message: discord.Message) -> list[discord.Attachment]:
        return [attachment for attachment in message.attachments if self._is_image_attachment(attachment)]

    @staticmethod
    def _cache_path(message: discord.Message, attachment: discord.Attachment) -> Path:
        filename = safe_filename(attachment.filename)
        return CACHE_DIR / str(message.guild.id) / f"{message.id}_{attachment.id}_{filename}"

    @staticmethod
    async def _save_attachment(attachment: discord.Attachment, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            await attachment.save(path, use_cached=True)
        except TypeError:
            await attachment.save(path)

    def _payload(self, message: discord.Message, images: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "guild_id": str(message.guild.id),
            "channel_id": str(message.channel.id),
            "message_id": str(message.id),
            "author_id": str(message.author.id),
            "author_name": str(message.author),
            "content": message.content[:4000] if message.content else "",
            "images": images,
        }

    async def _cache_message_images(self, message: discord.Message, persist: bool = True) -> list[dict[str, Any]]:
        images: list[dict[str, Any]] = []

        for attachment in self._image_attachments(message):
            image_info: dict[str, Any] = {
                "attachment_id": str(attachment.id),
                "filename": attachment.filename,
                "content_type": attachment.content_type,
                "size": int(attachment.size or 0),
                "url": attachment.url,
                "proxy_url": attachment.proxy_url,
                "cached": False,
                "cache_path": None,
            }

            if attachment.size and attachment.size > MAX_IMAGE_BYTES:
                image_info["error"] = "Image is larger than the local cache limit."
                images.append(image_info)
                continue

            path = self._cache_path(message, attachment)
            try:
                await self._save_attachment(attachment, path)
            except (discord.HTTPException, OSError):
                image_info["error"] = "Could not cache image."
                images.append(image_info)
                continue

            image_info["cached"] = True
            image_info["cache_path"] = str(path)
            images.append(image_info)

        if images:
            payload = self._payload(message, images)
            self._memory_cache[message.id] = payload
            if persist:
                try:
                    await self.store.save_message(message.id, payload)
                except Exception:
                    pass
        return images

    async def _log_channel(self):
        channel = self.bot.get_channel(self.log_channel_id)
        if channel is None:
            channel = await self.bot.fetch_channel(self.log_channel_id)
        return channel if hasattr(channel, "send") else None

    @staticmethod
    def _channel_text(message: discord.Message, data: dict[str, Any]) -> str:
        mention = getattr(message.channel, "mention", None)
        if mention:
            return mention
        channel_id = data.get("channel_id")
        return f"Channel ID: {channel_id}" if channel_id else "Unknown"

    def _build_embed(self, message: discord.Message, data: dict[str, Any], file_count: int) -> discord.Embed:
        embed = discord.Embed(
            title="Deleted Image",
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Original Channel", value=self._channel_text(message, data), inline=False)
        embed.add_field(
            name="Author",
            value=f"{data.get('author_name', message.author)} (ID: {data.get('author_id', message.author.id)})",
            inline=False,
        )
        content = str(data.get("content") or "").strip()
        if content:
            embed.add_field(name="Message Content", value=content[:1000], inline=False)
        embed.add_field(name="Recovered Images", value=str(file_count), inline=True)
        embed.set_footer(text=f"Message ID: {data.get('message_id', message.id)}")
        return embed

    @staticmethod
    def _delete_cached_files(data: dict[str, Any]) -> None:
        for image in data.get("images", []):
            cache_path = image.get("cache_path") if isinstance(image, dict) else None
            if not cache_path:
                continue
            path = Path(str(cache_path))
            try:
                if path.is_file():
                    path.unlink()
            except OSError:
                pass

    @staticmethod
    def _delete_empty_cache_dirs() -> None:
        if not CACHE_DIR.exists():
            return
        for path in sorted(CACHE_DIR.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if not path.is_dir():
                continue
            try:
                path.rmdir()
            except OSError:
                pass

    @staticmethod
    def _delete_old_local_cache_files(cutoff: datetime) -> int:
        if not CACHE_DIR.exists():
            return 0

        cutoff_ts = cutoff.timestamp()
        deleted = 0
        for path in CACHE_DIR.rglob("*"):
            if not path.is_file():
                continue
            try:
                if path.stat().st_mtime <= cutoff_ts:
                    path.unlink()
                    deleted += 1
            except OSError:
                pass
        DeletedImageLogs._delete_empty_cache_dirs()
        return deleted

    async def cleanup_old_cache_entries(self) -> int:
        cutoff = deleted_image_cache_cutoff(self.retention_days)
        deleted_docs = 0

        while True:
            payloads = await self.store.delete_old_messages(cutoff, limit=self.cleanup_batch_size)
            if not payloads:
                break
            deleted_docs += len(payloads)
            for payload in payloads:
                self._delete_cached_files(payload)
                try:
                    self._memory_cache.pop(int(payload.get("message_id")), None)
                except (TypeError, ValueError):
                    pass
            if len(payloads) < self.cleanup_batch_size:
                break

        deleted_files = self._delete_old_local_cache_files(cutoff)
        if deleted_docs or deleted_files:
            print(
                "[DELETED-IMAGE-LOGS] Deleted "
                f"{deleted_docs} old Firestore cache record(s) and {deleted_files} old local cache file(s)."
            )
        return deleted_docs

    async def _send_deleted_image_log(self, message: discord.Message, data: dict[str, Any]) -> bool:
        channel = await self._log_channel()
        if channel is None:
            return False

        images = [image for image in data.get("images", []) if isinstance(image, dict)]
        file_images = [
            image
            for image in images
            if image.get("cached") and image.get("cache_path") and Path(str(image["cache_path"])).is_file()
        ]
        embed = self._build_embed(message, data, len(file_images))

        if not file_images:
            embed.add_field(name="Note", value="The image could not be recovered from cache.", inline=False)
            await channel.send(embed=embed)
            return True

        for index in range(0, len(file_images), MAX_FILES_PER_MESSAGE):
            chunk = file_images[index : index + MAX_FILES_PER_MESSAGE]
            files = [
                discord.File(str(image["cache_path"]), filename=safe_filename(str(image.get("filename") or "image")))
                for image in chunk
            ]
            try:
                if index == 0:
                    await channel.send(embed=embed, files=files)
                else:
                    await channel.send(
                        content=f"Additional deleted image files for message `{data.get('message_id', message.id)}`.",
                        files=files,
                    )
            finally:
                for file in files:
                    file.close()
        return True

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.channel.id == self.log_channel_id:
            return
        if not self._image_attachments(message):
            return
        await self._cache_message_images(message)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if not message.guild or message.channel.id == self.log_channel_id:
            return

        data = self._memory_cache.get(message.id)
        if data is None:
            data = await self.store.load_message(message.id)
        if data is None:
            images = await self._cache_message_images(message, persist=False)
            if not images:
                return
            data = self._payload(message, images)

        try:
            sent = await self._send_deleted_image_log(message, data)
        except (discord.Forbidden, discord.HTTPException):
            return

        if sent:
            self._memory_cache.pop(message.id, None)
            try:
                await self.store.delete_message(message.id)
            except Exception:
                pass
            self._delete_cached_files(data)


async def setup(bot: commands.Bot):
    cog = DeletedImageLogs(bot)
    await bot.add_cog(cog)
    if bot.is_ready():
        cog._start_cleanup_runner()
