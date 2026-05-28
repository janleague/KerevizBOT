import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime


class UnbanView(discord.ui.View):
    def __init__(self, target_user_id: int):
        super().__init__(timeout=None)
        self.target_user_id = target_user_id

    @discord.ui.button(label="Unban", style=discord.ButtonStyle.success, emoji="🔓")
    async def unban_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            return await interaction.response.send_message("❌ This can only be used in a server.", ephemeral=True)

        # Permission check (button clicker must have ban permissions)
        if not interaction.user.guild_permissions.ban_members:
            return await interaction.response.send_message("⛔ You don’t have permission to unban members.", ephemeral=True)

        guild = interaction.guild

        # Try to unban
        try:
            user = await interaction.client.fetch_user(self.target_user_id)
        except discord.NotFound:
            return await interaction.response.send_message("❌ I couldn’t find that user anymore.", ephemeral=True)

        try:
            await guild.unban(user, reason=f"Unbanned by {interaction.user} via button")
        except discord.Forbidden:
            return await interaction.response.send_message("⛔ I don’t have permission to unban that user.", ephemeral=True)
        except discord.HTTPException:
            return await interaction.response.send_message("❌ Something went wrong while unbanning.", ephemeral=True)

        # Edit the original message to show it's unbanned and remove the button
        new_embed = discord.Embed(
            title="Member Unbanned",
            description=(
                f"**User:** {user.mention} (`{user}`)\n"
                f"**Moderator:** {interaction.user.mention}\n"
                f"**Reason:** Unbanned via button"
            ),
            color=discord.Color.green()
        )
        now_str = datetime.now().strftime("%d.%m.%Y %H:%M")
        bot_name = interaction.client.user.name if interaction.client.user else "Bot"
        new_embed.set_footer(text=f"{bot_name} • Ban Hammer • {now_str}")

        await interaction.response.edit_message(embed=new_embed, view=None)


