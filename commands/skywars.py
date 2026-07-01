import re

import discord
from discord.ext import commands

from services.hypixel_client import (
    HypixelClientError,
    as_int,
    fetch_hypixel_player,
    format_hypixel_error,
    get_rank,
    ratio,
    skywars_pro_score,
)


class Skywars(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="skywars", aliases=["sw"], help="Displays SkyWars statistics for a given player.")
    async def skywars(self, ctx: commands.Context, username: str):
        async with ctx.typing():
            try:
                bundle = await fetch_hypixel_player(self.bot.HYPIXEL_API_KEY, username)
            except HypixelClientError as exc:
                return await ctx.send(format_hypixel_error(exc))

        player = bundle.player
        uuid = bundle.uuid
        sw = (player.get("stats", {}) or {}).get("SkyWars", {}) or {}

        displayname = player.get("displayname", bundle.username)
        rank = get_rank(player)
        color = self.get_rank_color(rank)

        level_fmt = sw.get("levelFormatted") or sw.get("level_formatted") or ""
        level = 0
        match = re.search(r"\[(\d+)", str(level_fmt))
        if match:
            level = int(match.group(1))
        else:
            exp = as_int(sw.get("skywars_experience", 0))
            level = min(60, exp // 1000)

        wins = as_int(sw.get("wins", 0))
        losses = as_int(sw.get("losses", 0))
        kills = as_int(sw.get("kills", 0))
        deaths = as_int(sw.get("deaths", 1)) or 1
        assists = as_int(sw.get("assists", 0))
        coins = as_int(sw.get("coins", 0))
        souls = as_int(sw.get("souls", 0))

        kdr = ratio(kills, deaths)
        wlr = ratio(wins, losses)

        pro_score = skywars_pro_score(
            wins=wins,
            losses=losses,
            kills=kills,
            deaths=deaths,
            level=level,
        )

        embed = discord.Embed(
            title=f"**{displayname}** | {rank}",
            description=f"Level: `{level}` \u2B50",
            color=color,
        )
        embed.add_field(
            name="Pro Score",
            value=f"{pro_score.score}% - **{pro_score.tier}**\n{pro_score.bar}\n*{pro_score.comment}*",
            inline=False,
        )
        embed.add_field(name="Wins", value=wins, inline=True)
        embed.add_field(name="Losses", value=losses, inline=True)
        embed.add_field(name="W/L Ratio", value=wlr, inline=True)
        embed.add_field(name="Kills", value=kills, inline=True)
        embed.add_field(name="Deaths", value=deaths, inline=True)
        embed.add_field(name="KDR", value=kdr, inline=True)
        embed.add_field(name="Souls", value=souls, inline=True)
        embed.add_field(name="Coins", value=coins, inline=True)
        embed.add_field(name="Assists", value=assists, inline=True)
        embed.set_thumbnail(url=f"https://visage.surgeplay.com/head/{uuid}.png")
        embed.set_footer(text="Data fetched from the official Hypixel API.")

        await ctx.send(embed=embed)

    @skywars.error
    async def skywars_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingRequiredArgument):
            return await ctx.send("Usage: `!skywars <minecraft_username>`")
        raise error

    def get_rank_color(self, rank: str) -> discord.Color:
        colors = {
            "MVP++": discord.Color.gold(),
            "MVP+": discord.Color.blue(),
            "MVP": discord.Color.teal(),
            "VIP+": discord.Color.green(),
            "VIP": discord.Color.dark_green(),
            "YOUTUBER": discord.Color.red(),
            "None": discord.Color.light_grey(),
        }
        return colors.get(rank, discord.Color.dark_grey())


async def setup(bot):
    cog = Skywars(bot)
    for command in cog.get_commands():
        command.category = "Hypixel"
    await bot.add_cog(cog)
