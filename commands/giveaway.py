import asyncio
import json
import os
import random
import re
import secrets
import time
from datetime import datetime, timezone
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from services.giveaway_store import GiveawayStore, normalize_record


DATA_FILE = "giveaways.json"
CHECK_INTERVAL = 20
MIN_DURATION_SECONDS = 60
MAX_DURATION_SECONDS = 90 * 24 * 60 * 60
DEFAULT_COLOR = discord.Color.green().value
DURATION_RE = re.compile(r"(\d+)\s*([smhdw])", re.IGNORECASE)
PARTICIPANT_PREVIEW_LIMIT = 50
ACTIVE_PARTICIPANTS_CUSTOM_ID = "kereviz_giveaway_participants_active"
ENDED_PARTICIPANTS_CUSTOM_ID = "kereviz_giveaway_participants_ended"

COLOR_NAMES = {
    "green": discord.Color.green().value,
    "blue": discord.Color.blue().value,
    "red": discord.Color.red().value,
    "gold": discord.Color.gold().value,
    "purple": discord.Color.purple().value,
    "orange": discord.Color.orange().value,
    "teal": discord.Color.teal().value,
    "dark": discord.Color.dark_grey().value,
}


def now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def parse_duration(value: str) -> int:
    raw = value.strip().lower()
    if not raw:
        raise ValueError("Duration is required.")

    if raw.isdigit():
        total = int(raw) * 60
    else:
        total = 0
        consumed = ""
        multipliers = {
            "s": 1,
            "m": 60,
            "h": 60 * 60,
            "d": 24 * 60 * 60,
            "w": 7 * 24 * 60 * 60,
        }
        for amount, unit in DURATION_RE.findall(raw):
            total += int(amount) * multipliers[unit.lower()]
            consumed += f"{amount}{unit}"
        compact = re.sub(r"\s+", "", raw)
        if not total or consumed.lower() != compact:
            raise ValueError("Use a duration like 10m, 2h, 1d, or 1w2d.")

    if total < MIN_DURATION_SECONDS:
        raise ValueError("Duration must be at least 1 minute.")
    if total > MAX_DURATION_SECONDS:
        raise ValueError("Duration cannot be longer than 90 days.")
    return total


