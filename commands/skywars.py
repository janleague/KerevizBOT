# commands/skywars.py
import discord
from discord.ext import commands
import aiohttp
import re
import asyncio

HTTP_TIMEOUT = aiohttp.ClientTimeout(total=12)

class Skywars(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # keep the same pattern as bedwars: key comes from bot instance
        self.api_key = bot.HYPIXEL_API_KEY

    @commands.command(
        name="skywars",
        help="Displays SkyWars statistics for a given player."
    )
    async def skywars(self, ctx, username):
        if not self.api_key:
            await ctx.send("❌ HYPIXEL_API_KEY is not configured.")
            return

        # ---- identical HTTP flow as bedwars.py ----
        try:
            async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
                # Mojang → UUID
                async with session.get(f"https://api.mojang.com/users/profiles/minecraft/{username.lower()}") as mojang_resp:
                    if mojang_resp.status != 200:
                        await ctx.send("❌ Player not found. Please check the username and try again.")
                        return
                    mojang_data = await mojang_resp.json()
                    uuid = mojang_data["id"]

                # Hypixel /player
                async with session.get(f"https://api.hypixel.net/player?key={self.api_key}&uuid={uuid}") as hypixel_resp:
                    if hypixel_resp.status != 200:
                        await ctx.send("❌ Hypixel API is unavailable right now.")
                        return
                    data = await hypixel_resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            await ctx.send("❌ Failed to contact Mojang or Hypixel. Please try again later.")
            return

        if not data.get("success") or not data.get("player"):
            await ctx.send("❌ Failed to retrieve Hypixel data.")
            return

        player = data["player"]
        sw = (player.get("stats", {}) or {}).get("SkyWars", {}) or {}

        # ---- match bedwars header style ----
        displayname = player.get("displayname", username)
        rank = self.get_rank(player)
        color = self.get_rank_color(rank)

        # SkyWars “level/star” extraction (best-effort)
        # Prefer formatted star like "[12✫]" if present
        level_fmt = sw.get("levelFormatted") or sw.get("level_formatted") or ""
        level = 0
        m = re.search(r"\[(\d+)", level_fmt)
        if m:
            level = int(m.group(1))
        else:
            # fallback: rough estimate from experience if available
            exp = int(sw.get("skywars_experience", 0))
            # conservative approx (keeps layout consistent even if exact not provided)
            level = min(60, exp // 1000)

        # Basic totals
        wins   = int(sw.get("wins", 0))
        losses = int(sw.get("losses", 0))
        kills  = int(sw.get("kills", 0))
        deaths = int(sw.get("deaths", 1)) or 1
        assists = int(sw.get("assists", 0))
        coins  = int(sw.get("coins", 0))
        souls  = int(sw.get("souls", 0))

        # Ratios (match bedwars style & rounding)
        kdr = round(kills / (deaths or 1), 2)
        wlr = round(wins / (losses or 1), 2)

        # “Pro Score” to mirror BedWars look (weights tuned for SW)
        # keep same 0–100 scale and 10-block bar
        pro_score = (
            min(wlr, 10) * 25 +
            min(kdr, 10) * 35 +
            min(wins, 1000) / 1000 * 20 +
            (min(level, 60) / 60 * 20)
        )
        pro_score = min(100, round(pro_score))
        bar_blocks = pro_score // 10
        bar = "".join(["🟩" if i < bar_blocks else "⬜" for i in range(10)])

        if pro_score >= 90:
            comment = "Godlike performance. Truly elite."
        elif pro_score >= 70:
            comment = "High-level player! Hypixel knows your name."
        elif pro_score >= 50:
            comment = "Not bad, you're getting there!"
        else:
            comment = "You're learning, keep grinding!"

        # ---- embed identical skeleton to bedwars.py ----
        embed = discord.Embed(
            title=f"**{displayname}** | {rank}",
            description=f"Level: `{level}✫`",
            color=color
        )

        embed.add_field(
            name="Pro Score",
            value=f"{pro_score}%\n{bar}\n*{comment}*",
            inline=False
        )

        # Row 1
        embed.add_field(name="Wins", value=wins, inline=True)
        embed.add_field(name="Losses", value=losses, inline=True)
        embed.add_field(name="W/L Ratio", value=wlr, inline=True)

        # Row 2 (SkyWars doesn’t have “finals”; show main combat trio)
        embed.add_field(name="Kills", value=kills, inline=True)
        embed.add_field(name="Deaths", value=deaths, inline=True)
        embed.add_field(name="KDR", value=kdr, inline=True)

        # Row 3 (utility stats to fill the 3 columns like bedwars)
        embed.add_field(name="Souls", value=souls, inline=True)
        embed.add_field(name="Coins", value=coins, inline=True)
        embed.add_field(name="Assists", value=assists, inline=True)

        embed.set_thumbnail(url=f"https://visage.surgeplay.com/head/{uuid}.png")
        embed.set_footer(text="Data fetched from Hypixel API.")

        await ctx.send(embed=embed)

    # ---- reuse exact rank helpers from bedwars.py for 1:1 look ----
    def get_rank(self, player):
        if player.get("rank") and player["rank"] != "NORMAL":
            return player["rank"]
        elif player.get("monthlyPackageRank") == "SUPERSTAR":
            return "MVP++"
        elif player.get("newPackageRank"):
            return player["newPackageRank"].replace("_PLUS", "+").replace("_", "")
        elif player.get("packageRank"):
            return player["packageRank"].replace("_PLUS", "+").replace("_", "")
        else:
            return "None"

    def get_rank_color(self, rank):
        colors = {
            "MVP++": discord.Color.gold(),
            "MVP+": discord.Color.blue(),
            "MVP": discord.Color.teal(),
            "VIP+": discord.Color.green(),
            "VIP": discord.Color.dark_green(),
            "YOUTUBER": discord.Color.red(),
            "None": discord.Color.light_grey()
        }
        return colors.get(rank, discord.Color.dark_grey())

async def setup(bot):
    cog = Skywars(bot)
    # ensure help shows under the same category as bedwars
    for command in cog.get_commands():
        command.category = "Hypixel"
    await bot.add_cog(cog)
