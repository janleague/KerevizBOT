import asyncio
import os
import time
from datetime import timezone

import discord
from discord.ext import commands

from services.firestore_storage_monitor import (
    DEFAULT_LIMIT_BYTES,
    DEFAULT_RESET_PERCENT,
    DEFAULT_THRESHOLDS,
    METRIC_TYPE,
    FirestoreMonitoringError,
    FirestoreMonitoringPermissionError,
    FirestoreStorageAlertStore,
    FirestoreStorageMonitorClient,
    FirestoreStorageUsage,
    classify_threshold,
    format_bytes,
    parse_thresholds,
    should_reset_alert,
    should_send_permission_alert,
    should_send_threshold_alert,
)


DEFAULT_ALERT_CHANNEL_ID = 1521808241233760337
DEFAULT_CHECK_INTERVAL_SECONDS = 6 * 60 * 60


def _env_int(name: str, default: int | None = None) -> int | None:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        return int(value.strip().strip('"'))
    except ValueError:
        return default


def _severity_for_level(level: int | None) -> tuple[str, discord.Color]:
    if level is None or level < 70:
        return "Healthy", discord.Color.green()
    if level >= 95:
        return "Critical", discord.Color.red()
    if level >= 85:
        return "High Risk", discord.Color.orange()
    return "Warning", discord.Color.gold()


