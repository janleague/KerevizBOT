import discord
from discord.ext import commands
from datetime import datetime


class Moderation(commands.Cog):
    """Moderation tools (ban). Only members with `ban_members` permission can invoke."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---------------------------------------------------------------------
    # !ban @user|user_id [reason]
    # ---------------------------------------------------------------------
    @commands.command(name="ban", help="Ban a member by mention or user ID.")
    @commands.has_permissions(ban_members=True)
    async def ban(self, ctx: commands.Context, target: str, *, reason: str = "No reason provided"):
        guild = ctx.guild

        # 1. Resolve target (mention / ID)
        member_obj: discord.Member | None = None
        user_obj: discord.User | None = None

        try:
            # a) Try to convert mention / username
            member_obj = await commands.MemberConverter().convert(ctx, target)
        except commands.BadArgument:
            # b) Maybe it's a raw user ID
            try:
                user_id = int(target)
                member_obj = guild.get_member(user_id)
                if not member_obj:
                    user_obj = await self.bot.fetch_user(user_id)
            except (ValueError, discord.NotFound):
                return await ctx.send("❌ Please provide a valid member mention or user ID.")

        victim = member_obj or user_obj
        if not victim:
            return await ctx.send("❌ Could not find that user.")

        # 2. Permission hierarchy check
        if member_obj:
            if member_obj == ctx.author:
                return await ctx.send("❌ You cannot ban yourself.")
            if member_obj.top_role >= ctx.author.top_role:
                return await ctx.send("❌ You cannot ban someone with an equal or higher role.")

        # 3. Attempt ban
        try:
            await guild.ban(victim, reason=reason, delete_message_days=0)
        except discord.Forbidden:
            return await ctx.send("⛔ I don’t have permission to ban that user.")

        # 4. Confirmation embed
        embed = discord.Embed(
            title="🔨 Member Banned",
            description=(
                f"**User:** {victim.mention if member_obj else victim} (`{victim}`)\n"
                f"**Moderator:** {ctx.author.mention}\n"
                f"**Reason:** {reason}"
            ),
            color=discord.Color.red(),
            timestamp=datetime.utcnow(),
        )
        avatar_url = victim.avatar.url if isinstance(victim, discord.User) and victim.avatar else None
        if avatar_url:
            embed.set_thumbnail(url=avatar_url)
        embed.set_footer(text="Kereviz Bot • Ban Hammer")
        await ctx.send(embed=embed)

    # ---------------------------------------------------------------------
    # Error handler
    # ---------------------------------------------------------------------
    @ban.error
    async def ban_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("⛔ You don’t have permission to use this command.")
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("❌ Usage: `!ban <@user|user_id> [reason]`")
        elif isinstance(error, commands.BadArgument):
            await ctx.send("❌ Please mention a valid member or provide a valid user ID.")
        else:
            raise error  # Delegate to global error handler if any


async def setup(bot: commands.Bot):
    cog = Moderation(bot)
    # Show under Admin category in custom help
    for cmd in cog.get_commands():
        cmd.category = "Admin"
    await bot.add_cog(cog)
