import discord
from discord.ext import commands
import random
import aiohttp  # For fetching jokes and memes from APIs

HTTP_TIMEOUT = aiohttp.ClientTimeout(total=10)
CAT_API_URL = "https://api.thecatapi.com/v1/images/search?limit=1&mime_types=jpg,png"

class Fun(commands.Cog):
    """Fun commands for entertainment."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="joke", help="Tells a random joke.")
    async def joke(self, ctx: commands.Context):
        try:
            async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
                # Fetch a random joke from an API
                async with session.get("https://official-joke-api.appspot.com/random_joke") as response:
                    if response.status == 200:
                        data = await response.json()
                        joke = f"{data['setup']} - {data['punchline']}"
                        await ctx.send(joke)
                    else:
                        await ctx.send("❌ Failed to fetch a joke. Please try again later.")
        except Exception as e:
            await ctx.send("❌ An error occurred while fetching a joke.")
            print(f"[ERROR] joke command: {e}")

    @commands.command(name="roll", help="Rolls a dice (e.g., 1d6).")
    async def roll(self, ctx: commands.Context, dice: str):
        if not dice:
            await ctx.send("❌ You need to specify the dice format (e.g., `1d6`).")
            return
        try:
            rolls, limit = map(int, dice.lower().split("d"))
            if rolls < 1 or limit < 2:
                await ctx.send("❌ Dice must be at least `1d2`.")
                return
            if rolls > 100 or limit > 1000:
                await ctx.send("❌ Please keep rolls to `100d1000` or smaller.")
                return
            results = [random.randint(1, limit) for _ in range(rolls)]
            await ctx.send(f"🎲 Results: {', '.join(map(str, results))} (Total: {sum(results)})")
        except ValueError:
            await ctx.send("❌ Format has to be in NdM (e.g., 1d6).")

    @commands.command(name="8ball", help="Ask the magic 8-ball a question.")
    async def eight_ball(self, ctx: commands.Context, *, question: str = None):
        if not question:
            await ctx.send("❌ You need to ask a question!")
            return
        responses = [
            "It is certain.", "It is decidedly so.", "Without a doubt.", "Yes – definitely.",
            "You may rely on it.", "As I see it, yes.", "Most likely.", "Outlook good.",
            "Yes.", "Signs point to yes.", "Reply hazy, try again.", "Ask again later.",
            "Better not tell you now.", "Cannot predict now.", "Concentrate and ask again.",
            "Don't count on it.", "My reply is no.", "My sources say no.", "Outlook not so good.", "Very doubtful."
        ]
        await ctx.send(f"🎱 {random.choice(responses)}")

    @commands.command(name="meme", help="Fetches a random meme from the internet.")
    async def meme(self, ctx: commands.Context):
        try:
            async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
                # Fetch a random meme from an API
                async with session.get("https://meme-api.com/gimme") as response:
                    if response.status == 200:
                        data = await response.json()
                        meme_url = data["url"]
                        await ctx.send(f"🖼️ Here's a meme for you: {meme_url}")
                    else:
                        await ctx.send("❌ Failed to fetch a meme. Please try again later.")
        except Exception as e:
            await ctx.send("❌ An error occurred while fetching a meme.")
            print(f"[ERROR] meme command: {e}")

    @commands.command(name="cat", aliases=["kitty"], help="Fetches a random cute cat photo.")
    async def cat(self, ctx: commands.Context):
        try:
            async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
                async with session.get(CAT_API_URL) as response:
                    if response.status != 200:
                        return await ctx.send("❌ Failed to fetch a cat. Please try again later.")
                    data = await response.json()

            if not data or not isinstance(data, list) or not data[0].get("url"):
                return await ctx.send("❌ Could not find a cat photo right now. Please try again later.")

            embed = discord.Embed(
                title="🐱 Random Cat",
                description="Here is a tiny dose of cuteness for you.",
                color=discord.Color.orange(),
            )
            embed.set_image(url=data[0]["url"])
            embed.set_footer(text="Powered by TheCatAPI")
            await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send("❌ An error occurred while fetching a cat.")
            print(f"[ERROR] cat command: {e}")

    # ------------------------------------------------------------------
    # Error handler for fun commands
    # ------------------------------------------------------------------
    @joke.error
    async def joke_error(self, ctx: commands.Context, error: commands.CommandError):
        await ctx.send("❌ An error occurred while fetching a joke.")

    @roll.error
    async def roll_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("❌ Usage: `!roll <NdM>` (e.g., `1d6`).")
        else:
            await ctx.send("❌ An error occurred while rolling the dice.")

    @eight_ball.error
    async def eight_ball_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("❌ Usage: `!8ball <question>`.")
        else:
            await ctx.send("❌ An error occurred while consulting the magic 8-ball.")

    @meme.error
    async def meme_error(self, ctx: commands.Context, error: commands.CommandError):
        await ctx.send("❌ An error occurred while fetching a meme.")

    @cat.error
    async def cat_error(self, ctx: commands.Context, error: commands.CommandError):
        await ctx.send("❌ An error occurred while fetching a cat.")

# ----------------------------------------------------------------------
# Setup
# ----------------------------------------------------------------------
async def setup(bot: commands.Bot):
    cog = Fun(bot)
    for cmd in cog.get_commands():
        cmd.category = "Fun"  # Assign category for help command
    await bot.add_cog(cog)
