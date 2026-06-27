from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands


TEXT_CLEAR_LIMIT = 100
SLASH_CLEAR_LIMIT = 1000


def format_clear_result(deleted_count: int, requested_count: int) -> str:
    if deleted_count == requested_count:
        return f"Deleted `{deleted_count}` message(s)."
    return f"Deleted `{deleted_count}` message(s). Requested `{requested_count}`."


def build_audit_reason(action: str, moderator: discord.abc.User, reason: str) -> str:
    clean_reason = (reason or "No reason provided").strip() or "No reason provided"
    return f"{action} by {moderator} ({moderator.id}): {clean_reason}"[:512]


def is_nukeable_channel(channel: object) -> bool:
    return isinstance(channel, discord.TextChannel)


class NukeConfirmView(discord.ui.View):
    def __init__(self, cog: Clear, channel_id: int, requester_id: int, reason: str):
        super().__init__(timeout=30)
        self.cog = cog
        self.channel_id = channel_id
        self.requester_id = requester_id
        self.reason = reason

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.requester_id:
            return True
        await interaction.response.send_message(
            "Only the moderator who started `/nuke` can confirm this action.",
            ephemeral=True,
        )
        return False

    @discord.ui.button(label="Confirm Nuke", style=discord.ButtonStyle.danger)
    async def confirm_nuke(self, interaction: discord.Interaction, button: discord.ui.Button):  # noqa: ARG002
        if not interaction.guild:
            return await interaction.response.send_message("This can only be used in a server.", ephemeral=True)

        channel = interaction.guild.get_channel(self.channel_id)
        if not is_nukeable_channel(channel):
            return await interaction.response.send_message(
                "This channel is no longer available for nuking.",
                ephemeral=True,
            )
        if not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message(
                "I could not verify your server permissions.",
                ephemeral=True,
            )
        if not interaction.user.guild_permissions.manage_channels:
            return await interaction.response.send_message(
                "You need Manage Channels permission to confirm this nuke.",
                ephemeral=True,
            )

        await interaction.response.edit_message(
            embed=discord.Embed(
                title="Nuke Started",
                description="Cloning the channel, deleting the old one, and restoring its position.",
                color=discord.Color.orange(),
            ),
            view=None,
        )

        new_channel = await self.cog.nuke_channel(channel, interaction.user, self.reason)
        if new_channel is None:
            return await interaction.followup.send(
                "I could not nuke this channel. Check my Manage Channels permission and role position.",
                ephemeral=True,
            )

        await interaction.followup.send(f"Channel nuked successfully: {new_channel.mention}", ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_nuke(self, interaction: discord.Interaction, button: discord.ui.Button):  # noqa: ARG002
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="Nuke Cancelled",
                description="No channel was deleted.",
                color=discord.Color.green(),
            ),
            view=None,
        )


