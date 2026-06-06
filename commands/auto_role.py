import asyncio
from dataclasses import dataclass

import discord
from discord.ext import commands


MEMBER_ROLE_NAME = "Member"
ROLE_COLOR = 0x57F287
ASSIGN_DELAY_SECONDS = 0.35


@dataclass
class SyncResult:
    added: int = 0
    skipped_bots: int = 0
    already_had_role: int = 0
    failed: int = 0
    checked: int = 0
    error: str | None = None


class AutoRole(commands.Cog):
    """Keeps every human member on the server synced with the Member role."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._sync_lock = asyncio.Lock()
        self._background_task: asyncio.Task | None = None

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

    @autorole.error
    @autorole_sync.error
    async def autorole_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingPermissions):
            return await ctx.send("You need **Manage Roles** permission to use auto-role commands.")
        if isinstance(error, commands.NoPrivateMessage):
            return await ctx.send("Auto-role commands can only be used in a server.")
        raise error


async def setup(bot: commands.Bot):
    cog = AutoRole(bot)
    await bot.add_cog(cog)
    command = bot.get_command("autorole")
    if command:
        command.category = "Admin"
    if bot.is_ready():
        cog._start_background_sync()
