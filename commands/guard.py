import asyncio
import re
from copy import deepcopy
from typing import Any

import discord
from discord.ext import commands

from services.guard_store import GuardStore, normalize_config


DISCORD_INVITE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:discord\.gg|discord(?:app)?\.com/invite)/[A-Za-z0-9-]+",
    re.IGNORECASE,
)


def parse_toggle(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in {"on", "true", "yes", "enable", "enabled", "ac"}:
        return True
    if lowered in {"off", "false", "no", "disable", "disabled", "kapat"}:
        return False
    raise ValueError("Use `on`, `off`, or `status`.")


class Guard(commands.Cog):
    """Lightweight server protection tools."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._lock = asyncio.Lock()
        self.store = GuardStore()
        self._data: dict[str, Any] = {"version": 1, "guilds": {}}

    async def initialize(self) -> None:
        self._data = await self.store.load_all()

    def _config(self, guild_id: int) -> dict[str, Any]:
        guilds = self._data.setdefault("guilds", {})
        key = str(guild_id)
        if key not in guilds:
            guilds[key] = normalize_config(None)
        guilds[key] = normalize_config(guilds[key])
        return guilds[key]

    async def _save_guild(self, guild_id: int) -> None:
        await self.store.save_guild(guild_id, deepcopy(self._config(guild_id)))

    @staticmethod
    def _is_exempt(member: discord.Member) -> bool:
        perms = member.guild_permissions
        return bool(perms.administrator or perms.manage_guild or perms.manage_messages)

    @staticmethod
    def _can_delete(message: discord.Message) -> bool:
        me = message.guild.me if message.guild else None
        if me is None:
            return False
        permissions = message.channel.permissions_for(me)
        return bool(permissions.manage_messages)

    def _status_embed(self, config: dict[str, Any]) -> discord.Embed:
        enabled = bool(config.get("anti_ad_enabled"))
        embed = discord.Embed(title="Guard: Anti-Ad", color=discord.Color.green())
        embed.add_field(name="Status", value="Enabled" if enabled else "Disabled", inline=True)
        embed.add_field(name="Blocks", value="Discord invite links", inline=True)
        embed.add_field(name="Allows", value="GIFs, images, attachments, and normal links", inline=False)
        embed.set_footer(text="Use !antiadd on or !antiadd off to change it.")
        return embed

    @commands.command(
        name="antiadd",
        aliases=["antiad", "antireklam"],
        help="Block Discord invite advertisements while allowing GIFs and normal media.",
    )
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def antiadd(self, ctx: commands.Context, value: str = "status"):
        value = value.strip().lower()
        async with self._lock:
            config = self._config(ctx.guild.id)
            if value in {"status", "config", "info"}:
                return await ctx.send(embed=self._status_embed(deepcopy(config)))

            try:
                enabled = parse_toggle(value)
            except ValueError as exc:
                return await ctx.send(str(exc))

            config["anti_ad_enabled"] = enabled
            await self._save_guild(ctx.guild.id)

        if enabled and not self._can_delete(ctx.message):
            return await ctx.send(
                "Anti-ad is enabled, but I need **Manage Messages** permission to delete invite links."
            )
        await ctx.send(f"Anti-ad is now **{'enabled' if enabled else 'disabled'}**.")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot or not message.content:
            return
        if not isinstance(message.author, discord.Member):
            return
        if self._is_exempt(message.author):
            return
        if not DISCORD_INVITE_RE.search(message.content):
            return

        async with self._lock:
            enabled = bool(self._config(message.guild.id).get("anti_ad_enabled"))
        if not enabled or not self._can_delete(message):
            return

        try:
            await message.delete()
        except (discord.Forbidden, discord.HTTPException):
            return

        try:
            await message.channel.send(
                f"{message.author.mention}, Discord invite links are not allowed here.",
                delete_after=6,
                allowed_mentions=discord.AllowedMentions(users=True),
            )
        except (discord.Forbidden, discord.HTTPException):
            pass

    @antiadd.error
    async def antiadd_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingPermissions):
            return await ctx.send("You need Manage Server permission to configure Guard.")
        if isinstance(error, commands.NoPrivateMessage):
            return await ctx.send("Guard commands can only be used in a server.")
        raise error


async def setup(bot: commands.Bot):
    cog = Guard(bot)
    await cog.initialize()
    await bot.add_cog(cog)
    command = bot.get_command("antiadd")
    if command:
        command.category = "Guard"