def format_duration(seconds: int) -> str:
    parts: list[str] = []
    for suffix, size in (("w", 604800), ("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        if seconds >= size:
            amount, seconds = divmod(seconds, size)
            parts.append(f"{amount}{suffix}")
    return " ".join(parts) if parts else "0s"


def parse_color(value: str | None) -> int:
    if not value:
        return DEFAULT_COLOR
    raw = value.strip().lower()
    if raw in COLOR_NAMES:
        return COLOR_NAMES[raw]
    raw = raw.removeprefix("#").removeprefix("0x")
    if len(raw) == 6:
        try:
            return int(raw, 16)
        except ValueError:
            pass
    raise ValueError("Use a color name like green/gold/blue or a hex color like #57F287.")


def valid_url(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if value.startswith("http://") or value.startswith("https://"):
        return value
    raise ValueError("Image URLs must start with http:// or https://.")


def normalize_entrant_ids(raw_entrants: list[Any] | tuple[Any, ...] | None) -> list[int]:
    entrant_ids: list[int] = []
    seen: set[int] = set()
    for entry in raw_entrants or []:
        try:
            user_id = int(entry)
        except (TypeError, ValueError):
            continue
        if user_id in seen:
            continue
        entrant_ids.append(user_id)
        seen.add(user_id)
    return entrant_ids


def format_participants_preview(raw_entrants: list[Any] | tuple[Any, ...] | None) -> tuple[str, int, bool]:
    entrant_ids = normalize_entrant_ids(raw_entrants)
    total = len(entrant_ids)
    if not entrant_ids:
        return "No participants yet.", 0, False

    preview = entrant_ids[:PARTICIPANT_PREVIEW_LIMIT]
    lines = [f"`{index}.` <@{user_id}> (`{user_id}`)" for index, user_id in enumerate(preview, start=1)]
    truncated = total > len(preview)
    if truncated:
        lines.append(f"...and `{total - len(preview)}` more participant(s).")
    return "\n".join(lines), total, truncated


class GiveawayJoinView(discord.ui.View):
    def __init__(self, cog: "Giveaway"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Enter Giveaway", style=discord.ButtonStyle.success, custom_id="kereviz_giveaway_enter")
    async def enter(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_entry(interaction)

    @discord.ui.button(label="Participants", style=discord.ButtonStyle.secondary, custom_id=ACTIVE_PARTICIPANTS_CUSTOM_ID)
    async def participants(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_participants(interaction)


class GiveawayEndedView(discord.ui.View):
    def __init__(self, cog: "Giveaway"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Giveaway Ended", style=discord.ButtonStyle.secondary, custom_id="kereviz_giveaway_closed", disabled=True)
    async def closed(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass

    @discord.ui.button(label="Participants", style=discord.ButtonStyle.secondary, custom_id=ENDED_PARTICIPANTS_CUSTOM_ID)
    async def participants(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_participants(interaction)


class Giveaway(commands.Cog):
    """Persistent, button-based giveaway system."""

    giveaway = app_commands.Group(name="giveaway", description="Create and manage giveaways.")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._lock = asyncio.Lock()
        self.store = GiveawayStore()
        self._giveaways: dict[str, dict[str, Any]] = {}
        self._runner: asyncio.Task | None = None

    async def initialize(self) -> None:
        self._giveaways = await self.store.load_all()
        await self._migrate_legacy_file()

    def start_runner(self) -> None:
        if self._runner is None or self._runner.done():
            self._runner = asyncio.create_task(self._run_due_giveaways())

    def cog_unload(self) -> None:
        if self._runner and not self._runner.done():
            self._runner.cancel()

    @commands.command(name="giveaway", aliases=["gw"], help="Show the giveaway command guide.")
    @commands.guild_only()
    async def giveaway_help(self, ctx: commands.Context):
        embed = discord.Embed(
            title="Giveaway Commands",
            description="Use slash commands for the full giveaway system.",
            color=discord.Color.green(),
        )
        embed.add_field(name="/giveaway create", value="Create a customizable giveaway.", inline=False)
        embed.add_field(name="/giveaway end", value="End an active giveaway early.", inline=False)
        embed.add_field(name="/giveaway reroll", value="Pick new winner(s) from an ended giveaway.", inline=False)
        embed.add_field(name="/giveaway cancel", value="Cancel an active giveaway without winners.", inline=False)
        embed.add_field(name="/giveaway delete", value="Permanently remove an ended or cancelled giveaway from storage.", inline=False)
        embed.add_field(name="/giveaway list", value="List giveaways in this server.", inline=False)
        embed.set_footer(text="Durations support 10m, 2h, 1d, 1w2d, and similar formats.")
        await ctx.send(embed=embed)

    @giveaway.command(name="create", description="Create a customizable giveaway.")
    @app_commands.describe(
        duration="How long the giveaway should run. Examples: 10m, 2h, 1d, 1w2d",
        prize="The prize people are entering for.",
        winners="How many winners to draw.",
        channel="Where the giveaway should be posted. Defaults to this channel.",
        description="Optional extra details shown in the giveaway embed.",
        required_role="Only members with this role can enter.",
        bonus_role="Members with this role get bonus entry weight.",
        bonus_entries="Total entry weight for bonus-role members. 2 means double chance.",
        image_url="Optional large image URL for the embed.",
        thumbnail_url="Optional thumbnail URL for the embed.",
        color="Embed color name or hex value. Examples: green, gold, #57F287",
        ping_role="Optional role to ping when the giveaway starts.",
        ping_everyone="Ping everyone when the giveaway starts.",
        host="Who should be displayed as the giveaway host. Defaults to you.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def create(
        self,
        interaction: discord.Interaction,
        duration: str,
        prize: str,
        winners: app_commands.Range[int, 1, 25] = 1,
        channel: discord.TextChannel | None = None,
        description: str | None = None,
        required_role: discord.Role | None = None,
        bonus_role: discord.Role | None = None,
        bonus_entries: app_commands.Range[int, 2, 25] = 2,
        image_url: str | None = None,
        thumbnail_url: str | None = None,
        color: str | None = None,
        ping_role: discord.Role | None = None,
        ping_everyone: bool = False,
        host: discord.Member | None = None,
    ):
        if not interaction.guild:
            return await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        try:
            seconds = parse_duration(duration)
            embed_color = parse_color(color)
            image_url = valid_url(image_url)
            thumbnail_url = valid_url(thumbnail_url)
        except ValueError as exc:
            return await interaction.followup.send(str(exc), ephemeral=True)

        target_channel = channel or interaction.channel
        if not isinstance(target_channel, discord.TextChannel):
            return await interaction.followup.send("Please choose a normal text channel.", ephemeral=True)

        me = interaction.guild.me
        if me is None:
            return await interaction.followup.send("I could not verify my server permissions.", ephemeral=True)

        perms = target_channel.permissions_for(me)
        if not perms.send_messages or not perms.embed_links or not perms.read_message_history:
            return await interaction.followup.send(
                "I need Send Messages, Embed Links, and Read Message History in that channel.",
                ephemeral=True,
            )

        if ping_everyone and not perms.mention_everyone:
            return await interaction.followup.send("I need Mention Everyone permission to ping everyone.", ephemeral=True)

        if ping_role and not ping_role.mentionable and not perms.mention_everyone:
            return await interaction.followup.send(
                "That role is not mentionable, and I do not have permission to mention all roles.",
                ephemeral=True,
            )

        if len(prize) > 256:
            return await interaction.followup.send("Prize must be 256 characters or fewer.", ephemeral=True)
        if description and len(description) > 1800:
            return await interaction.followup.send("Description must be 1800 characters or fewer.", ephemeral=True)

        giveaway_id = self._new_id()
        host_member = host or interaction.user
        ends_at = now_ts() + seconds
        record = {
            "id": giveaway_id,
            "guild_id": interaction.guild.id,
            "channel_id": target_channel.id,
            "message_id": None,
            "host_id": host_member.id,
            "created_by_id": interaction.user.id,
            "created_at": now_ts(),
            "ends_at": ends_at,
            "ended_at": None,
            "status": "active",
            "prize": prize,
            "description": description,
            "winners_count": int(winners),
            "winner_ids": [],
            "winner_announcement_sent": False,
            "entrants": [],
            "required_role_id": required_role.id if required_role else None,
            "bonus_role_id": bonus_role.id if bonus_role else None,
            "bonus_entries": int(bonus_entries),
            "image_url": image_url,
            "thumbnail_url": thumbnail_url,
            "color": embed_color,
            "ping_role_id": ping_role.id if ping_role else None,
            "ping_everyone": bool(ping_everyone),
        }

        content, allowed_mentions = self._announcement_content(record, ping_role)
        try:
            message = await target_channel.send(
                content=content,
                embed=self._build_embed(record),
                view=GiveawayJoinView(self),
                allowed_mentions=allowed_mentions,
            )
        except discord.Forbidden:
            return await interaction.followup.send("I do not have permission to post in that channel.", ephemeral=True)
        except discord.HTTPException:
            return await interaction.followup.send("Discord rejected the giveaway message. Please try again.", ephemeral=True)

        record["message_id"] = message.id
        async with self._lock:
            self._giveaways[giveaway_id] = record
            await self._save_giveaway(record)

        await interaction.followup.send(
            f"Giveaway created in {target_channel.mention}: {message.jump_url}",
            ephemeral=True,
        )

    @giveaway.command(name="end", description="End an active giveaway early and draw winners.")
    @app_commands.describe(identifier="Giveaway message ID or giveaway ID.", winners="Optional winner count override.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def end(self, interaction: discord.Interaction, identifier: str, winners: int | None = None):
        if not interaction.guild:
            return await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        if winners is not None and not 1 <= winners <= 25:
            return await interaction.response.send_message("Winner count must be between 1 and 25.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        record = self._find_giveaway(identifier, interaction.guild.id)
        if not record:
            return await interaction.followup.send("I could not find that giveaway.", ephemeral=True)
        if record["status"] != "active":
            return await interaction.followup.send("That giveaway is not active.", ephemeral=True)

        final_record = await self._finish_giveaway(record["id"], winner_count=winners, ended_by=interaction.user.id)
        if not final_record:
            return await interaction.followup.send("That giveaway could not be ended.", ephemeral=True)
        await interaction.followup.send("Giveaway ended and winners were processed.", ephemeral=True)

    @giveaway.command(name="reroll", description="Reroll winner(s) for an ended giveaway.")
    @app_commands.describe(
        identifier="Giveaway message ID or giveaway ID.",
        winners="How many winners to reroll. Defaults to the giveaway winner count.",
        include_previous_winners="Allow previous winners to be picked again.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def reroll(
        self,
        interaction: discord.Interaction,
        identifier: str,
        winners: int | None = None,
        include_previous_winners: bool = False,
    ):
        if not interaction.guild:
            return await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        if winners is not None and not 1 <= winners <= 25:
            return await interaction.response.send_message("Winner count must be between 1 and 25.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        record = self._find_giveaway(identifier, interaction.guild.id)
        if not record:
            return await interaction.followup.send("I could not find that giveaway.", ephemeral=True)
        if record["status"] not in {"ended", "ending"}:
            return await interaction.followup.send("Only ended giveaways can be rerolled.", ephemeral=True)

        exclude = set() if include_previous_winners else set(record.get("winner_ids", []))
        new_winners = await self._draw_winners(record, winner_count=winners, exclude=exclude)
        if not new_winners:
            return await interaction.followup.send("No eligible entries were available for a reroll.", ephemeral=True)

        async with self._lock:
            stored = self._giveaways.get(record["id"])
            if not stored:
                return await interaction.followup.send("I could not find that giveaway anymore.", ephemeral=True)
            stored["winner_ids"] = new_winners
            stored["winner_announcement_sent"] = False
            stored["rerolled_at"] = now_ts()
            stored["rerolled_by_id"] = interaction.user.id
            await self._save_giveaway(stored)
            updated = dict(stored)

        await self._update_message(updated)
        if await self._announce_winners(updated, rerolled=True):
            await self._mark_announcement_sent(record["id"])
        await interaction.followup.send("Giveaway rerolled.", ephemeral=True)

    @giveaway.command(name="cancel", description="Cancel an active giveaway without drawing winners.")
    @app_commands.describe(identifier="Giveaway message ID or giveaway ID.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def cancel(self, interaction: discord.Interaction, identifier: str):
        if not interaction.guild:
            return await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        record = self._find_giveaway(identifier, interaction.guild.id)
        if not record:
            return await interaction.followup.send("I could not find that giveaway.", ephemeral=True)
        if record["status"] != "active":
            return await interaction.followup.send("That giveaway is not active.", ephemeral=True)

        async with self._lock:
            stored = self._giveaways.get(record["id"])
            if not stored:
                return await interaction.followup.send("I could not find that giveaway anymore.", ephemeral=True)
            stored["status"] = "cancelled"
            stored["ended_at"] = now_ts()
            stored["cancelled_by_id"] = interaction.user.id
            await self._save_giveaway(stored)
            updated = dict(stored)

        await self._update_message(updated)
        await interaction.followup.send("Giveaway cancelled.", ephemeral=True)

    @giveaway.command(name="delete", description="Permanently delete an ended or cancelled giveaway from storage.")
    @app_commands.describe(identifier="Giveaway message ID or giveaway ID.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def delete(self, interaction: discord.Interaction, identifier: str):
        if not interaction.guild:
            return await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        record = self._find_giveaway(identifier, interaction.guild.id)
        if not record:
            return await interaction.followup.send("I could not find that giveaway.", ephemeral=True)
        if record["status"] == "active":
            return await interaction.followup.send(
                "Active giveaways cannot be deleted. Use /giveaway cancel or /giveaway end first.",
                ephemeral=True,
            )

        async with self._lock:
            stored = self._giveaways.get(record["id"])
            if not stored:
                return await interaction.followup.send("I could not find that giveaway anymore.", ephemeral=True)
            del self._giveaways[record["id"]]
            await self.store.delete_giveaway(record["id"])

        await interaction.followup.send("Giveaway permanently deleted from storage.", ephemeral=True)

    @giveaway.command(name="list", description="List giveaways in this server.")
    @app_commands.describe(active_only="Show only active giveaways.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def list_giveaways(self, interaction: discord.Interaction, active_only: bool = True):
        if not interaction.guild:
            return await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)

        rows = []
        for record in self._giveaways.values():
            if record.get("guild_id") != interaction.guild.id:
                continue
            if active_only and record.get("status") != "active":
                continue
            rows.append(record)
        rows.sort(key=lambda item: item.get("ends_at", 0), reverse=not active_only)

        embed = discord.Embed(title="Giveaways", color=discord.Color.green())
        if not rows:
            embed.description = "No giveaways found."
        else:
            lines = []
            for record in rows[:15]:
                status = str(record.get("status", "unknown")).title()
                message_id = record.get("message_id")
                channel_id = record.get("channel_id")
                ends_at = int(record.get("ends_at", 0))
                lines.append(
                    f"`{record['id']}` | {status} | <#{channel_id}> | `{message_id}` | "
                    f"{record.get('prize', 'Unknown')} | <t:{ends_at}:R>"
                )
            embed.description = "\n".join(lines)
            if len(rows) > 15:
                embed.set_footer(text=f"Showing 15 of {len(rows)} giveaways.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @giveaway.command(name="info", description="Show detailed information about a giveaway.")
    @app_commands.describe(identifier="Giveaway message ID or giveaway ID.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def info(self, interaction: discord.Interaction, identifier: str):
        if not interaction.guild:
            return await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)

        record = self._find_giveaway(identifier, interaction.guild.id)
        if not record:
            return await interaction.response.send_message("I could not find that giveaway.", ephemeral=True)
        await interaction.response.send_message(embed=self._build_embed(record, detailed=True), ephemeral=True)

    async def handle_entry(self, interaction: discord.Interaction) -> None:
        if not interaction.guild or not interaction.message:
            return await interaction.response.send_message("This giveaway is not available here.", ephemeral=True)
        if interaction.user.bot:
            return await interaction.response.send_message("Bots cannot enter giveaways.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        message_id = interaction.message.id
        user_id = interaction.user.id

        async with self._lock:
            record = self._find_giveaway(str(message_id), interaction.guild.id)
            if not record:
                return await interaction.followup.send("I could not find this giveaway.", ephemeral=True)
            if record["status"] != "active" or now_ts() >= int(record["ends_at"]):
                return await interaction.followup.send("This giveaway has already ended.", ephemeral=True)

            member = interaction.user if isinstance(interaction.user, discord.Member) else None
            if member is None:
                member = interaction.guild.get_member(user_id)
            if member is None:
                return await interaction.followup.send("I could not verify your server membership.", ephemeral=True)

            required_role_id = record.get("required_role_id")
            if required_role_id and not any(role.id == required_role_id for role in member.roles):
                return await interaction.followup.send(f"You need <@&{required_role_id}> to enter this giveaway.", ephemeral=True)

            entrants = [int(entry) for entry in record.get("entrants", [])]
            if user_id in entrants:
                entrants.remove(user_id)
                action_text = "You have left the giveaway."
            else:
                entrants.append(user_id)
                action_text = "You are now entered in the giveaway."
            record["entrants"] = entrants
            await self._save_giveaway(record)
            updated = dict(record)

        try:
            await interaction.message.edit(embed=self._build_embed(updated), view=GiveawayJoinView(self))
        except discord.HTTPException:
            pass
        await interaction.followup.send(action_text, ephemeral=True)

    async def handle_participants(self, interaction: discord.Interaction) -> None:
        if not interaction.guild or not interaction.message:
            return await interaction.response.send_message("This giveaway is not available here.", ephemeral=True)

        record = self._find_giveaway(str(interaction.message.id), interaction.guild.id)
        if not record:
            return await interaction.response.send_message("I could not find this giveaway.", ephemeral=True)

        await interaction.response.send_message(
            embed=self._participants_embed(record),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _run_due_giveaways(self) -> None:
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            due_ids = [
                record["id"]
                for record in list(self._giveaways.values())
                if record.get("status") == "ending"
                or (record.get("status") == "active" and int(record.get("ends_at", 0)) <= now_ts())
            ]
            for giveaway_id in due_ids:
                await self._finish_giveaway(giveaway_id)
            await self._retry_missing_announcements()
            await asyncio.sleep(CHECK_INTERVAL)

    async def _finish_giveaway(
        self,
        giveaway_id: str,
        winner_count: int | None = None,
        ended_by: int | None = None,
    ) -> dict[str, Any] | None:
        async with self._lock:
            record = self._giveaways.get(giveaway_id)
            if not record or record.get("status") not in {"active", "ending"}:
                return None
            if record.get("status") == "active":
                record["status"] = "ending"
                record["ended_at"] = now_ts()
                if ended_by:
                    record["ended_by_id"] = ended_by
                await self._save_giveaway(record)
            snapshot = dict(record)

        winners = await self._draw_winners(snapshot, winner_count=winner_count)

        async with self._lock:
            record = self._giveaways.get(giveaway_id)
            if not record:
                return None
            record["status"] = "ended"
            record["winner_ids"] = winners
            record["winner_announcement_sent"] = False
            if winner_count is not None:
                record["winners_count"] = winner_count
            record["ended_at"] = record.get("ended_at") or now_ts()
            await self._save_giveaway(record)
            final_record = dict(record)

        await self._update_message(final_record)
        if await self._announce_winners(final_record):
            await self._mark_announcement_sent(giveaway_id)
        return final_record

    async def _retry_missing_announcements(self) -> None:
        records = [
            dict(record)
            for record in self._giveaways.values()
            if record.get("status") == "ended" and not record.get("winner_announcement_sent")
        ]
        for record in records:
            if await self._announce_winners(record):
                await self._mark_announcement_sent(record["id"])

    async def _mark_announcement_sent(self, giveaway_id: str) -> None:
        async with self._lock:
            record = self._giveaways.get(giveaway_id)
            if record:
                record["winner_announcement_sent"] = True
                await self._save_giveaway(record)

    async def _draw_winners(
        self,
        record: dict[str, Any],
        winner_count: int | None = None,
        exclude: set[int] | None = None,
    ) -> list[int]:
        guild = self.bot.get_guild(int(record["guild_id"]))
        if not guild:
            return []

        required_role_id = record.get("required_role_id")
        bonus_role_id = record.get("bonus_role_id")
        bonus_entries = max(1, int(record.get("bonus_entries", 1)))
        excluded = exclude or set()
        weighted_pool: list[int] = []

        for entry in record.get("entrants", []):
            user_id = int(entry)
            if user_id in excluded:
                continue
            member = guild.get_member(user_id)
            if member is None:
                try:
                    member = await guild.fetch_member(user_id)
                except discord.HTTPException:
                    continue
            if required_role_id and not any(role.id == required_role_id for role in member.roles):
                continue
            weight = bonus_entries if bonus_role_id and any(role.id == bonus_role_id for role in member.roles) else 1
            weighted_pool.extend([user_id] * min(weight, 50))

        target = winner_count or int(record.get("winners_count", 1))
        rng = random.SystemRandom()
        winners: list[int] = []
        while weighted_pool and len(winners) < target:
            picked = rng.choice(weighted_pool)
            winners.append(picked)
            weighted_pool = [user_id for user_id in weighted_pool if user_id != picked]
        return winners

    async def _update_message(self, record: dict[str, Any]) -> None:
        channel = self.bot.get_channel(int(record["channel_id"]))
        if channel is None:
            try:
                fetched = await self.bot.fetch_channel(int(record["channel_id"]))
                channel = fetched if isinstance(fetched, discord.TextChannel) else None
            except discord.HTTPException:
                return
        if not isinstance(channel, discord.TextChannel) or not record.get("message_id"):
            return

        try:
            message = await channel.fetch_message(int(record["message_id"]))
            view = GiveawayJoinView(self) if record.get("status") == "active" else GiveawayEndedView(self)
            await message.edit(embed=self._build_embed(record), view=view)
        except discord.HTTPException:
            return

    async def _announce_winners(self, record: dict[str, Any], rerolled: bool = False) -> bool:
        channel = self.bot.get_channel(int(record["channel_id"]))
        if not isinstance(channel, discord.TextChannel):
            return False

        winner_ids = [int(user_id) for user_id in record.get("winner_ids", [])]
        jump_url = None
        if record.get("message_id"):
            jump_url = f"https://discord.com/channels/{record['guild_id']}/{record['channel_id']}/{record['message_id']}"

        if winner_ids:
            mentions = ", ".join(f"<@{user_id}>" for user_id in winner_ids)
            prefix = "New winner" if rerolled else "Congratulations"
            content = f"{prefix} {mentions}! You won **{record['prize']}**."
            if jump_url:
                content += f"\nGiveaway: {jump_url}"
        else:
            content = f"The giveaway for **{record['prize']}** ended with no valid entries."
            if jump_url:
                content += f"\nGiveaway: {jump_url}"

        try:
            await channel.send(content, allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False))
            return True
        except discord.HTTPException:
            return False

    def _announcement_content(
        self,
        record: dict[str, Any],
        ping_role: discord.Role | None,
    ) -> tuple[str, discord.AllowedMentions]:
        lines = []
        if record.get("ping_everyone"):
            lines.append("@everyone")
        if ping_role:
            lines.append(ping_role.mention)
        lines.append("A new giveaway has started.")
        allowed_mentions = discord.AllowedMentions(
            everyone=bool(record.get("ping_everyone")),
            roles=[ping_role] if ping_role else False,
            users=False,
        )
        return "\n".join(lines), allowed_mentions

    def _build_embed(self, record: dict[str, Any], detailed: bool = False) -> discord.Embed:
        status = str(record.get("status", "active"))
        color = int(record.get("color") or DEFAULT_COLOR)
        if status == "cancelled":
            color = discord.Color.red().value

        title_status = {
            "active": "Giveaway",
            "ending": "Giveaway Ending",
            "ended": "Giveaway Ended",
            "cancelled": "Giveaway Cancelled",
        }.get(status, "Giveaway")

        embed = discord.Embed(
            title=f"{title_status}: {record.get('prize', 'Unknown Prize')}",
            description=record.get("description") or None,
            color=color,
            timestamp=datetime.fromtimestamp(int(record.get("ends_at", now_ts())), timezone.utc),
        )

        ends_at = int(record.get("ends_at", 0))
        entries = len(record.get("entrants", []))
        winner_count = int(record.get("winners_count", 1))
        winner_ids = [int(user_id) for user_id in record.get("winner_ids", [])]

        if status == "active":
            timing = f"Ends <t:{ends_at}:R>\nExact time: <t:{ends_at}:F>"
        elif status == "cancelled":
            timing = "This giveaway was cancelled."
        else:
            timing = f"Ended <t:{int(record.get('ended_at') or ends_at)}:R>"

        embed.add_field(name="Prize", value=str(record.get("prize", "Unknown Prize"))[:1024], inline=False)
        embed.add_field(name="Time", value=timing, inline=False)
        embed.add_field(name="Host", value=f"<@{int(record.get('host_id', 0))}>", inline=True)
        embed.add_field(name="Entries", value=str(entries), inline=True)

        if status in {"ended", "ending"}:
            winners_text = ", ".join(f"<@{user_id}>" for user_id in winner_ids) if winner_ids else "No winners"
            embed.add_field(name="Winners", value=winners_text[:1024], inline=False)
        else:
            embed.add_field(name="Winners", value=str(winner_count), inline=True)

        requirement_lines = []
        if record.get("required_role_id"):
            requirement_lines.append(f"Required role: <@&{record['required_role_id']}>")
        if record.get("bonus_role_id"):
            requirement_lines.append(f"Bonus role: <@&{record['bonus_role_id']}> gets x{record.get('bonus_entries', 2)} chance")
        embed.add_field(
            name="Requirements",
            value="\n".join(requirement_lines) if requirement_lines else "No special requirements",
            inline=False,
        )

        if detailed:
            duration = format_duration(max(0, ends_at - int(record.get("created_at", ends_at))))
            embed.add_field(name="Giveaway ID", value=f"`{record['id']}`", inline=True)
            embed.add_field(name="Message ID", value=f"`{record.get('message_id')}`", inline=True)
            embed.add_field(name="Duration", value=duration, inline=True)

        if record.get("image_url"):
            embed.set_image(url=record["image_url"])
        if record.get("thumbnail_url"):
            embed.set_thumbnail(url=record["thumbnail_url"])

        footer = f"Giveaway ID: {record['id']}"
        if status == "active":
            footer += " | Click Enter Giveaway to join or leave"
        embed.set_footer(text=footer)
        return embed

    def _participants_embed(self, record: dict[str, Any]) -> discord.Embed:
        participant_text, total, truncated = format_participants_preview(record.get("entrants", []))
        status = str(record.get("status", "active")).title()
        embed = discord.Embed(
            title=f"Participants: {record.get('prize', 'Unknown Prize')}",
            description=participant_text,
            color=int(record.get("color") or DEFAULT_COLOR),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Total Entries", value=str(total), inline=True)
        embed.add_field(name="Status", value=status, inline=True)
        embed.add_field(name="Giveaway ID", value=f"`{record.get('id', 'unknown')}`", inline=True)
        if truncated:
            embed.set_footer(text=f"Showing first {PARTICIPANT_PREVIEW_LIMIT} participants.")
        else:
            embed.set_footer(text="Participants are shown privately to you.")
        return embed

    def _find_giveaway(self, identifier: str, guild_id: int | None = None) -> dict[str, Any] | None:
        ident = identifier.strip()
        record = self._giveaways.get(ident)
        if record and (guild_id is None or record.get("guild_id") == guild_id):
            return record
        for item in self._giveaways.values():
            if guild_id is not None and item.get("guild_id") != guild_id:
                continue
            if str(item.get("message_id")) == ident:
                return item
        return None

    def _new_id(self) -> str:
        while True:
            giveaway_id = secrets.token_hex(4)
            if giveaway_id not in self._giveaways:
                return giveaway_id

    async def _save_giveaway(self, record: dict[str, Any]) -> None:
        snapshot = normalize_record(record)
        giveaway_id = str(snapshot["id"])
        self._giveaways[giveaway_id] = snapshot
        await self.store.save_giveaway(snapshot)

    def _load_legacy_file(self) -> dict[str, dict[str, Any]]:
        if not os.path.exists(DATA_FILE):
            return {}
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as file:
                data = json.load(file)
            giveaways = data.get("giveaways", {}) if isinstance(data, dict) else {}
            if isinstance(giveaways, dict):
                return {str(key): value for key, value in giveaways.items() if isinstance(value, dict)}
        except Exception:
            broken_name = f"{DATA_FILE}.broken-{int(time.time())}"
            try:
                os.replace(DATA_FILE, broken_name)
            except OSError:
                pass
        return {}

    async def _migrate_legacy_file(self) -> None:
        legacy_giveaways = self._load_legacy_file()
        if not legacy_giveaways:
            return

        migrated = 0
        async with self._lock:
            for key, record in legacy_giveaways.items():
                if not isinstance(record, dict):
                    continue
                record["id"] = str(record.get("id") or key)
                if record["id"] in self._giveaways:
                    continue
                await self._save_giveaway(record)
                migrated += 1

        if migrated:
            migrated_name = f"{DATA_FILE}.migrated-{int(time.time())}"
            try:
                os.replace(DATA_FILE, migrated_name)
            except OSError:
                pass

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            message = "You need Manage Server permission to use this giveaway command."
        elif isinstance(error, app_commands.BotMissingPermissions):
            missing = ", ".join(error.missing_permissions)
            message = f"I am missing these permissions: {missing}"
        else:
            message = "An unexpected giveaway error occurred. Please try again."
            print(f"[GIVEAWAY ERROR] {error!r}")

        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot):
    cog = Giveaway(bot)
    await cog.initialize()
    await bot.add_cog(cog)
    bot.add_view(GiveawayJoinView(cog))
    bot.add_view(GiveawayEndedView(cog))
    cog.start_runner()
    guide = bot.get_command("giveaway")
    if guide:
        guide.category = "Admin"
