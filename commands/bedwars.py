import discord
from discord.ext import commands
import aiohttp

class Bedwars(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.api_key = bot.HYPIXEL_API_KEY

    @commands.command(
        name="bedwars",
        help="Displays BedWars statistics for a given player."
    )
    async def bedwars(self, ctx, username):
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://api.mojang.com/users/profiles/minecraft/{username.lower()}"
            ) as mojang_resp:
                if mojang_resp.status != 200:
                    await ctx.send("❌ Player not found. Please check the username and try again.")
                    return
                mojang_data = await mojang_resp.json()
                uuid = mojang_data["id"]

            async with session.get(
                f"https://api.hypixel.net/player?key={self.api_key}&uuid={uuid}"
            ) as hypixel_resp:
                data = await hypixel_resp.json()

        if not data.get("success") or not data.get("player"):
            await ctx.send("❌ Failed to retrieve Hypixel data.")
            return

        player = data["player"]
        stats = player.get("stats", {}).get("Bedwars", {})

        displayname = player.get("displayname", username)
        rank = self.get_rank(player)
        color = self.get_rank_color(rank)
        level = int(player.get("achievements", {}).get("bedwars_level", 0))

        wins = stats.get("wins_bedwars", 0)
        losses = stats.get("losses_bedwars", 0)
        kills = stats.get("kills_bedwars", 0)
        deaths = stats.get("deaths_bedwars", 1) or 1
        fkills = stats.get("final_kills_bedwars", 0)
        fdeaths = stats.get("final_deaths_bedwars", 1) or 1
        beds_broken = stats.get("beds_broken_bedwars", 0)
        beds_lost = stats.get("beds_lost_bedwars", 1) or 1

        kdr = round(kills / deaths, 2)
        fkdr = round(fkills / fdeaths, 2)
        bblr = round(beds_broken / beds_lost, 2)
        wlr = round(wins / (losses or 1), 2)

        embed = discord.Embed(
            title=f"{displayname} | {rank}",
            description=f"Level: `{level}⭐`",
            color=color
        )

        pro_score = (
            min(wlr, 10) * 20 +
            min(fkdr, 10) * 20 +
            min(kdr, 10) * 10 +
            min(bblr, 10) * 15 +
            (min(level, 500) / 500 * 15)
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

        embed.add_field(
            name="Pro Score",
            value=f"{pro_score}%\n{bar}\n*{comment}*",
            inline=False
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
        embed.set_footer(text="Data fetched from Hypixel API.")

        await ctx.send(embed=embed)

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
    cog = Bedwars(bot)
    for command in cog.get_commands():
        command.category = "Hypixel"
    await bot.add_cog(cog)