class FirestoreStorageMonitor(commands.Cog):
    """Monitor Firestore storage usage and alert before the free tier fills up."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.owner_id = _env_int("OWNER_ID")
        self.alert_channel_id = int(_env_int("FIRESTORE_ALERT_CHANNEL_ID", DEFAULT_ALERT_CHANNEL_ID) or DEFAULT_ALERT_CHANNEL_ID)
        self.project_id = (os.getenv("FIREBASE_PROJECT_ID") or "").strip()
        self.limit_bytes = int(_env_int("FIRESTORE_STORAGE_LIMIT_BYTES", DEFAULT_LIMIT_BYTES) or DEFAULT_LIMIT_BYTES)
        self.thresholds = parse_thresholds(os.getenv("FIRESTORE_STORAGE_WARN_THRESHOLDS"), DEFAULT_THRESHOLDS)
        self.check_interval = int(
            _env_int("FIRESTORE_STORAGE_CHECK_INTERVAL", DEFAULT_CHECK_INTERVAL_SECONDS)
            or DEFAULT_CHECK_INTERVAL_SECONDS
        )
        self.store = FirestoreStorageAlertStore()
        self.client = FirestoreStorageMonitorClient(project_id=self.project_id, limit_bytes=self.limit_bytes)
        self._task: asyncio.Task | None = None

    def cog_unload(self):
        if self._task and not self._task.done():
            self._task.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        self._start_monitor()

    def _start_monitor(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._monitor_loop())

    async def _monitor_loop(self) -> None:
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                await self.check_and_alert()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[FIRESTORE-MONITOR] Unexpected monitor error: {exc}")
            await asyncio.sleep(max(300, self.check_interval))

    async def _is_owner(self, ctx: commands.Context) -> bool:
        if self.owner_id and ctx.author.id == self.owner_id:
            return True
        try:
            return await self.bot.is_owner(ctx.author)
        except Exception:
            return False

    async def _alert_channel(self) -> discord.TextChannel | None:
        channel = self.bot.get_channel(self.alert_channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(self.alert_channel_id)
            except Exception as exc:
                print(f"[FIRESTORE-MONITOR] Could not fetch alert channel {self.alert_channel_id}: {exc}")
                return None
        if not isinstance(channel, discord.TextChannel):
            print(f"[FIRESTORE-MONITOR] Alert channel is not a text channel: {self.alert_channel_id}")
            return None
        return channel

    def _owner_ping(self) -> str | None:
        return f"<@{self.owner_id}>" if self.owner_id else None

    def _usage_embed(self, usage: FirestoreStorageUsage, level: int | None, *, test: bool = False) -> discord.Embed:
        severity, color = _severity_for_level(level)
        title = f"{'[TEST] ' if test else ''}Firestore Storage {severity}"
        embed = discord.Embed(
            title=title,
            description="Cloud Firestore storage usage has reached a monitored threshold.",
            color=color,
        )
        embed.add_field(name="Usage", value=f"{usage.percent:.2f}%", inline=True)
        embed.add_field(name="Used", value=format_bytes(usage.used_bytes), inline=True)
        embed.add_field(name="Limit", value=format_bytes(usage.limit_bytes), inline=True)
        embed.add_field(name="Threshold", value=f"{level}%" if level else "None", inline=True)
        embed.add_field(name="Databases", value=str(usage.database_count), inline=True)
        embed.add_field(name="Project", value=f"`{self.project_id or 'not configured'}`", inline=True)
        embed.add_field(name="Metric", value=f"`{usage.metric_type}`", inline=False)
        embed.add_field(
            name="Action",
            value="Review Firestore usage, clean old data, or raise the configured storage limit if billing changes.",
            inline=False,
        )
        if usage.measured_at:
            measured = usage.measured_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            embed.set_footer(text=f"Measured at {measured} | Checks every {self.check_interval // 3600}h")
        else:
            embed.set_footer(text=f"Checks every {self.check_interval // 3600}h")
        return embed

    def _permission_embed(self, error: Exception) -> discord.Embed:
        embed = discord.Embed(
            title="Firestore Storage Monitor Setup Required",
            description="I could not read Cloud Monitoring metrics for Firestore storage.",
            color=discord.Color.orange(),
        )
        embed.add_field(name="Project", value=f"`{self.project_id or 'not configured'}`", inline=True)
        embed.add_field(name="Required IAM Role", value="`roles/monitoring.viewer`", inline=True)
        embed.add_field(
            name="What to do",
            value="Grant Monitoring Viewer to the Firebase service account used by this bot, then run `!firestoreusage` again.",
            inline=False,
        )
        embed.add_field(name="Error", value=str(error)[:1000], inline=False)
        embed.set_footer(text="Kereviz Firestore Monitor")
        return embed

    def _status_embed(self, usage: FirestoreStorageUsage) -> discord.Embed:
        level = classify_threshold(usage.percent, self.thresholds)
        severity, color = _severity_for_level(level)
        embed = discord.Embed(
            title="Firestore Storage Usage",
            description=f"Current status: **{severity}**",
            color=color,
        )
        embed.add_field(name="Usage", value=f"{usage.percent:.2f}%", inline=True)
        embed.add_field(name="Used", value=format_bytes(usage.used_bytes), inline=True)
        embed.add_field(name="Limit", value=format_bytes(usage.limit_bytes), inline=True)
        embed.add_field(name="Thresholds", value=", ".join(f"{item}%" for item in self.thresholds), inline=True)
        embed.add_field(name="Reset Below", value=f"{DEFAULT_RESET_PERCENT:.0f}%", inline=True)
        embed.add_field(name="Alert Channel", value=f"<#{self.alert_channel_id}>", inline=True)
        embed.add_field(name="Metric", value=f"`{METRIC_TYPE}`", inline=False)
        return embed

    async def _send_usage_alert(self, usage: FirestoreStorageUsage, level: int, *, test: bool = False) -> bool:
        channel = await self._alert_channel()
        if channel is None:
            return False
        content = self._owner_ping()
        await channel.send(
            content=content,
            embed=self._usage_embed(usage, level, test=test),
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )
        return True

    async def _send_permission_alert(self, error: Exception) -> bool:
        channel = await self._alert_channel()
        if channel is None:
            return False
        content = self._owner_ping()
        await channel.send(
            content=content,
            embed=self._permission_embed(error),
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )
        return True

    async def check_and_alert(self, *, force_permission_alert: bool = False) -> FirestoreStorageUsage | None:
        try:
            usage = await self.client.fetch_usage()
        except FirestoreMonitoringPermissionError as exc:
            state = await self.store.load_state()
            current_ts = int(time.time())
            if force_permission_alert or should_send_permission_alert(state.get("last_permission_alert_at"), current_ts):
                if await self._send_permission_alert(exc):
                    await self.store.record_permission_alert(current_ts, str(exc))
            return None
        except FirestoreMonitoringError as exc:
            print(f"[FIRESTORE-MONITOR] {exc}")
            return None

        state = await self.store.load_state()
        current_level = classify_threshold(usage.percent, self.thresholds)
        last_level = state.get("last_alerted_level")

        if should_reset_alert(usage.percent) and last_level is not None:
            await self.store.reset_threshold_alert(usage)
            return usage

        if should_send_threshold_alert(current_level, last_level):
            if await self._send_usage_alert(usage, int(current_level)):
                await self.store.record_threshold_alert(int(current_level), usage)

        return usage

    @commands.command(name="firestoreusage", aliases=["fsusage"], help="Show Firestore storage usage or send a test alert.")
    async def firestoreusage(self, ctx: commands.Context, action: str | None = None):
        if not await self._is_owner(ctx):
            return await ctx.send("Only the bot owner can use this command.", delete_after=5)

        if action and action.lower() == "test":
            usage = FirestoreStorageUsage(
                used_bytes=int(self.limit_bytes * 0.72),
                limit_bytes=self.limit_bytes,
                percent=72.0,
                measured_at=None,
                database_count=1,
            )
            sent = await self._send_usage_alert(usage, 70, test=True)
            return await ctx.send("Test Firestore storage alert sent." if sent else "I could not send the test alert.")

        try:
            usage = await self.client.fetch_usage()
        except FirestoreMonitoringPermissionError as exc:
            await self.check_and_alert(force_permission_alert=True)
            return await ctx.send(embed=self._permission_embed(exc))
        except FirestoreMonitoringError as exc:
            return await ctx.send(f"Firestore storage check failed: {exc}")

        await ctx.send(embed=self._status_embed(usage))


async def setup(bot: commands.Bot):
    cog = FirestoreStorageMonitor(bot)
    await bot.add_cog(cog)
    command = bot.get_command("firestoreusage")
    if command:
        command.category = "Admin"
    if bot.is_ready():
        cog._start_monitor()
