import discord
from discord.ext import commands

from services.hypixel_client import (
    HypixelClientError,
    as_int,
    fetch_hypixel_player,
    format_number,
    format_timestamp,
    get_rank,
    network_level,
    ratio,
)


RANK_COLORS = {
    "MVP++": discord.Color.gold(),
    "MVP+": discord.Color.blue(),
    "MVP": discord.Color.teal(),
    "VIP+": discord.Color.green(),
    "VIP": discord.Color.dark_green(),
    "YOUTUBER": discord.Color.red(),
    "None": discord.Color.light_grey(),
}


class HypixelStats(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.api_key = bot.HYPIXEL_API_KEY

    @commands.command(name="stats", aliases=["hstats", "hypixel"], help="Show a player's general Hypixel profile stats.")
    async def hypixel_stats(self, ctx: commands.Context, username: str):
        try:
            bundle = await fetch_hypixel_player(self.api_key, username)
        except HypixelClientError as exc:
            return await ctx.send(f"Error: {exc}")

        player = bundle.player
        stats = player.get("stats", {}) or {}
        bedwars = stats.get("Bedwars", {}) or {}
        skywars = stats.get("SkyWars", {}) or {}
        duels = stats.get("Duels", {}) or {}

        displayname = player.get("displayname", bundle.username)
        rank = get_rank(player)
        level = network_level(player.get("networkExp"))
        karma = format_number(player.get("karma", 0))
        achievement_points = format_number(player.get("achievementPoints", 0))
        quests_completed = format_number(len(player.get("quests", {}) or {}))
        challenges_completed = format_number(sum(as_int(value) for value in (player.get("challenges", {}) or {}).values()))

        bw_wins = as_int(bedwars.get("wins_bedwars", 0))
        sw_wins = as_int(skywars.get("wins", 0))
        duels_wins = as_int(duels.get("wins", 0))
        duels_losses = as_int(duels.get("losses", 0))
        duels_kills = as_int(duels.get("kills", 0))
        duels_deaths = as_int(duels.get("deaths", 0))

        embed = discord.Embed(
            title=f"{displayname} | Hypixel Profile",
            description=f"Rank: `{rank}`\nNetwork Level: `{level:.2f}`",
            color=RANK_COLORS.get(rank, discord.Color.dark_grey()),
        )
        embed.add_field(name="Karma", value=karma, inline=True)
        embed.add_field(name="Achievement Points", value=achievement_points, inline=True)
        embed.add_field(name="Quests", value=quests_completed, inline=True)
        embed.add_field(name="Challenges", value=challenges_completed, inline=True)
        embed.add_field(name="First Login", value=format_timestamp(player.get("firstLogin")), inline=True)
        embed.add_field(name="Last Login", value=format_timestamp(player.get("lastLogin")), inline=True)
        embed.add_field(name="Last Game", value=str(player.get("lastGameType") or "Unknown"), inline=True)
        embed.add_field(name="Language", value=str(player.get("userLanguage") or "Unknown"), inline=True)
        embed.add_field(name="Known As", value=bundle.username, inline=True)

        embed.add_field(
            name="Game Snapshot",
            value=(
                f"BedWars Wins: `{format_number(bw_wins)}`\n"
                f"SkyWars Wins: `{format_number(sw_wins)}`\n"
                f"Duels Wins: `{format_number(duels_wins)}` | WLR `{ratio(duels_wins, duels_losses)}`\n"
                f"Duels Kills: `{format_number(duels_kills)}` | KDR `{ratio(duels_kills, duels_deaths)}`"
            ),
            inline=False,
        )
        embed.set_thumbnail(url=f"https://visage.surgeplay.com/head/{bundle.uuid}.png")
        embed.set_footer(text="Data fetched from the official Hypixel API.")
        await ctx.send(embed=embed)

    @hypixel_stats.error
    async def hypixel_stats_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingRequiredArgument):
            return await ctx.send("Usage: `!stats <minecraft_username>`")
        raise error


async def setup(bot):
    cog = HypixelStats(bot)
    for command in cog.get_commands():
        command.category = "Hypixel"
    await bot.add_cog(cog)
