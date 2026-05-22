import asyncio
import json
import os
import time
from copy import deepcopy
from typing import Any

import discord
from discord.ext import commands

from services.invite_store import InviteTrackerStore, normalize_config


DATA_FILE = "invite_tracker.json"


def current_ts() -> int:
    return int(time.time())


def parse_toggle(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in {"on", "true", "yes", "enable", "enabled"}:
        return True
    if lowered in {"off", "false", "no", "disable", "disabled"}:
        return False
    raise ValueError("Use `on` or `off`.")


class InviteTracker(commands.Cog):
    """Persistent invite tracker with reward roles."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._lock = asyncio.Lock()
        self.store = InviteTrackerStore()
        self._data: dict[str, Any] = {"version": 1, "guilds": {}}

    async def initialize(self) -> None:
        self._data = await self.store.load_all()
        await self._migrate_legacy_file()

    def _load_legacy_file(self) -> dict[str, Any]:
        if not os.path.exists(DATA_FILE):
            return {"version": 1, "guilds": {}}
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as file:
                data = json.load(file)
            if isinstance(data, dict) and isinstance(data.get("guilds"), dict):
                data.setdefault("version", 1)
                return data
        except Exception:
            broken_name = f"{DATA_FILE}.broken-{current_ts()}"
            try:
                os.replace(DATA_FILE, broken_name)
            except OSError:
                pass
        return {"version": 1, "guilds": {}}

    async def _migrate_legacy_file(self) -> None:
        legacy = self._load_legacy_file()
        legacy_guilds = legacy.get("guilds", {}) if isinstance(legacy, dict) else {}
        if not isinstance(legacy_guilds, dict) or not legacy_guilds:
            return

        migrated = 0
        async with self._lock:
            guilds = self._data.setdefault("guilds", {})
            for guild_id, config in legacy_guilds.items():
                key = str(guild_id)
                if key in guilds:
                    continue
                guilds[key] = normalize_config(config if isinstance(config, dict) else {})
                await self.store.save_guild(key, guilds[key])
                migrated += 1

        if migrated:
            migrated_name = f"{DATA_FILE}.migrated-{current_ts()}"
            try:
                os.replace(DATA_FILE, migrated_name)
            except OSError:
                pass

    async def _save_guild(self, guild_id: int) -> None:
        config = deepcopy(self._config(guild_id))
        await self.store.save_guild(guild_id, config)

    def _config(self, guild_id: int) -> dict[str, Any]:
        guilds = self._data.setdefault("guilds", {})
        key = str(guild_id)
        if key not in guilds:
            guilds[key] = {
                "enabled": False,
                "count_leaves": False,
                "log_channel_id": None,
                "rewards": [],
                "member_invites": {},
                "member_joins": {},
                "invite_cache": {},
                "vanity_uses": None,
                "last_sync_ts": 0,
            }
        config = guilds[key]
        config.setdefault("enabled", False)
        config.setdefault("count_leaves", False)
        config.setdefault("log_channel_id", None)
        config.setdefault("rewards", [])
        config.setdefault("member_invites", {})
        config.setdefault("member_joins", {})
        config.setdefault("invite_cache", {})
        config.setdefault("vanity_uses", None)
        config.setdefault("last_sync_ts", 0)
        config["rewards"] = sorted(
            [reward for reward in config["rewards"] if isinstance(reward, dict) and "count" in reward and "role_id" in reward],
            key=lambda reward: int(reward["count"]),
        )
        return config

    async def _fetch_invites(self, guild: discord.Guild) -> dict[str, dict[str, Any]] | None:
        try:
            invites = await guild.invites()
        except (discord.Forbidden, discord.HTTPException):
            return None

        data: dict[str, dict[str, Any]] = {}
        for invite in invites:
            data[invite.code] = {
                "uses": int(invite.uses or 0),
                "inviter_id": invite.inviter.id if invite.inviter else None,
                "channel_id": invite.channel.id if invite.channel else None,
            }
        return data

    async def _fetch_vanity_uses(self, guild: discord.Guild) -> int | None:
        if "VANITY_URL" not in getattr(guild, "features", []):
            return None
        try:
            invite = await guild.vanity_invite()
            return int(invite.uses or 0)
        except (discord.Forbidden, discord.HTTPException):
            return None

    async def _resync_guild(self, guild: discord.Guild) -> tuple[bool, str]:
        invites = await self._fetch_invites(guild)
        if invites is None:
            return False, "I could not fetch invites. Make sure I have Manage Server permission."
        vanity_uses = await self._fetch_vanity_uses(guild)

        async with self._lock:
            config = self._config(guild.id)
            config["invite_cache"] = invites
            config["vanity_uses"] = vanity_uses
            config["last_sync_ts"] = current_ts()
            await self._save_guild(guild.id)
        return True, f"Invite cache synced with {len(invites)} invite(s)."

    def _detect_used_invite(
        self,
        before_cache: dict[str, dict[str, Any]],
        after_cache: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        best_match: dict[str, Any] | None = None
        best_diff = 0
        for code, after_data in after_cache.items():
            before_uses = int(before_cache.get(code, {}).get("uses", 0))
            after_uses = int(after_data.get("uses", 0))
            diff = after_uses - before_uses
            if diff > best_diff:
                best_diff = diff
                best_match = {"code": code, **after_data}
        return best_match

    async def _log_event(self, guild: discord.Guild, embed: discord.Embed) -> None:
        config = self._config(guild.id)
        channel_id = config.get("log_channel_id")
        if not channel_id:
            return
        channel = guild.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            return

    async def _sync_reward_roles(self, member: discord.Member, invite_count: int) -> tuple[list[str], list[str]]:
        guild = member.guild
        me = guild.me
        if me is None or not me.guild_permissions.manage_roles:
            return [], []

        config = self._config(guild.id)
        rewards = sorted(config.get("rewards", []), key=lambda reward: int(reward["count"]))
        added: list[str] = []
        removed: list[str] = []

        for reward in rewards:
            role = guild.get_role(int(reward["role_id"]))
            if role is None or role >= me.top_role:
                continue
            threshold = int(reward["count"])
            should_have = invite_count >= threshold
            has_role = role in member.roles
            try:
                if should_have and not has_role:
                    await member.add_roles(role, reason="Invite tracker reward")
                    added.append(role.name)
                elif not should_have and has_role:
                    await member.remove_roles(role, reason="Invite tracker reward sync")
                    removed.append(role.name)
            except discord.HTTPException:
                continue
        return added, removed

    def _next_reward_text(self, config: dict[str, Any], current_count: int) -> str:
        rewards = sorted(config.get("rewards", []), key=lambda reward: int(reward["count"]))
        for reward in rewards:
            if current_count < int(reward["count"]):
                return f"{reward['count']} invites"
        return "All rewards unlocked"

    def _config_embed(self, guild: discord.Guild, config: dict[str, Any]) -> discord.Embed:
        embed = discord.Embed(title="Invite Tracker Config", color=discord.Color.green())
        embed.add_field(name="Status", value="Enabled" if config.get("enabled") else "Disabled", inline=True)
        embed.add_field(name="Count Leaves", value="On" if config.get("count_leaves") else "Off", inline=True)
        embed.add_field(name="Tracked Inviters", value=str(len(config.get("member_invites", {}))), inline=True)
        log_channel = f"<#{config['log_channel_id']}>" if config.get("log_channel_id") else "Not set"
        embed.add_field(name="Log Channel", value=log_channel, inline=False)
        rewards = config.get("rewards", [])
        if rewards:
            lines = []
            for reward in rewards:
                role = guild.get_role(int(reward["role_id"]))
                role_text = role.mention if role else f"(missing role {reward['role_id']})"
                lines.append(f"{reward['count']} invites -> {role_text}")
            embed.add_field(name="Reward Roles", value="\n".join(lines), inline=False)
        else:
            embed.add_field(name="Reward Roles", value="No reward roles configured.", inline=False)
        embed.set_footer(text="The bot needs Manage Server to fetch invites and Manage Roles to award reward roles.")
        return embed

    @commands.group(name="invite", invoke_without_command=True, help="Manage invite tracking and reward roles.")
    @commands.guild_only()
    async def invite_group(self, ctx: commands.Context):
        embed = discord.Embed(
            title="Invite Tracker",
            description="Track invites, reward roles automatically, and customize thresholds.",
            color=discord.Color.green(),
        )
        embed.add_field(name="Setup", value="`!invite enable`\n`!invite log #channel`\n`!invite reward add <count> @role`", inline=False)
        embed.add_field(name="Management", value="`!invite config`\n`!invite resync`\n`!invite countleaves <on|off>`", inline=False)
        embed.add_field(name="Rewards", value="`!invite reward remove <count>`\n`!invite disable`", inline=False)
        embed.add_field(name="Stats", value="`!invites [member]`\n`!inviteleaderboard`", inline=False)
        await ctx.send(embed=embed)

    @invite_group.command(name="enable", help="Enable the invite tracker in this server.")
    @commands.has_permissions(manage_guild=True)
    async def invite_enable(self, ctx: commands.Context):
        ok, message = await self._resync_guild(ctx.guild)
        if not ok:
            return await ctx.send(message)
        async with self._lock:
            config = self._config(ctx.guild.id)
            config["enabled"] = True
            await self._save_guild(ctx.guild.id)
        await ctx.send("Invite tracker enabled. " + message)

    @invite_group.command(name="disable", help="Disable the invite tracker in this server.")
    @commands.has_permissions(manage_guild=True)
    async def invite_disable(self, ctx: commands.Context):
        async with self._lock:
            config = self._config(ctx.guild.id)
            config["enabled"] = False
            await self._save_guild(ctx.guild.id)
        await ctx.send("Invite tracker disabled.")

    @invite_group.command(name="config", help="Show the current invite tracker configuration.")
    @commands.has_permissions(manage_guild=True)
    async def invite_config(self, ctx: commands.Context):
        async with self._lock:
            config = deepcopy(self._config(ctx.guild.id))
        await ctx.send(embed=self._config_embed(ctx.guild, config))

    @invite_group.command(name="log", help="Set the invite tracker log channel, or use `off`.")
    @commands.has_permissions(manage_guild=True)
    async def invite_log(self, ctx: commands.Context, channel: str = None):
        async with self._lock:
            config = self._config(ctx.guild.id)
            if channel is None or channel.lower() == "off":
                config["log_channel_id"] = None
                await self._save_guild(ctx.guild.id)
                return await ctx.send("Invite tracker log channel cleared.")

            if not ctx.message.channel_mentions:
                return await ctx.send("Mention a text channel or use `!invite log off`.")
            mentioned = ctx.message.channel_mentions[0]
            if not isinstance(mentioned, discord.TextChannel):
                return await ctx.send("Please mention a normal text channel.")
            config["log_channel_id"] = mentioned.id
            await self._save_guild(ctx.guild.id)
        await ctx.send(f"Invite tracker log channel set to {mentioned.mention}.")

    @invite_group.command(name="countleaves", help="Choose whether leaving members should reduce invite totals.")
    @commands.has_permissions(manage_guild=True)
    async def invite_count_leaves(self, ctx: commands.Context, value: str):
        try:
            enabled = parse_toggle(value)
        except ValueError as exc:
            return await ctx.send(str(exc))
        async with self._lock:
            config = self._config(ctx.guild.id)
            config["count_leaves"] = enabled
            await self._save_guild(ctx.guild.id)
        await ctx.send(f"Count leaves is now {'enabled' if enabled else 'disabled'}.")

    @invite_group.command(name="resync", help="Refresh the invite cache from Discord.")
    @commands.has_permissions(manage_guild=True)
    async def invite_resync(self, ctx: commands.Context):
        ok, message = await self._resync_guild(ctx.guild)
        await ctx.send(message)

    @invite_group.command(name="reset", help="Set a member's invite total manually.")
    @commands.has_permissions(manage_guild=True)
    async def invite_reset(self, ctx: commands.Context, member: discord.Member, count: int = 0):
        if count < 0:
            return await ctx.send("Invite count cannot be negative.")

        async with self._lock:
            config = self._config(ctx.guild.id)
            config["member_invites"][str(member.id)] = count
            await self._save_guild(ctx.guild.id)
        added, removed = await self._sync_reward_roles(member, count)
        message = f"{member.mention} now has `{count}` tracked invites."
        if added:
            message += f"\nAdded roles: {', '.join(added)}"
        if removed:
            message += f"\nRemoved roles: {', '.join(removed)}"
        await ctx.send(message)

    @invite_group.group(name="reward", invoke_without_command=True, help="Manage invite reward thresholds.")
    @commands.has_permissions(manage_guild=True)
    async def invite_reward_group(self, ctx: commands.Context):
        async with self._lock:
            config = deepcopy(self._config(ctx.guild.id))
        rewards = config.get("rewards", [])
        if not rewards:
            return await ctx.send("No invite rewards configured yet.")
        lines = []
        for reward in rewards:
            role = ctx.guild.get_role(int(reward["role_id"]))
            role_text = role.mention if role else f"(missing role {reward['role_id']})"
            lines.append(f"{reward['count']} invites -> {role_text}")
        await ctx.send(embed=discord.Embed(title="Invite Rewards", description="\n".join(lines), color=discord.Color.green()))

    @invite_reward_group.command(name="add", help="Add or update an invite reward threshold.")
    async def invite_reward_add(self, ctx: commands.Context, count: int, role: discord.Role):
        if count <= 0:
            return await ctx.send("Invite threshold must be positive.")

        async with self._lock:
            config = self._config(ctx.guild.id)
            rewards = [reward for reward in config["rewards"] if int(reward["count"]) != count]
            rewards.append({"count": count, "role_id": role.id})
            config["rewards"] = sorted(rewards, key=lambda reward: int(reward["count"]))
            tracked = {int(user_id): int(total) for user_id, total in config.get("member_invites", {}).items()}
            await self._save_guild(ctx.guild.id)

        sync_count = 0
        for user_id, total in tracked.items():
            member = ctx.guild.get_member(user_id)
            if member:
                await self._sync_reward_roles(member, total)
                sync_count += 1
        await ctx.send(f"Reward updated: {count} invites -> {role.mention}. Synced {sync_count} tracked member(s).")

    @invite_reward_group.command(name="remove", help="Remove an invite reward threshold.")
    async def invite_reward_remove(self, ctx: commands.Context, count: int):
        async with self._lock:
            config = self._config(ctx.guild.id)
            before = len(config["rewards"])
            config["rewards"] = [reward for reward in config["rewards"] if int(reward["count"]) != count]
            if len(config["rewards"]) == before:
                await self._save_guild(ctx.guild.id)
                return await ctx.send("No reward threshold with that invite count was found.")
            tracked = {int(user_id): int(total) for user_id, total in config.get("member_invites", {}).items()}
            await self._save_guild(ctx.guild.id)

        for user_id, total in tracked.items():
            member = ctx.guild.get_member(user_id)
            if member:
                await self._sync_reward_roles(member, total)
        await ctx.send(f"Removed the reward threshold for {count} invites.")

    @commands.command(name="invites", help="Show your invite stats or another member's invite stats.")
    @commands.guild_only()
    async def invites(self, ctx: commands.Context, member: discord.Member = None):
        target = member or ctx.author
        async with self._lock:
            config = deepcopy(self._config(ctx.guild.id))
        total = int(config.get("member_invites", {}).get(str(target.id), 0))
        next_reward = self._next_reward_text(config, total)
        embed = discord.Embed(title=f"Invite Stats: {target.display_name}", color=discord.Color.green())
        embed.add_field(name="Tracked Invites", value=str(total), inline=True)
        embed.add_field(name="Next Reward", value=next_reward, inline=True)
        reward_roles = []
        for reward in config.get("rewards", []):
            if total >= int(reward["count"]):
                role = ctx.guild.get_role(int(reward["role_id"]))
                if role:
                    reward_roles.append(role.mention)
        embed.add_field(name="Unlocked Rewards", value=", ".join(reward_roles) if reward_roles else "None yet", inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="inviteleaderboard", aliases=["invitetop"], help="Show the top inviters in the server.")
    @commands.guild_only()
    async def invite_leaderboard(self, ctx: commands.Context):
        async with self._lock:
            config = deepcopy(self._config(ctx.guild.id))
        totals = [(int(total), int(user_id)) for user_id, total in config.get("member_invites", {}).items()]
        totals = [row for row in totals if row[0] > 0]
        totals.sort(reverse=True)
        if not totals:
            return await ctx.send("No invite data yet.")

        lines = []
        for index, (total, user_id) in enumerate(totals[:10], start=1):
            member = ctx.guild.get_member(user_id)
            name = member.display_name if member else f"User {user_id}"
            lines.append(f"{index}. {name} - {total} invites")
        await ctx.send(embed=discord.Embed(title="Invite Leaderboard", description="\n".join(lines), color=discord.Color.green()))

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            async with self._lock:
                config = self._config(guild.id)
                should_sync = bool(config.get("enabled")) or not config.get("invite_cache")
            if should_sync:
                await self._resync_guild(guild)

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        if not invite.guild:
            return
        async with self._lock:
            config = self._config(invite.guild.id)
            cache = config.setdefault("invite_cache", {})
            cache[invite.code] = {
                "uses": int(invite.uses or 0),
                "inviter_id": invite.inviter.id if invite.inviter else None,
                "channel_id": invite.channel.id if invite.channel else None,
            }
            await self._save_guild(invite.guild.id)

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite):
        if not invite.guild:
            return
        async with self._lock:
            config = self._config(invite.guild.id)
            config.setdefault("invite_cache", {}).pop(invite.code, None)
            await self._save_guild(invite.guild.id)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        async with self._lock:
            config_snapshot = deepcopy(self._config(guild.id))
        if not config_snapshot.get("enabled"):
            return

        before_cache = config_snapshot.get("invite_cache", {})
        before_vanity = config_snapshot.get("vanity_uses")
        after_cache = await self._fetch_invites(guild)
        if after_cache is None:
            return
        after_vanity = await self._fetch_vanity_uses(guild)
        used_invite = self._detect_used_invite(before_cache, after_cache)
        used_vanity = False
        if used_invite is None and before_vanity is not None and after_vanity is not None and after_vanity > before_vanity:
            used_vanity = True

        inviter_member: discord.Member | None = None
        invite_total = None
        added_roles: list[str] = []
        removed_roles: list[str] = []

        async with self._lock:
            config = self._config(guild.id)
            config["invite_cache"] = after_cache
            config["vanity_uses"] = after_vanity
            config["last_sync_ts"] = current_ts()

            if used_invite and used_invite.get("inviter_id"):
                inviter_id = int(used_invite["inviter_id"])
                config["member_joins"][str(member.id)] = inviter_id
                config["member_invites"][str(inviter_id)] = int(config["member_invites"].get(str(inviter_id), 0)) + 1
                invite_total = int(config["member_invites"][str(inviter_id)])
            await self._save_guild(guild.id)

        if used_invite and used_invite.get("inviter_id"):
            inviter_member = guild.get_member(int(used_invite["inviter_id"]))
            if inviter_member:
                added_roles, removed_roles = await self._sync_reward_roles(inviter_member, int(invite_total or 0))

        embed = discord.Embed(title="Member Joined", color=discord.Color.green(), timestamp=discord.utils.utcnow())
        embed.add_field(name="Member", value=f"{member.mention} (`{member.id}`)", inline=False)
        if inviter_member and invite_total is not None:
            embed.add_field(name="Inviter", value=f"{inviter_member.mention}", inline=True)
            embed.add_field(name="Total Invites", value=str(invite_total), inline=True)
            embed.add_field(name="Invite Code", value=f"`{used_invite['code']}`", inline=True)
            if added_roles:
                embed.add_field(name="New Reward Roles", value=", ".join(added_roles), inline=False)
            if removed_roles:
                embed.add_field(name="Removed Reward Roles", value=", ".join(removed_roles), inline=False)
        elif used_vanity:
            embed.add_field(name="Source", value="Vanity URL", inline=False)
        else:
            embed.add_field(name="Source", value="Unknown invite", inline=False)
        await self._log_event(guild, embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        guild = member.guild
        async with self._lock:
            config = self._config(guild.id)
            inviter_id = config.get("member_joins", {}).pop(str(member.id), None)
            if inviter_id is None:
                await self._save_guild(guild.id)
                return

            should_decrement = bool(config.get("enabled")) and bool(config.get("count_leaves"))
            invite_total = None
            if should_decrement:
                key = str(inviter_id)
                current_total = int(config.get("member_invites", {}).get(key, 0))
                config["member_invites"][key] = max(0, current_total - 1)
                invite_total = int(config["member_invites"][key])
            await self._save_guild(guild.id)

        added_roles: list[str] = []
        removed_roles: list[str] = []
        inviter_member = guild.get_member(int(inviter_id)) if inviter_id else None
        if inviter_member and invite_total is not None:
            added_roles, removed_roles = await self._sync_reward_roles(inviter_member, invite_total)

        if should_decrement and inviter_member and invite_total is not None:
            embed = discord.Embed(title="Member Left", color=discord.Color.orange(), timestamp=discord.utils.utcnow())
            embed.add_field(name="Member", value=f"{member} (`{member.id}`)", inline=False)
            embed.add_field(name="Inviter", value=inviter_member.mention, inline=True)
            embed.add_field(name="Updated Invites", value=str(invite_total), inline=True)
            if added_roles:
                embed.add_field(name="Added Reward Roles", value=", ".join(added_roles), inline=False)
            if removed_roles:
                embed.add_field(name="Removed Reward Roles", value=", ".join(removed_roles), inline=False)
            await self._log_event(guild, embed)

    @invite_group.error
    @invites.error
    @invite_leaderboard.error
    async def invite_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingPermissions):
            return await ctx.send("You need Manage Server permission to use this invite command.")
        if isinstance(error, commands.MissingRequiredArgument):
            return await ctx.send("Missing argument. Use `!invite` to see the invite tracker setup guide.")
        if isinstance(error, commands.BadArgument):
            return await ctx.send("I could not understand that member, role, or channel.")
        raise error


async def setup(bot: commands.Bot):
    cog = InviteTracker(bot)
    await cog.initialize()
    await bot.add_cog(cog)
    for command_name in ("invite", "invites", "inviteleaderboard"):
        command = bot.get_command(command_name)
        if command:
            command.category = "Invites"
