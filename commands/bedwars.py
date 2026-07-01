import discord
from discord.ext import commands

from services.hypixel_client import (
    HypixelClientError,
    as_int,
    fetch_hypixel_player,
    format_hypixel_error,
    get_rank,
    ratio,
)


class Bedwars(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="bedwars", help="Displays BedWars statistics for a given player.")
    async def bedwars(self, ctx: commands.Context, username: str):
        async with ctx.typing():
            try:
                bundle = await fetch_hypixel_player(self.bot.HYPIXEL_API_KEY, username)
            except HypixelClientError as exc:
                return await ctx.send(format_hypixel_error(exc))

        player = bundle.player
        uuid = bundle.uuid
        stats = (player.get("stats", {}) or {}).get("Bedwars", {}) or {}

        displayname = player.get("displayname", bundle.username)
        rank = get_rank(player)
        color = self.get_rank_color(rank)
        level = as_int(player.get("achievements", {}).get("bedwars_level", 0))

        wins = as_int(stats.get("wins_bedwars", 0))
        losses = as_int(stats.get("losses_bedwars", 0))
        kills = as_int(stats.get("kills_bedwars", 0))
        deaths = as_int(stats.get("deaths_bedwars", 1)) or 1
        fkills = as_int(stats.get("final_kills_bedwars", 0))
        fdeaths = as_int(stats.get("final_deaths_bedwars", 1)) or 1
        beds_broken = as_int(stats.get("beds_broken_bedwars", 0))
        beds_lost = as_int(stats.get("beds_lost_bedwars", 1)) or 1

        kdr = ratio(kills, deaths)
        fkdr = ratio(fkills, fdeaths)
        bblr = ratio(beds_broken, beds_lost)
        wlr = ratio(wins, losses)

        pro_score = (
            min(wlr, 10) * 20
            + min(fkdr, 10) * 20
            + min(kdr, 10) * 10
            + min(bblr, 10) * 15
            + (min(level, 500) / 500 * 15)
        )
        pro_score = min(100, round(pro_score))
        bar_blocks = pro_score // 10
        bar = "[" + ("#" * bar_blocks) + ("-" * (10 - bar_blocks)) + "]"

        if pro_score >= 90:
            comment = "Godlike performance. Truly elite."
        elif pro_score >= 70:
            comment = "High-level player! Hypixel knows your name."
        elif pro_score >= 50:
            comment = "Not bad, you're getting there!"
        else:
            comment = "You're learning, keep grinding!"

        embed = discord.Embed(
            title=f"**{displayname}** | {rank}",
            description=f"Level: `{level} stars`",
            color=color,
        )
        embed.add_field(
            name="Pro Score",
            value=f"{pro_score}%\n{bar}\n*{comment}*",
            inline=False,
        )
        embed.add_field(name="Wins", value=wins, inline=True)
        embed.add_field(name="Losses", value=losses, inline=True)
        embed.add_field(name="W/L Ratio", value=wlr, inline=True)
        embed.add_field(name="Final Kills", value=fkills, inline=True)
        embed.add_field(name="Final Deaths", value=fdeaths, inline=True)
        embed.add_field(name="FKDR", value=fkdr, inline=True)
        embed.add_field(name="Kills", value=kills, inline=True)
        embed.add_field(name="Deaths", value=deaths, inline=True)
        embed.add_field(name="KDR", value=kdr, inline=True)
        embed.add_field(name="Beds Broken", value=beds_broken, inline=True)
        embed.add_field(name="Beds Lost", value=beds_lost, inline=True)
        embed.add_field(name="BBLR", value=bblr, inline=True)
        embed.set_thumbnail(url=f"https://visage.surgeplay.com/head/{uuid}.png")
        embed.set_footer(text="Data fetched from the official Hypixel API.")

        await ctx.send(embed=embed)

    @bedwars.error
    async def bedwars_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingRequiredArgument):
            return await ctx.send("Usage: `!bedwars <minecraft_username>`")
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
    cog = Bedwars(bot)
    for command in cog.get_commands():
        command.category = "Hypixel"
    await bot.add_cog(cog)
