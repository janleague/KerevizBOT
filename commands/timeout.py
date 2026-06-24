from __future__ import annotations

from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands


MAX_TIMEOUT_DURATION = timedelta(days=28)
DEFAULT_TIMEOUT_MINUTES = 10


def build_timeout_duration(days: int, hours: int, minutes: int) -> timedelta:
    duration = timedelta(days=int(days), hours=int(hours), minutes=int(minutes))
    if duration < timedelta(0):
        raise ValueError("Timeout duration cannot be negative.")
    if duration > MAX_TIMEOUT_DURATION:
        raise ValueError("Discord timeouts cannot be longer than 28 days.")
    return duration


def format_duration(duration: timedelta) -> str:
    total_minutes = int(duration.total_seconds() // 60)
    if total_minutes <= 0:
        return "Timeout removed"

    days, rem = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    return ", ".join(parts)


def _member_avatar_url(member: discord.Member | discord.User) -> str:
    return member.display_avatar.url if hasattr(member, "display_avatar") else member.default_avatar.url


class RemoveTimeoutView(discord.ui.View):
    def __init__(self, bot: commands.Bot, target_user_id: int):
        super().__init__(timeout=900)
        self.bot = bot
        self.target_user_id = target_user_id

    @discord.ui.button(label="Remove Timeout", style=discord.ButtonStyle.success)
    async def remove_timeout(self, interaction: discord.Interaction, button: discord.ui.Button):  # noqa: ARG002
        if not interaction.guild:
            return await interaction.response.send_message(
                "This button can only be used in a server.",
                ephemeral=True,
            )
        if not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message(
                "I could not verify your server permissions.",
                ephemeral=True,
            )
        if not interaction.user.guild_permissions.moderate_members:
            return await interaction.response.send_message(
                "You need Moderate Members permission to remove timeouts.",
                ephemeral=True,
            )

        member = interaction.guild.get_member(self.target_user_id)
        if member is None:
            return await interaction.response.send_message(
                "That member is no longer in this server.",
                ephemeral=True,
            )

        issue = Timeout._moderation_block_reason(interaction.guild, interaction.user, member)
        if issue:
            return await interaction.response.send_message(issue, ephemeral=True)

        await interaction.response.defer(thinking=True)
        try:
            await member.timeout(None, reason=f"Timeout removed by {interaction.user} ({interaction.user.id})")
        except discord.Forbidden:
            return await interaction.followup.send(
                "I do not have permission to remove that timeout.",
                ephemeral=True,
            )
        except discord.HTTPException:
            return await interaction.followup.send(
                "Discord rejected the timeout removal. Please try again.",
                ephemeral=True,
            )

        embed = Timeout.build_result_embed(
            bot=self.bot,
            guild=interaction.guild,
            target=member,
            moderator=interaction.user,
            reason="Removed with the message button",
            duration=timedelta(0),
            until=None,
            dm_status="Not sent",
        )
        await interaction.followup.send(embed=embed)


class Timeout(commands.Cog):
    """Slash timeout command for moderators."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @staticmethod
    def _moderation_block_reason(
        guild: discord.Guild,
        moderator: discord.Member,
        target: discord.Member,
    ) -> str | None:
        if target.id == moderator.id:
            return "You cannot timeout yourself."
        if target.id == guild.owner_id:
            return "You cannot timeout the server owner."
        if target.guild_permissions.administrator:
            return "You cannot timeout a member with Administrator permission."
        if target.top_role >= moderator.top_role and guild.owner_id != moderator.id:
            return "You cannot timeout someone with an equal or higher role than you."

        me = guild.me
        if me and target.id == me.id:
            return "I cannot timeout myself."
        if me and target.top_role >= me.top_role:
            return "I cannot timeout that member because their role is higher or equal to mine."
        return None

    @staticmethod
    async def _send_timeout_dm(
        member: discord.Member,
        guild: discord.Guild,
        moderator: discord.Member,
        reason: str,
        duration: timedelta,
        until,
    ) -> bool:
        if duration <= timedelta(0):
            title = "Your timeout has been removed"
            description = f"Your timeout in **{guild.name}** has been removed."
            color = discord.Color.green()
        else:
            title = "You have been timed out"
            description = f"You have been timed out in **{guild.name}**."
            color = discord.Color.orange()

        embed = discord.Embed(
            title=title,
            description=description,
            color=color,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Moderator", value=str(moderator), inline=True)
        embed.add_field(name="Duration", value=format_duration(duration), inline=True)
        if until:
            embed.add_field(name="Ends", value=discord.utils.format_dt(until, style="F"), inline=False)
        embed.add_field(name="Reason", value=reason[:1024], inline=False)
        embed.set_footer(text="Kereviz Bot Moderation")

        try:
            await member.send(embed=embed)
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    @staticmethod
    def build_result_embed(
        *,
        bot: commands.Bot,
        guild: discord.Guild,
        target: discord.Member,
        moderator: discord.Member | discord.User,
        reason: str,
        duration: timedelta,
        until,
        dm_status: str,
    ) -> discord.Embed:
        is_removal = duration <= timedelta(0)
        embed = discord.Embed(
            title="Timeout Removed" if is_removal else "Member Timed Out",
            description=f"Moderation action completed in **{guild.name}**.",
            color=discord.Color.green() if is_removal else discord.Color.orange(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_thumbnail(url=_member_avatar_url(target))
        embed.add_field(name="Member", value=f"{target.mention}\n`{target}`\nID: `{target.id}`", inline=True)
        embed.add_field(name="Moderator", value=f"{moderator.mention}\n`{moderator}`", inline=True)
        embed.add_field(name="Duration", value=format_duration(duration), inline=False)
        if until:
            embed.add_field(
                name="Ends",
                value=f"{discord.utils.format_dt(until, style='F')}\n{discord.utils.format_dt(until, style='R')}",
                inline=False,
            )
        embed.add_field(name="Reason", value=reason[:1024], inline=False)
        embed.add_field(name="DM Status", value=dm_status, inline=True)
        embed.set_footer(text=f"{bot.user.name if bot.user else 'Kereviz Bot'} Moderation")
        return embed

    @app_commands.command(name="timeout", description="Set or remove a member timeout with a detailed moderation embed.")
    @app_commands.describe(
        member="Member to timeout or clear.",
        reason="Reason for the moderation log.",
        days="Timeout days. Use 0 with hours/minutes 0 to remove.",
        hours="Timeout hours. Use 0 with days/minutes 0 to remove.",
        minutes="Timeout minutes. Default is 10.",
        notify_member="Try to DM the member before applying the action.",
        private="Only show the result to moderators.",
    )
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.checks.bot_has_permissions(moderate_members=True)
    async def timeout(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str = "No reason provided",
        days: app_commands.Range[int, 0, 28] = 0,
        hours: app_commands.Range[int, 0, 23] = 0,
        minutes: app_commands.Range[int, 0, 59] = DEFAULT_TIMEOUT_MINUTES,
        notify_member: bool = True,
        private: bool = False,
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

        try:
            duration = build_timeout_duration(days, hours, minutes)
        except ValueError as exc:
            return await interaction.response.send_message(str(exc), ephemeral=True)

        issue = self._moderation_block_reason(interaction.guild, interaction.user, member)
        if issue:
            return await interaction.response.send_message(issue, ephemeral=True)

        until = None if duration <= timedelta(0) else discord.utils.utcnow() + duration

        await interaction.response.defer(ephemeral=private, thinking=True)

        dm_status = "Skipped"
        if notify_member:
            sent = await self._send_timeout_dm(
                member,
                interaction.guild,
                interaction.user,
                reason,
                duration,
                until,
            )
            dm_status = "Sent" if sent else "Closed or unavailable"

        audit_reason = f"{reason} | Moderator: {interaction.user} ({interaction.user.id})"[:512]
        try:
            await member.timeout(until, reason=audit_reason)
        except discord.Forbidden:
            return await interaction.followup.send(
                "I do not have permission to timeout that member.",
                ephemeral=True,
            )
        except discord.HTTPException:
            return await interaction.followup.send(
                "Discord rejected the timeout action. Check role hierarchy and try again.",
                ephemeral=True,
            )

        embed = self.build_result_embed(
            bot=self.bot,
            guild=interaction.guild,
            target=member,
            moderator=interaction.user,
            reason=reason,
            duration=duration,
            until=until,
            dm_status=dm_status,
        )
        view = None if duration <= timedelta(0) else RemoveTimeoutView(self.bot, member.id)
        await interaction.followup.send(embed=embed, view=view)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            message = "You need Moderate Members permission to use `/timeout`."
        elif isinstance(error, app_commands.BotMissingPermissions):
            message = "I need Moderate Members permission to use `/timeout`."
        elif isinstance(error, app_commands.NoPrivateMessage):
            message = "`/timeout` can only be used in a server."
        else:
            message = "An unexpected `/timeout` error occurred. Please try again."
            print(f"[TIMEOUT COMMAND ERROR] {error!r}")

        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Timeout(bot))