class Moderation(commands.Cog):
    """Moderation tools (ban) - Slash version."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _send_ban_dm(self, user: discord.User | discord.Member, guild: discord.Guild, reason: str) -> bool:
        embed = discord.Embed(
            title="You have been banned",
            description=(
                f"You have been banned from **{guild.name}**.\n\n"
                f"Reason: {reason}"
            ),
            color=discord.Color.red(),
        )
        embed.set_footer(text="This message was sent before the ban was applied.")

        try:
            await user.send(embed=embed)
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    @app_commands.command(name="ban", description="Ban a member (optionally delete recent messages).")
    @app_commands.describe(
        member="The member to ban",
        reason="Reason for the ban",
        delete_message_days="Delete the member's messages from the last N days (0-7)"
    )
    @app_commands.checks.has_permissions(ban_members=True)
    async def ban(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str = "No reason provided",
        delete_message_days: app_commands.Range[int, 0, 7] = 0
    ):
        if not interaction.guild:
            return await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)

        guild = interaction.guild

        # Safety / hierarchy checks
        if member.id == interaction.user.id:
            return await interaction.response.send_message("❌ You cannot ban yourself.", ephemeral=True)

        # Can't ban someone equal/higher than moderator (role hierarchy)
        if member.top_role >= interaction.user.top_role and guild.owner_id != interaction.user.id:
            return await interaction.response.send_message(
                "❌ You cannot ban someone with an equal or higher role than you.",
                ephemeral=True
            )

        # Bot role hierarchy check
        me = guild.me  # type: ignore
        if me and member.top_role >= me.top_role:
            return await interaction.response.send_message(
                "❌ I cannot ban that member because their role is higher or equal to mine.",
                ephemeral=True
            )

        # Discord API limitation: ban deletion max 7 days (we enforce 0-7 already)
        delete_seconds = int(delete_message_days) * 86400

        await self._send_ban_dm(member, guild, reason)

        # Execute ban
        try:
            await guild.ban(
                member,
                reason=reason,
                delete_message_seconds=delete_seconds
            )
        except discord.Forbidden:
            return await interaction.response.send_message("⛔ I don’t have permission to ban that user.", ephemeral=True)
        except discord.HTTPException:
            return await interaction.response.send_message("❌ Something went wrong while banning.", ephemeral=True)

        # Build embed (close to your screenshot layout, English)
        embed = discord.Embed(
            title="Member Banned",
            description=(
                f"**User:** {member.mention} (`{member}`)\n"
                f"**Moderator:** {interaction.user.mention}\n"
                f"**Reason:** {reason}"
            ),
            color=discord.Color.red()
        )

        embed.add_field(
            name="Deleted Messages",
            value=f"Last {delete_message_days} day(s)",
            inline=False,
        )



        now_str = datetime.now().strftime("%d.%m.%Y %H:%M")
        bot_name = self.bot.user.name if self.bot.user else "Bot"
        embed.set_footer(text=f"{bot_name} • Ban Hammer • {now_str}")

        view = UnbanView(target_user_id=member.id)

        # Send
        await interaction.response.send_message(embed=embed, view=view)

    # ==================== TEXT COMMAND (!ban) ====================
    @commands.command(name="ban", help="Ban a member by mention or user ID.")
    @commands.has_permissions(ban_members=True)
    @commands.guild_only()
    async def text_ban(self, ctx: commands.Context, target: str = None, *, reason: str = "No reason provided"):
        if not target:
            return await ctx.send("❌ Usage: `!ban <@member or user_id> [reason]`")

        guild = ctx.guild
        member: discord.Member | None = None

        # Try to resolve as a member (handles @mention and IDs of members in server)
        try:
            member = await commands.MemberConverter().convert(ctx, target)
        except commands.MemberNotFound:
            # If not found as member, try raw ID to ban someone not in the server
            try:
                user_id = int(target)
                user = None
                try:
                    user = await self.bot.fetch_user(user_id)
                    await self._send_ban_dm(user, guild, reason)
                except Exception:
                    user = None

                # Ban by Object (allows banning users who already left the server)
                try:
                    await guild.ban(discord.Object(id=user_id), reason=reason, delete_message_seconds=0)
                except discord.NotFound:
                    return await ctx.send("❌ Could not find a user with that ID.")
                except discord.Forbidden:
                    return await ctx.send("⛔ I don't have permission to ban that user.")

                # Try to fetch user info for the embed
                if user:
                    display = f"{user.mention} (`{user}`)"
                else:
                    display = f"`{user_id}`"

                embed = discord.Embed(
                    title="🔨 Member Banned",
                    description=(
                        f"**User:** {display}\n"
                        f"**Moderator:** {ctx.author.mention}\n"
                        f"**Reason:** {reason}"
                    ),
                    color=discord.Color.red(),
                )
                now_str = datetime.now().strftime("%d.%m.%Y %H:%M")
                bot_name = self.bot.user.name if self.bot.user else "Bot"
                embed.set_footer(text=f"{bot_name} • Ban Hammer • {now_str}")
                return await ctx.send(embed=embed)

            except ValueError:
                return await ctx.send("❌ Please provide a valid @mention or user ID.")

        # --- Member found in server: safety checks ---
        if member.id == ctx.author.id:
            return await ctx.send("❌ You cannot ban yourself.")

        if member.top_role >= ctx.author.top_role and guild.owner_id != ctx.author.id:
            return await ctx.send("❌ You cannot ban someone with an equal or higher role than you.")

        me = guild.me
        if me and member.top_role >= me.top_role:
            return await ctx.send("❌ I cannot ban that member because their role is higher or equal to mine.")

        await self._send_ban_dm(member, guild, reason)

        try:
            await guild.ban(member, reason=reason, delete_message_seconds=0)
        except discord.Forbidden:
            return await ctx.send("⛔ I don't have permission to ban that user.")
        except discord.HTTPException:
            return await ctx.send("❌ Something went wrong while banning.")

        embed = discord.Embed(
            title="🔨 Member Banned",
            description=(
                f"**User:** {member.mention} (`{member}`)\n"
                f"**Moderator:** {ctx.author.mention}\n"
                f"**Reason:** {reason}"
            ),
            color=discord.Color.red(),
        )
        now_str = datetime.now().strftime("%d.%m.%Y %H:%M")
        bot_name = self.bot.user.name if self.bot.user else "Bot"
        embed.set_footer(text=f"{bot_name} • Ban Hammer • {now_str}")

        view = UnbanView(target_user_id=member.id)
        await ctx.send(embed=embed, view=view)

    @text_ban.error
    async def text_ban_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("⛔ You don't have permission to use this command.")
        elif isinstance(error, commands.NoPrivateMessage):
            await ctx.send("❌ This command can only be used in a server.")
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("❌ Usage: `!ban <@member or user_id> [reason]`")
        else:
            await ctx.send("❌ An unexpected error occurred.")

    @ban.error
    async def ban_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            return await interaction.response.send_message("⛔ You don’t have permission to use this command.", ephemeral=True)

        # Fallback
        try:
            await interaction.response.send_message("❌ An unexpected error occurred.", ephemeral=True)
        except discord.InteractionResponded:
            await interaction.followup.send("❌ An unexpected error occurred.", ephemeral=True)


async def setup(bot: commands.Bot):
    cog = Moderation(bot)
    for cmd in cog.get_commands():
        cmd.category = "Admin"
    await bot.add_cog(cog)
