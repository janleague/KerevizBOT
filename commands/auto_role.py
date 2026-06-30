import asyncio
from dataclasses import dataclass
from typing import Any

import discord
from discord.ext import commands

from services.reaction_role_store import ReactionRolePanelStore, normalize_panel


MEMBER_ROLE_NAME = "Member"
ROLE_COLOR = 0x57F287
ASSIGN_DELAY_SECONDS = 0.35

PING_ROLES_CHANNEL_ID = 1521423149286162534
YOUTUBE_PING_ROLE_ID = 1176193004361490577
GIVEAWAY_PING_ROLE_ID = 1521422402330955828
PANEL_COLOR = 0xFEE75C
PANEL_MARKER = "Kereviz Notification Roles"
YOUTUBE_EMOJI = "\N{CLAPPER BOARD}"
GIVEAWAY_EMOJI = "\N{PARTY POPPER}"


@dataclass
class SyncResult:
    added: int = 0
    skipped_bots: int = 0
    already_had_role: int = 0
    failed: int = 0
    checked: int = 0
    error: str | None = None


@dataclass(frozen=True)
class ReactionRoleOption:
    key: str
    emoji: str
    role_id: int
    label: str
    description: str


@dataclass
class PanelSyncResult:
    added: int = 0
    already_had_role: int = 0
    skipped_bots: int = 0
    missing_members: int = 0
    failed: int = 0
    checked_reactions: int = 0
    error: str | None = None


REACTION_ROLE_OPTIONS: tuple[ReactionRoleOption, ...] = (
    ReactionRoleOption(
        key="youtube",
        emoji=YOUTUBE_EMOJI,
        role_id=YOUTUBE_PING_ROLE_ID,
        label="YouTube Ping",
        description="Get notified when a new Kereviz video or upload announcement goes live.",
    ),
    ReactionRoleOption(
        key="giveaway",
        emoji=GIVEAWAY_EMOJI,
        role_id=GIVEAWAY_PING_ROLE_ID,
        label="Giveaway Ping",
        description="Get notified when a new giveaway starts.",
    ),
)
REACTION_ROLE_BY_EMOJI = {option.emoji: option for option in REACTION_ROLE_OPTIONS}


