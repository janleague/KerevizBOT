import asyncio
import base64
import io
import random
from typing import Any, Dict, Optional, Tuple

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import BucketType

from services.minecraft_server_store import MinecraftServerStore

# Config
SERVERS_FILE = "servers.txt"
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=10)
API_HEADERS = {"User-Agent": "RandomMCBot/Simple/1.1"}
MCSTATUS_URL = "https://api.mcstatus.io/v2/status/java/{host}"
MCSRVS_URL = "https://api.mcsrvstat.us/2/{host}"

# ---- helper: owner-only check for slash commands ----
async def _is_bot_owner(interaction: discord.Interaction) -> bool:
    owner_id = interaction.client.owner_id
    if owner_id is None:
        try:
            app = await interaction.client.application_info()
            if app and app.owner:
                owner_id = app.owner.id
        except Exception:
            pass
    return interaction.user.id == owner_id


def owner_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        if await _is_bot_owner(interaction):
            return True
        raise app_commands.CheckFailure("Owner only")
    return app_commands.check(predicate)


class RandomMinecraftServer(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = MinecraftServerStore()

    async def initialize(self) -> None:
        await self.store.seed_from_file(SERVERS_FILE)

    # ---------- TEXT COMMAND (shown in help under Fun) ----------
    @commands.cooldown(1, 6, BucketType.user)
    @commands.command(name="randomminecraftserver", aliases=["rms"], help="Show a random Minecraft server")
    async def randomminecraftserver(self, ctx: commands.Context):
        hosts = await self.store.list_servers()
        if not hosts:
            return await ctx.reply("❌ Server list is empty in Firebase. Use `/rmsadd` to add one.")
        random.shuffle(hosts)

        chosen: Optional[Tuple[str, Dict[str, Any]]] = None
        async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT, headers=API_HEADERS) as session:
            for host in hosts[:25]:
                data = await self._status_any(session, host)
                if not data:
                    continue
                chosen = (host, data)
                if data.get("online") is True or (data.get("players", {}).get("online", 0) > 0):
                    break

        if not chosen:
            return await ctx.reply("❌ Couldn't find an active server right now. Try again.")
        host, data = chosen

        embed, file = self._build_embed(host, data)
        if file:
            await ctx.send(embed=embed, file=file)
        else:
            await ctx.send(embed=embed)

    # ---------- SLASH COMMAND (owner-only, hidden from text help) ----------
    @app_commands.command(name="rmsadd", description="Owner only: add a server to Firebase")
    @owner_only()
    async def slash_rmsadd(self, interaction: discord.Interaction, host: str):
        host = host.strip()
        if not host:
            return await interaction.response.send_message("Usage: /rmsadd <host[:port]>", ephemeral=True)
        try:
            added = await self.store.add_server(host)
            if not added:
                return await interaction.response.send_message("⚠️ Already in Firebase.", ephemeral=True)
            await interaction.response.send_message(f"✅ Added `{host}` to Firebase.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Could not update Firebase: {e!s}", ephemeral=True)

    # Optional: nicer error if non-owner tries
    @slash_rmsadd.error
    async def _slash_rmsadd_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            try:
                await interaction.response.send_message("⛔ Owner only.", ephemeral=True)
            except Exception:
                pass

    async def _status_any(self, session: aiohttp.ClientSession, host: str) -> Optional[Dict[str, Any]]:
        async def mcstatus():
            try:
                async with session.get(MCSTATUS_URL.format(host=host)) as r:
                    if r.status == 200:
                        d = await r.json()
                        return {
                            "online": d.get("online"),
                            "players": d.get("players", {}),
                            "version": (d.get("version", {}) or {}).get("name") or (d.get("version", {}) or {}).get("name_raw"),
                            "motd": (d.get("motd", {}) or {}).get("clean", []),
                            "icon": d.get("icon"),
                        }
            except Exception:
                return None

        async def mcsrvstat():
            try:
                async with session.get(MCSRVS_URL.format(host=host)) as r:
                    if r.status == 200:
                        d = await r.json()
                        return {
                            "online": d.get("online"),
                            "players": d.get("players", {}),
                            "version": d.get("version"),
                            "motd": (d.get("motd", {}) or {}).get("clean", []),
                            "icon": d.get("icon"),
                        }
            except Exception:
                return None

        tasks = [asyncio.create_task(mcstatus()), asyncio.create_task(mcsrvstat())]
        try:
            for task in asyncio.as_completed(tasks, timeout=8):
                res = await task
                if res:
                    return res
        except asyncio.TimeoutError:
            pass
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
        for task in tasks:
            if task.cancelled():
                continue
            try:
                res = task.result()
                if res:
                    return res
            except asyncio.CancelledError:
                continue
            except Exception:
                pass
        return None

    def _build_embed(self, host: str, data: Dict[str, Any]) -> Tuple[discord.Embed, Optional[discord.File]]:
        players = data.get("players") or {}
        online = players.get("online", 0)
        maxp = players.get("max", 0)
        version = data.get("version") or "Unknown"

        motd_raw = data.get("motd") or []
        if isinstance(motd_raw, list):
            motd_text = " ".join(s.strip() for s in motd_raw if isinstance(s, str))
        else:
            motd_text = str(motd_raw)
        motd_text = " ".join(motd_text.replace("\n", " ").split()).strip() or "(No MOTD)"

        embed = discord.Embed(title="Random Minecraft Server", color=discord.Color.green())
        embed.add_field(name="Server IP", value=f"`{host}`", inline=False)
        embed.add_field(name="Players", value=f"{online}/{maxp}", inline=True)
        embed.add_field(name="Version", value=str(version), inline=True)
        embed.add_field(name="MOTD", value=motd_text[:1000], inline=False)
        embed.set_footer(text="Source: Firebase Firestore + live status")

        icon = data.get("icon")
        if isinstance(icon, str) and icon.startswith("data:image"):
            try:
                raw = base64.b64decode(icon.split(",", 1)[1])
                file = discord.File(io.BytesIO(raw), filename="favicon.png")
                embed.set_thumbnail(url="attachment://favicon.png")
                return embed, file
            except Exception:
                pass
        return embed, None

async def setup(bot: commands.Bot):
    cog = RandomMinecraftServer(bot)
    await cog.initialize()
    await bot.add_cog(cog)
    # Put text command under Fun in your help menu
    cmd = bot.get_command("randomminecraftserver")
    if cmd:
        cmd.category = "Fun"
    # tree.sync() is already called in bot.py on_ready — no need to sync here
