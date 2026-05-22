import discord
from discord.ext import commands
from datetime import datetime

class Unban(commands.Cog):
    """Unban command for administrators."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ------------------------------------------------------------------
    # !unban username#discriminator | user_id [reason]
    # ------------------------------------------------------------------
    @commands.command(name="unban", help="Unban a user by username#discriminator or user ID.")
    @commands.has_permissions(ban_members=True)
    @commands.guild_only()
    async def unban(self, ctx: commands.Context, target: str, *, reason: str = "No reason provided"):
        guild = ctx.guild

        # ✅ Proper way to collect bans in discord.py 2.x
        banned_users = [entry async for entry in guild.bans(limit=None)]
        user_to_unban = None

        # Try by user ID first
        try:
            uid = int(target)
            for ban_entry in banned_users:
                if ban_entry.user.id == uid:
                    user_to_unban = ban_entry.user
                    break
        except ValueError:
            # Not an ID, try username#discriminator
            for ban_entry in banned_users:
                if str(ban_entry.user) == target:
                    user_to_unban = ban_entry.user
                    break

        if not user_to_unban:
            return await ctx.send("❌ Could not find that banned user.")

        try:
            await guild.unban(user_to_unban, reason=reason)
        except discord.Forbidden:
            return await ctx.send("⛔ I don’t have permission to unban that user.")

        embed = discord.Embed(
            title="🔓 Member Unbanned",
            description=(
                f"**User:** {user_to_unban} (`{user_to_unban.id}`)\n"
                f"**Moderator:** {ctx.author.mention}\n"
                f"**Reason:** {reason}"
            ),
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        if user_to_unban.avatar:
            embed.set_thumbnail(url=user_to_unban.avatar.url)
        embed.set_footer(text="Kereviz Bot • Unban Hammer")
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------
    # Error handler for unban
    # ------------------------------------------------------------------
    @unban.error
    async def unban_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("⛔ You don’t have permission to use this command.")
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("❌ Usage: `!unban <username#discriminator | user_id> [reason]`")
        elif isinstance(error, commands.BadArgument):
            await ctx.send("❌ Please provide a valid username#discriminator or user ID.")
        else:
            raise error

# ----------------------------------------------------------------------
# Setup
# ----------------------------------------------------------------------
async def setup(bot: commands.Bot):
    cog = Unban(bot)
    for cmd in cog.get_commands():
        cmd.category = "Admin"  # so help shows under Admin
    await bot.add_cog(cog)