class Clear(commands.Cog):
    """Message and channel cleanup commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @staticmethod
    async def _purge_channel_messages(channel, amount: int) -> int:
        purge = getattr(channel, "purge", None)
        if purge is None:
            raise TypeError("This channel does not support message purging.")
        deleted = await purge(limit=amount)
        return len(deleted)

    def _nuke_prompt_embed(self, channel: discord.TextChannel, moderator: discord.Member, reason: str) -> discord.Embed:
        embed = discord.Embed(
            title="Confirm Channel Nuke",
            description=(
                f"This will delete {channel.mention} and recreate it with the same settings.\n"
                "All messages in the current channel will be removed."
            ),
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Channel", value=f"{channel.name} (`{channel.id}`)", inline=False)
        embed.add_field(name="Position", value=str(channel.position), inline=True)
        embed.add_field(name="Category", value=channel.category.name if channel.category else "None", inline=True)
        embed.add_field(name="Moderator", value=moderator.mention, inline=True)
        embed.add_field(name="Reason", value=reason[:1024], inline=False)
        embed.set_footer(text="This confirmation expires in 30 seconds.")
        return embed

    def _nuke_done_embed(self, moderator: discord.Member, reason: str) -> discord.Embed:
        embed = discord.Embed(
            title="Channel Nuked",
            description="This channel has been recreated with the previous channel settings.",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Moderator", value=moderator.mention, inline=True)
        embed.add_field(name="Reason", value=reason[:1024], inline=False)
        embed.set_footer(text=f"{self.bot.user.name if self.bot.user else 'Kereviz Bot'} Moderation")
        return embed

    async def nuke_channel(
        self,
        channel: discord.TextChannel,
        moderator: discord.Member,
        reason: str,
    ) -> discord.TextChannel | None:
        old_position = channel.position
        audit_reason = build_audit_reason("Channel nuke", moderator, reason)
        new_channel: discord.TextChannel | None = None

        try:
            new_channel = await channel.clone(reason=audit_reason)
        except (discord.Forbidden, discord.HTTPException):
            return None

        try:
            await channel.delete(reason=audit_reason)
        except (discord.Forbidden, discord.HTTPException):
            try:
                await new_channel.delete(reason="Cleaning up failed nuke clone")
            except (discord.Forbidden, discord.HTTPException):
                pass
            return None

        try:
            await new_channel.edit(position=old_position, reason=audit_reason)
        except (discord.Forbidden, discord.HTTPException):
            pass

        try:
            await new_channel.send(
                embed=self._nuke_done_embed(moderator, reason),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException):
            pass
        return new_channel

    @commands.command(name="clear", aliases=["purge"], help="Delete a number of recent messages.")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    @commands.bot_has_permissions(manage_messages=True, read_message_history=True)
    async def clear(self, ctx: commands.Context, amount: int):
        if amount < 1:
            return await ctx.send("Usage: `!clear <message count>`", delete_after=4)
        if amount > TEXT_CLEAR_LIMIT:
            return await ctx.send(f"Please choose `{TEXT_CLEAR_LIMIT}` messages or fewer.", delete_after=4)

        deleted = await ctx.channel.purge(limit=amount + 1)
        cleaned = max(0, len(deleted) - 1)
        await ctx.send(format_clear_result(cleaned, amount), delete_after=3)

    @app_commands.command(name="clear", description="Delete a chosen number of recent messages from this channel.")
    @app_commands.describe(amount="How many recent messages to delete.")
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.checks.bot_has_permissions(manage_messages=True, read_message_history=True)
    async def slash_clear(
        self,
        interaction: discord.Interaction,
        amount: app_commands.Range[int, 1, SLASH_CLEAR_LIMIT],
    ):
        if not interaction.guild:
            return await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )

        channel = interaction.channel
        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            deleted_count = await self._purge_channel_messages(channel, int(amount))
        except TypeError:
            return await interaction.followup.send(
                "This channel does not support message cleanup.",
                ephemeral=True,
            )
        except discord.Forbidden:
            return await interaction.followup.send(
                "I do not have permission to delete messages here.",
                ephemeral=True,
            )
        except discord.HTTPException:
            return await interaction.followup.send(
                "Discord rejected the message cleanup request. Try a smaller amount.",
                ephemeral=True,
            )

        await interaction.followup.send(format_clear_result(deleted_count, int(amount)), ephemeral=True)

    @app_commands.command(name="nuke", description="Delete and recreate this text channel with the same settings.")
    @app_commands.describe(reason="Reason shown in audit logs and the recreated channel notice.")
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.checks.has_permissions(manage_channels=True)
    @app_commands.checks.bot_has_permissions(manage_channels=True)
    async def nuke(
        self,
        interaction: discord.Interaction,
        reason: str = "Channel cleanup requested",
    ):
        if not interaction.guild:
            return await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
        if not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message(
                "I could not verify your server permissions.",
                ephemeral=True,
            )
        if not is_nukeable_channel(interaction.channel):
            return await interaction.response.send_message(
                "`/nuke` can only be used in a normal text channel.",
                ephemeral=True,
            )

        view = NukeConfirmView(self, interaction.channel.id, interaction.user.id, reason)
        await interaction.response.send_message(
            embed=self._nuke_prompt_embed(interaction.channel, interaction.user, reason),
            view=view,
            ephemeral=True,
        )

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

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            message = "You do not have permission to use this moderation command."
        elif isinstance(error, app_commands.BotMissingPermissions):
            message = "I am missing the required permissions for this moderation command."
        elif isinstance(error, app_commands.NoPrivateMessage):
            message = "This command can only be used in a server."
        else:
            message = "An unexpected moderation command error occurred."
            print(f"[CLEAR/NUKE COMMAND ERROR] {error!r}")

        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot):
    cog = Clear(bot)
    await bot.add_cog(cog)
    command = bot.get_command("clear")
    if command:
        command.category = "Admin"
