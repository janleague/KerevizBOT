import discord
from discord.ext import commands


class Clear(commands.Cog):
    """Simple message cleanup command."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="clear", aliases=["purge"], help="Delete a number of recent messages.")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    @commands.bot_has_permissions(manage_messages=True, read_message_history=True)
    async def clear(self, ctx: commands.Context, amount: int):
        if amount < 1:
            return await ctx.send("Usage: `!clear <message count>`", delete_after=4)
        if amount > 100:
            return await ctx.send("Please choose `100` messages or fewer.", delete_after=4)

        deleted = await ctx.channel.purge(limit=amount + 1)
        cleaned = max(0, len(deleted) - 1)
        await ctx.send(f"Deleted `{cleaned}` message(s).", delete_after=3)

    @clear.error
    async def clear_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingRequiredArgument):
            return await ctx.send("Usage: `!clear <message count>`", delete_after=4)
        if isinstance(error, commands.BadArgument):
            return await ctx.send("Message count must be a number.", delete_after=4)
        if isinstance(error, commands.MissingPermissions):
            return await ctx.send("You need **Manage Messages** permission to use this command.", delete_after=4)
        if isinstance(error, commands.BotMissingPermissions):
            return await ctx.send("I need **Manage Messages** permission to clear messages.", delete_after=4)
        if isinstance(error, commands.NoPrivateMessage):
            return await ctx.send("This command can only be used in a server.")
        raise error


async def setup(bot: commands.Bot):
    cog = Clear(bot)
    await bot.add_cog(cog)
    command = bot.get_command("clear")
    if command:
        command.category = "Admin"