class AutoRole(commands.Cog):
    """Keeps member roles synced and manages the notification reaction-role panel."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._sync_lock = asyncio.Lock()
        self._panel_lock = asyncio.Lock()
        self._background_task: asyncio.Task | None = None
        self._panel_task: asyncio.Task | None = None
        self._panel_records: dict[int, dict[str, Any]] = {}
        self._panel_store = ReactionRolePanelStore()
        self._panel_store_available = True

    def _bot_member(self, guild: discord.Guild) -> discord.Member | None:
        if guild.me:
            return guild.me
        if self.bot.user:
            return guild.get_member(self.bot.user.id)
        return None

    def _can_manage_role(self, guild: discord.Guild, role: discord.Role) -> bool:
        me = self._bot_member(guild)
        if me is None or not me.guild_permissions.manage_roles:
            return False
        return not role.managed and role < me.top_role

    async def initialize(self) -> None:
        try:
            self._panel_records = await self._panel_store.load_all()
        except Exception as exc:
            self._panel_store_available = False
            print(f"[REACTION-ROLES] Firestore panel state is unavailable: {exc}")

    def cog_unload(self) -> None:
        if self._background_task and not self._background_task.done():
            self._background_task.cancel()
        if self._panel_task and not self._panel_task.done():
            self._panel_task.cancel()

    async def _fetch_panel_channel(self) -> discord.TextChannel | None:
        channel = self.bot.get_channel(PING_ROLES_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(PING_ROLES_CHANNEL_ID)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
                print(f"[REACTION-ROLES] Could not fetch panel channel {PING_ROLES_CHANNEL_ID}: {exc}")
                return None

        if not isinstance(channel, discord.TextChannel):
            print(f"[REACTION-ROLES] Configured panel channel is not a text channel: {PING_ROLES_CHANNEL_ID}")
            return None
        return channel

    async def _load_panel_record(self, guild_id: int) -> dict[str, Any]:
        if guild_id in self._panel_records:
            return self._panel_records[guild_id]
        if not self._panel_store_available:
            return {}

        try:
            record = await self._panel_store.load_panel(guild_id)
        except Exception as exc:
            self._panel_store_available = False
            print(f"[REACTION-ROLES] Could not load panel state for guild {guild_id}: {exc}")
            return {}

        if record:
            self._panel_records[guild_id] = record
        return record

    async def _save_panel_record(self, record: dict[str, Any]) -> None:
        normalized = normalize_panel(record)
        guild_id = normalized.get("guild_id")
        if guild_id is None:
            return

        self._panel_records[int(guild_id)] = normalized
        if not self._panel_store_available:
            return

        try:
            await self._panel_store.save_panel(normalized)
        except Exception as exc:
            self._panel_store_available = False
            print(f"[REACTION-ROLES] Could not save panel state: {exc}")

    def _panel_setup_issues(self, guild: discord.Guild, channel: discord.TextChannel) -> list[str]:
        issues: list[str] = []
        me = self._bot_member(guild)
        if me is None:
            return ["I could not verify my server permissions."]

        channel_perms = channel.permissions_for(me)
        required_channel_permissions = (
            ("view_channel", "View Channel"),
            ("send_messages", "Send Messages"),
            ("embed_links", "Embed Links"),
            ("read_message_history", "Read Message History"),
            ("add_reactions", "Add Reactions"),
        )
        for attr, label in required_channel_permissions:
            if not getattr(channel_perms, attr):
                issues.append(f"I need **{label}** in {channel.mention}.")

        if not me.guild_permissions.manage_roles:
            issues.append("I need the **Manage Roles** server permission.")

        for option in REACTION_ROLE_OPTIONS:
            role = guild.get_role(option.role_id)
            if role is None:
                issues.append(f"The **{option.label}** role (`{option.role_id}`) was not found.")
            elif not self._can_manage_role(guild, role):
                issues.append(
                    f"I cannot manage {role.mention}. Move my bot role above it and make sure it is not managed."
                )

        return issues

    def _build_panel_embed(self, guild: discord.Guild) -> discord.Embed:
        embed = discord.Embed(
            title="Notification Roles",
            description=(
                "Choose which server pings you want to receive.\n\n"
                "React below to subscribe. Remove your reaction anytime to unsubscribe."
            ),
            color=discord.Color(PANEL_COLOR),
            timestamp=discord.utils.utcnow(),
        )

        for option in REACTION_ROLE_OPTIONS:
            role = guild.get_role(option.role_id)
            role_text = role.mention if role else f"`{option.role_id}`"
            embed.add_field(
                name=f"{option.emoji} {option.label}",
                value=f"{option.description}\nRole: {role_text}",
                inline=False,
            )

        embed.add_field(
            name="How It Works",
            value=(
                f"{YOUTUBE_EMOJI} adds or removes **YouTube Ping**.\n"
                f"{GIVEAWAY_EMOJI} adds or removes **Giveaway Ping**."
            ),
            inline=False,
        )
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.set_footer(text=f"{PANEL_MARKER} | React to update your pings")
        return embed

    def _looks_like_panel_message(self, message: discord.Message) -> bool:
        if not self.bot.user or message.author.id != self.bot.user.id:
            return False
        for embed in message.embeds:
            footer = embed.footer.text if embed.footer else ""
            if embed.title == "Notification Roles" and PANEL_MARKER in footer:
                return True
        return False

    async def _find_existing_panel(self, channel: discord.TextChannel) -> discord.Message | None:
        try:
            async for message in channel.history(limit=50):
                if self._looks_like_panel_message(message):
                    return message
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"[REACTION-ROLES] Could not scan panel history in {channel.id}: {exc}")
        return None

    async def _sync_panel_reactions(self, message: discord.Message) -> None:
        expected = {option.emoji for option in REACTION_ROLE_OPTIONS}
        channel = message.channel
        guild = message.guild
        can_manage_messages = False
        if isinstance(channel, discord.TextChannel) and guild is not None:
            me = self._bot_member(guild)
            can_manage_messages = bool(me and channel.permissions_for(me).manage_messages)

        for reaction in list(message.reactions):
            if str(reaction.emoji) not in expected and can_manage_messages:
                try:
                    await message.clear_reaction(reaction.emoji)
                except discord.HTTPException:
                    pass

        existing = {str(reaction.emoji) for reaction in message.reactions}
        for option in REACTION_ROLE_OPTIONS:
            if option.emoji in existing:
                continue
            try:
                await message.add_reaction(option.emoji)
            except (discord.Forbidden, discord.HTTPException) as exc:
                print(f"[REACTION-ROLES] Could not add {option.emoji} to panel {message.id}: {exc}")

    async def _upsert_reaction_panel(
        self,
        channel: discord.TextChannel,
        *,
        created_by_id: int | None = None,
    ) -> discord.Message | None:
        async with self._panel_lock:
            guild = channel.guild
            message: discord.Message | None = None
            record = await self._load_panel_record(guild.id)

            stored_message_id = record.get("message_id")
            if stored_message_id and int(record.get("channel_id") or channel.id) == channel.id:
                try:
                    message = await channel.fetch_message(int(stored_message_id))
                except discord.NotFound:
                    message = None
                except (discord.Forbidden, discord.HTTPException) as exc:
                    print(f"[REACTION-ROLES] Could not fetch stored panel message {stored_message_id}: {exc}")

            if message is None:
                message = await self._find_existing_panel(channel)

            embed = self._build_panel_embed(guild)
            if message is None:
                try:
                    message = await channel.send(
                        embed=embed,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except (discord.Forbidden, discord.HTTPException) as exc:
                    print(f"[REACTION-ROLES] Could not create panel in {channel.id}: {exc}")
                    return None
            else:
                try:
                    await message.edit(embed=embed, allowed_mentions=discord.AllowedMentions.none())
                except (discord.Forbidden, discord.HTTPException) as exc:
                    print(f"[REACTION-ROLES] Could not refresh panel {message.id}: {exc}")
                    return None

            await self._sync_panel_reactions(message)
            await self._save_panel_record(
                {
                    "guild_id": guild.id,
                    "channel_id": channel.id,
                    "message_id": message.id,
                    "created_by_id": created_by_id or record.get("created_by_id"),
                    "panel_type": "notification_roles",
                    "status": "active",
                }
            )
            return message

    def _start_reaction_panel_setup(self) -> None:
        if self._panel_task and not self._panel_task.done():
            return
        self._panel_task = asyncio.create_task(self._setup_reaction_panel())

    async def _setup_reaction_panel(self) -> None:
        await self.bot.wait_until_ready()
        channel = await self._fetch_panel_channel()
        if channel is None:
            return

        issues = self._panel_setup_issues(channel.guild, channel)
        if issues:
            print("[REACTION-ROLES] Panel setup needs attention: " + " | ".join(issues))
            return

        message = await self._upsert_reaction_panel(channel)
        if message:
            print(f"[REACTION-ROLES] Panel ready: {message.jump_url}")

    async def _is_panel_payload(self, payload: discord.RawReactionActionEvent) -> bool:
        if payload.guild_id is None or payload.channel_id != PING_ROLES_CHANNEL_ID:
            return False
        record = await self._load_panel_record(payload.guild_id)
        return bool(record.get("message_id") and int(record["message_id"]) == payload.message_id)

    async def _fetch_member(self, guild: discord.Guild, user_id: int) -> discord.Member | None:
        member = guild.get_member(user_id)
        if member is not None:
            return member
        try:
            return await guild.fetch_member(user_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    async def _remove_payload_reaction(self, payload: discord.RawReactionActionEvent) -> None:
        channel = self.bot.get_channel(payload.channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(payload.channel_id)
            except (discord.Forbidden, discord.HTTPException):
                return
        if not isinstance(channel, discord.TextChannel):
            return

        try:
            message = await channel.fetch_message(payload.message_id)
            user = self.bot.get_user(payload.user_id) or await self.bot.fetch_user(payload.user_id)
            await message.remove_reaction(payload.emoji, user)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            return

    async def _handle_panel_reaction(self, payload: discord.RawReactionActionEvent, *, added: bool) -> None:
        if self.bot.user and payload.user_id == self.bot.user.id:
            return
        if not await self._is_panel_payload(payload):
            return

        option = REACTION_ROLE_BY_EMOJI.get(str(payload.emoji))
        if option is None:
            if added:
                await self._remove_payload_reaction(payload)
            return

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return

        role = guild.get_role(option.role_id)
        if role is None or not self._can_manage_role(guild, role):
            print(f"[REACTION-ROLES] Cannot manage role for {option.label} in {guild.name}.")
            if added:
                await self._remove_payload_reaction(payload)
            return

        member = payload.member if added and isinstance(payload.member, discord.Member) else None
        if member is None:
            member = await self._fetch_member(guild, payload.user_id)
        if member is None or member.bot:
            return

        try:
            if added and role not in member.roles:
                await member.add_roles(role, reason=f"KerevizBOT notification role reaction: {option.label}")
            elif not added and role in member.roles:
                await member.remove_roles(role, reason=f"KerevizBOT notification role reaction removed: {option.label}")
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"[REACTION-ROLES] Could not update {option.label} for {member}: {exc}")

    async def _sync_members_from_reactions(
        self,
        guild: discord.Guild,
        message: discord.Message,
    ) -> PanelSyncResult:
        result = PanelSyncResult()
        roles_by_emoji: dict[str, discord.Role] = {}
        for option in REACTION_ROLE_OPTIONS:
            role = guild.get_role(option.role_id)
            if role is None:
                result.error = f"The {option.label} role was not found."
                return result
            if not self._can_manage_role(guild, role):
                result.error = f"I cannot manage {role.name}. Move my bot role above it."
                return result
            roles_by_emoji[option.emoji] = role

        for reaction in message.reactions:
            role = roles_by_emoji.get(str(reaction.emoji))
            if role is None:
                continue

            result.checked_reactions += 1
            try:
                async for user in reaction.users(limit=None):
                    if self.bot.user and user.id == self.bot.user.id:
                        continue
                    if user.bot:
                        result.skipped_bots += 1
                        continue
                    member = await self._fetch_member(guild, user.id)
                    if member is None:
                        result.missing_members += 1
                        continue
                    if role in member.roles:
                        result.already_had_role += 1
                        continue
                    try:
                        await member.add_roles(role, reason="KerevizBOT notification role reaction sync")
                        result.added += 1
                        await asyncio.sleep(ASSIGN_DELAY_SECONDS)
                    except (discord.Forbidden, discord.HTTPException):
                        result.failed += 1
            except discord.HTTPException:
                result.failed += 1

        return result

    async def _reaction_roles_status_embed(self, guild: discord.Guild) -> discord.Embed:
        channel = await self._fetch_panel_channel()
        record = await self._load_panel_record(guild.id)

        embed = discord.Embed(
            title="Reaction Role Panel",
            color=discord.Color(PANEL_COLOR),
            timestamp=discord.utils.utcnow(),
        )

        if channel is None or channel.guild.id != guild.id:
            embed.description = "The configured panel channel is not available in this server."
            embed.add_field(name="Configured Channel ID", value=f"`{PING_ROLES_CHANNEL_ID}`", inline=False)
            return embed

        message_id = record.get("message_id")
        if message_id:
            panel_link = f"https://discord.com/channels/{guild.id}/{channel.id}/{message_id}"
        else:
            panel_link = "Not created yet."

        role_lines = []
        for option in REACTION_ROLE_OPTIONS:
            role = guild.get_role(option.role_id)
            role_text = role.mention if role else f"`{option.role_id}`"
            role_lines.append(f"{option.emoji} **{option.label}** -> {role_text}")

        issues = self._panel_setup_issues(guild, channel)
        embed.add_field(name="Channel", value=channel.mention, inline=True)
        embed.add_field(name="Panel", value=panel_link, inline=False)
        embed.add_field(name="Roles", value="\n".join(role_lines), inline=False)
        embed.add_field(
            name="Health",
            value="Ready" if not issues else "\n".join(f"- {issue}" for issue in issues),
            inline=False,
        )
        embed.set_footer(text="Use !reactionroles post to create or refresh the panel.")
        return embed

    async def _ensure_member_role(self, guild: discord.Guild) -> tuple[discord.Role | None, str | None]:
        role = discord.utils.get(guild.roles, name=MEMBER_ROLE_NAME)
        if role:
            if not self._can_manage_role(guild, role):
                return role, "Member role is above my bot role, managed, or I do not have Manage Roles."
            return role, None

        me = self._bot_member(guild)
        if me is None or not me.guild_permissions.manage_roles:
            return None, "Member role does not exist and I do not have Manage Roles to create it."

        try:
            role = await guild.create_role(
                name=MEMBER_ROLE_NAME,
                color=discord.Color(ROLE_COLOR),
                hoist=False,
                mentionable=False,
                reason="KerevizBOT auto-role setup",
            )
            return role, None
        except discord.Forbidden:
            return None, "Discord blocked me from creating the Member role."
        except discord.HTTPException as exc:
            return None, f"Could not create Member role: {exc}"

    async def _iter_members(self, guild: discord.Guild):
        try:
            async for member in guild.fetch_members(limit=None):
                yield member
            return
        except (discord.Forbidden, discord.HTTPException):
            pass

        for member in guild.members:
            yield member

    async def _assign_role(self, member: discord.Member, role: discord.Role, reason: str) -> bool:
        if member.bot or role in member.roles:
            return False
        try:
            await member.add_roles(role, reason=reason)
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    async def sync_guild(self, guild: discord.Guild, *, reason: str) -> SyncResult:
        async with self._sync_lock:
            result = SyncResult()
            role, role_error = await self._ensure_member_role(guild)
            if role is None or role_error:
                result.error = role_error or "Member role could not be prepared."
                return result

            async for member in self._iter_members(guild):
                result.checked += 1
                if member.bot:
                    result.skipped_bots += 1
                    continue
                if role in member.roles:
                    result.already_had_role += 1
                    continue

                added = await self._assign_role(member, role, reason)
                if added:
                    result.added += 1
                    await asyncio.sleep(ASSIGN_DELAY_SECONDS)
                else:
                    result.failed += 1

            return result

    def _start_background_sync(self) -> None:
        if self._background_task and not self._background_task.done():
            return
        self._background_task = asyncio.create_task(self._sync_all_guilds())

    async def _sync_all_guilds(self) -> None:
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            result = await self.sync_guild(guild, reason="KerevizBOT startup Member auto-role sync")
            if result.error:
                print(f"[AUTO-ROLE] {guild.name}: {result.error}")
            else:
                print(
                    f"[AUTO-ROLE] {guild.name}: checked={result.checked}, "
                    f"added={result.added}, already={result.already_had_role}, failed={result.failed}"
                )

    @commands.Cog.listener()
    async def on_ready(self):
        self._start_background_sync()
        self._start_reaction_panel_setup()

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        role, role_error = await self._ensure_member_role(member.guild)
        if role is None or role_error:
            if role_error:
                print(f"[AUTO-ROLE] {member.guild.name}: {role_error}")
            return
        await self._assign_role(member, role, "KerevizBOT Member auto-role on join")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        await self._handle_panel_reaction(payload, added=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        await self._handle_panel_reaction(payload, added=False)

    @commands.group(name="autorole", invoke_without_command=True, help="Show the automatic Member role status.")
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    async def autorole(self, ctx: commands.Context):
        role = discord.utils.get(ctx.guild.roles, name=MEMBER_ROLE_NAME)
        if role is None:
            return await ctx.send("Auto-role is enabled, but the **Member** role does not exist yet. Use `!autorole sync` to create and sync it.")

        missing = sum(1 for member in ctx.guild.members if not member.bot and role not in member.roles)
        embed = discord.Embed(title="Auto Role: Member", color=discord.Color(ROLE_COLOR))
        embed.add_field(name="Role", value=role.mention, inline=True)
        embed.add_field(name="Missing in cache", value=str(missing), inline=True)
        embed.add_field(name="Startup Sync", value="Enabled", inline=True)
        embed.set_footer(text="Use !autorole sync to force a full member scan.")
        await ctx.send(embed=embed)

    @autorole.command(name="sync", help="Force-sync Member role to everyone missing it.")
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    async def autorole_sync(self, ctx: commands.Context):
        message = await ctx.send("Syncing **Member** role for everyone missing it...")
        result = await self.sync_guild(ctx.guild, reason=f"KerevizBOT Member auto-role sync by {ctx.author}")
        if result.error:
            return await message.edit(content=f"Auto-role sync failed: {result.error}")

        await message.edit(
            content=(
                "**Member** auto-role sync complete.\n"
                f"Checked: `{result.checked}` | Added: `{result.added}` | "
                f"Already had role: `{result.already_had_role}` | Failed: `{result.failed}`"
            )
        )

    @commands.group(
        name="reactionroles",
        aliases=["rr", "rolespanel"],
        invoke_without_command=True,
        help="Show the notification reaction-role panel status.",
    )
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    async def reactionroles(self, ctx: commands.Context):
        await ctx.send(embed=await self._reaction_roles_status_embed(ctx.guild))

    @reactionroles.command(name="post", aliases=["refresh"], help="Create or refresh the notification reaction-role panel.")
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    async def reactionroles_post(self, ctx: commands.Context):
        channel = await self._fetch_panel_channel()
        if channel is None or channel.guild.id != ctx.guild.id:
            return await ctx.send("The configured reaction-role channel is not available in this server.")

        issues = self._panel_setup_issues(ctx.guild, channel)
        if issues:
            return await ctx.send(
                "**I cannot publish the reaction-role panel yet:**\n" + "\n".join(f"- {issue}" for issue in issues)
            )

        message = await self._upsert_reaction_panel(channel, created_by_id=ctx.author.id)
        if message is None:
            return await ctx.send("I could not create or refresh the reaction-role panel. Check my channel permissions.")

        await ctx.send(f"Reaction-role panel is ready in {channel.mention}: {message.jump_url}")

    @reactionroles.command(name="sync", help="Grant missing roles from the current panel reactions.")
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    async def reactionroles_sync(self, ctx: commands.Context):
        channel = await self._fetch_panel_channel()
        if channel is None or channel.guild.id != ctx.guild.id:
            return await ctx.send("The configured reaction-role channel is not available in this server.")

        issues = self._panel_setup_issues(ctx.guild, channel)
        if issues:
            return await ctx.send(
                "**I cannot sync reaction roles yet:**\n" + "\n".join(f"- {issue}" for issue in issues)
            )

        panel_message = await self._upsert_reaction_panel(channel, created_by_id=ctx.author.id)
        if panel_message is None:
            return await ctx.send("I could not load the reaction-role panel.")

        status_message = await ctx.send("Syncing notification roles from panel reactions...")
        result = await self._sync_members_from_reactions(ctx.guild, panel_message)
        if result.error:
            return await status_message.edit(content=f"Reaction-role sync failed: {result.error}")

        await status_message.edit(
            content=(
                "**Reaction-role sync complete.**\n"
                f"Reaction groups checked: `{result.checked_reactions}` | Added: `{result.added}` | "
                f"Already had role: `{result.already_had_role}` | Bots skipped: `{result.skipped_bots}` | "
                f"Missing members: `{result.missing_members}` | Failed: `{result.failed}`"
            )
        )

    @autorole.error
    @autorole_sync.error
    async def autorole_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingPermissions):
            return await ctx.send("You need **Manage Roles** permission to use auto-role commands.")
        if isinstance(error, commands.NoPrivateMessage):
            return await ctx.send("Auto-role commands can only be used in a server.")
        raise error

    @reactionroles.error
    @reactionroles_post.error
    @reactionroles_sync.error
    async def reactionroles_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingPermissions):
            return await ctx.send("You need **Manage Roles** permission to use reaction-role commands.")
        if isinstance(error, commands.NoPrivateMessage):
            return await ctx.send("Reaction-role commands can only be used in a server.")
        raise error


async def setup(bot: commands.Bot):
    cog = AutoRole(bot)
    await cog.initialize()
    await bot.add_cog(cog)
    command = bot.get_command("autorole")
    if command:
        command.category = "Admin"
    reactionroles = bot.get_command("reactionroles")
    if reactionroles:
        reactionroles.category = "Admin"
    if bot.is_ready():
        cog._start_background_sync()
        cog._start_reaction_panel_setup()
