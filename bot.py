import discord
import asyncio
import os
import re
import sys
import time
import platform
import psutil
import aiohttp
import xml.etree.ElementTree as ET
from dotenv import load_dotenv
from discord.ext import commands
from discord import app_commands
from services.blocked_commands import KEREVIZCRAFT_CATEGORY, KEREVIZCRAFT_COMMAND_NAMES
from services.youtube_store import YouTubeAnnouncementStore

# ===================== LOAD ENV =====================
load_dotenv()

# ===================== ENV =====================
HYPIXEL_API_KEY         = os.getenv("HYPIXEL_API_KEY")
DISCORD_TOKEN           = os.getenv("DISCORD_TOKEN")
YOUTUBE_CHANNEL_ID      = os.getenv("YOUTUBE_CHANNEL_ID")
DISCORD_CHANNEL_ID      = int(os.getenv("DISCORD_CHANNEL_ID")) if os.getenv("DISCORD_CHANNEL_ID") else None
OWNER_ID                = int(os.getenv("OWNER_ID")) if os.getenv("OWNER_ID") else None
LOG_CHANNEL_ID          = int(os.getenv("LOG_CHANNEL_ID")) if os.getenv("LOG_CHANNEL_ID") else None
WELCOME_CHANNEL_ID      = int(os.getenv("WELCOME_CHANNEL_ID")) if os.getenv("WELCOME_CHANNEL_ID") else None
LEAVES_LOG_CHANNEL_ID   = int(os.getenv("LEAVES_LOG_CHANNEL_ID")) if os.getenv("LEAVES_LOG_CHANNEL_ID") else None
MESSAGES_LOG_CHANNEL_ID = int(os.getenv("MESSAGES_LOG_CHANNEL_ID")) if os.getenv("MESSAGES_LOG_CHANNEL_ID") else None
GITHUB_URL              = os.getenv("GITHUB_URL")
POLLINATIONS_API_KEY    = os.getenv("POLLINATIONS_API_KEY")

# ===================== CONFIG =====================
ANNOUNCE_INTERVAL = 900  # seconds between YouTube feed checks
LAST_VIDEO_FILE   = "last_video_id.txt"
YOUTUBE_URL       = "https://www.youtube.com/@kerevizYT"

# YouTube RSS namespace map
YT_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt":   "http://www.youtube.com/xml/schemas/2015",
}
YT_CHANNEL_ID_RE = re.compile(r"UC[0-9A-Za-z_-]{22}")
YT_HANDLE_RE = re.compile(r"(?:youtube\.com/)?(@[A-Za-z0-9._-]+)", re.IGNORECASE)
YT_FEED_HEADERS = {
    "User-Agent": "KerevizBOT/1.0 (+https://github.com/janleague/KerevizBOT)",
    "Accept": "application/atom+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
}
YT_FEED_TIMEOUT = aiohttp.ClientTimeout(total=15)
YT_FEED_MAX_ATTEMPTS = 3
YT_FEED_RETRY_STATUSES = {404, 429, 500, 502, 503, 504}
YT_ERROR_LOG_COOLDOWN = 3600

# ===================== DISCORD SETUP =====================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
bot.HYPIXEL_API_KEY = HYPIXEL_API_KEY
bot.POLLINATIONS_API_KEY = POLLINATIONS_API_KEY

start_time       = time.time()
log_enabled      = True
announce_enabled = False
last_video_id    = None
extensions_loaded = False
slash_synced = False
youtube_task: asyncio.Task | None = None
announce_view_registered = False
youtube_store = YouTubeAnnouncementStore()
yt_feed_failure_count = 0
yt_feed_last_error_key: str | None = None
yt_feed_last_error_log_at = 0.0


def remove_kerevizcraft_commands() -> list[str]:
    removed: list[str] = []
    for command_name in sorted(KEREVIZCRAFT_COMMAND_NAMES):
        command = bot.remove_command(command_name)
        if command is not None:
            removed.append(command_name)
        try:
            app_command = bot.tree.remove_command(command_name)
            if app_command is not None and command_name not in removed:
                removed.append(command_name)
        except Exception:
            pass

    for command in list(bot.commands):
        if getattr(command, "category", None) == KEREVIZCRAFT_CATEGORY:
            removed_command = bot.remove_command(command.name)
            if removed_command is not None and command.name not in removed:
                removed.append(command.name)
    return removed

# ===================== HELPERS =====================
async def send_log(message: str) -> None:
    if not log_enabled:
        return
    print(message)
    if LOG_CHANNEL_ID:
        ch = bot.get_channel(LOG_CHANNEL_ID)
        if ch:
            try:
                await ch.send(f"🛠️ {message}")
            except discord.Forbidden:
                print("[WARNING] Missing perms to send logs.")
            except Exception as e:
                print(f"[ERROR] Failed to send log: {e}")


def human_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    out = []
    if d: out.append(f"{d}d")
    if h: out.append(f"{h}h")
    if m: out.append(f"{m}m")
    out.append(f"{s}s")
    return " ".join(out)


def progress_bar(pct: float, width: int = 12) -> str:
    pct = max(0.0, min(1.0, pct))
    filled = int(round(width * pct))
    return "█" * filled + "░" * (width - filled)


def _state_storage_label() -> str:
    return youtube_store.storage_label


async def _load_last_video_state() -> str | None:
    return await youtube_store.migrate_from_file(LAST_VIDEO_FILE)


async def _persist_last_video_state(vid: str | None) -> None:
    if vid:
        await youtube_store.set_last_video_id(vid)


def _extract_youtube_channel_id(source: str | None) -> str | None:
    if not source:
        return None
    match = YT_CHANNEL_ID_RE.search(source.strip())
    return match.group(0) if match else None


def _youtube_handle_url(source: str | None) -> str | None:
    if not source:
        return None
    raw_value = source.strip()
    match = YT_HANDLE_RE.search(raw_value)
    if not match:
        return None
    return f"https://www.youtube.com/{match.group(1)}"


async def _record_youtube_feed_failure(reason: str) -> None:
    global yt_feed_failure_count, yt_feed_last_error_key, yt_feed_last_error_log_at

    yt_feed_failure_count += 1
    now = time.time()
    key = reason.strip()
    should_log = (
        key != yt_feed_last_error_key
        or yt_feed_failure_count in {1, 3}
        or now - yt_feed_last_error_log_at >= YT_ERROR_LOG_COOLDOWN
    )
    if not should_log:
        return

    suffix = ""
    if yt_feed_failure_count > 1:
        suffix = f" (failure count: {yt_feed_failure_count}; repeated messages are rate-limited)"
    await send_log(f"[YT] Feed check failed: {key}.{suffix}")
    yt_feed_last_error_key = key
    yt_feed_last_error_log_at = now


async def _record_youtube_feed_success() -> None:
    global yt_feed_failure_count, yt_feed_last_error_key, yt_feed_last_error_log_at

    if yt_feed_failure_count:
        await send_log(f"[YT] Feed recovered after {yt_feed_failure_count} failed check(s).")
    yt_feed_failure_count = 0
    yt_feed_last_error_key = None
    yt_feed_last_error_log_at = 0.0


async def _resolve_youtube_channel_id(
    session: aiohttp.ClientSession,
    source: str | None,
    *,
    log_failures: bool = True,
) -> str | None:
    channel_id = _extract_youtube_channel_id(source)
    if channel_id:
        return channel_id

    handle_url = _youtube_handle_url(source)
    if not handle_url:
        if log_failures:
            await _record_youtube_feed_failure("invalid YouTube channel source")
        return None

    try:
        async with session.get(handle_url, timeout=YT_FEED_TIMEOUT) as resp:
            if resp.status != 200:
                if log_failures:
                    await _record_youtube_feed_failure(f"channel source resolve returned HTTP {resp.status}")
                return None
            page_text = await resp.text()
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        if log_failures:
            await _record_youtube_feed_failure(f"channel source resolve error: {type(exc).__name__}")
        return None

    channel_id = _extract_youtube_channel_id(page_text)
    if not channel_id and log_failures:
        await _record_youtube_feed_failure("could not resolve YouTube channel ID from source")
    return channel_id


async def _fetch_youtube_feed_text(
    session: aiohttp.ClientSession,
    feed_url: str,
    *,
    log_failures: bool = True,
) -> str | None:
    last_status: int | None = None
    last_error: str | None = None

    for attempt in range(1, YT_FEED_MAX_ATTEMPTS + 1):
        try:
            async with session.get(feed_url, timeout=YT_FEED_TIMEOUT) as resp:
                if resp.status == 200:
                    return await resp.text()
                last_status = resp.status
                last_error = None
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            last_error = type(exc).__name__
            last_status = None

        should_retry = (
            attempt < YT_FEED_MAX_ATTEMPTS
            and (last_status in YT_FEED_RETRY_STATUSES or last_error is not None)
        )
        if should_retry:
            await asyncio.sleep(min(2 * attempt, 6))
            continue
        break

    if not log_failures:
        return None
    if last_status is not None:
        await _record_youtube_feed_failure(f"HTTP {last_status}")
    elif last_error:
        await _record_youtube_feed_failure(f"network error: {last_error}")
    else:
        await _record_youtube_feed_failure("unknown fetch error")
    return None


async def _announce_video_once(channel, video_id: str, link: str, log_prefix: str) -> bool:
    global last_video_id

    if video_id == last_video_id:
        return False

    channel_id = getattr(channel, "id", None)
    if not await youtube_store.claim_video(video_id, channel_id):
        loaded_state = await _load_last_video_state()
        if loaded_state:
            last_video_id = loaded_state
        await send_log(f"[YT] Duplicate announcement prevented for {video_id}.")
        return False

    try:
        message = await channel.send(f"@everyone 📢 A new video has just been uploaded!\n{link}")
    except Exception as exc:
        await youtube_store.mark_failed(video_id, str(exc))
        raise

    await youtube_store.mark_sent(video_id, channel_id, getattr(message, "id", None))
    last_video_id = video_id
    await send_log(f"{log_prefix} {video_id}")
    return True


async def _fetch_latest_video() -> tuple[str, str] | None:
    """
    Fetch the YouTube RSS feed and return (video_id, video_url) of the
    latest entry, or None on failure.  Fully async — no blocking calls.
    """
    global YOUTUBE_CHANNEL_ID

    if not YOUTUBE_CHANNEL_ID:
        return None
    try:
        async with aiohttp.ClientSession(headers=YT_FEED_HEADERS) as session:
            channel_id = await _resolve_youtube_channel_id(session, YOUTUBE_CHANNEL_ID)
            if not channel_id:
                return None
            if channel_id != YOUTUBE_CHANNEL_ID:
                YOUTUBE_CHANNEL_ID = channel_id

            feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
            xml_text = await _fetch_youtube_feed_text(session, feed_url)
            if xml_text is None:
                return None

        root = ET.fromstring(xml_text)
        entry = root.find("atom:entry", YT_NS)
        if entry is None:
            await _record_youtube_feed_failure("feed has no entries")
            return None

        vid_elem  = entry.find("yt:videoId", YT_NS)
        link_elem = entry.find("atom:link",  YT_NS)

        vid  = vid_elem.text.strip()  if vid_elem  is not None else None
        link = link_elem.get("href")  if link_elem is not None else None

        if vid and link:
            await _record_youtube_feed_success()
            return vid, link
        await _record_youtube_feed_failure("could not extract videoId or link from feed entry")
        return None

    except ET.ParseError as e:
        await _record_youtube_feed_failure(f"XML parse error: {e}")
        return None
    except Exception as e:
        await _record_youtube_feed_failure(f"fetch error: {e}")
        return None

# ===================== ADMIN TEXT COMMANDS =====================
@bot.command(name="log", help="Toggle bot log messages on/off.")
async def cmd_log(ctx: commands.Context):
    global log_enabled
    if ctx.author.id != OWNER_ID:
        return await ctx.send("⛔ You are not authorized to toggle logs.")
    log_enabled = not log_enabled
    await ctx.send(f"🛠 Logs are now **{'enabled' if log_enabled else 'disabled'}**.")
cmd_log.category = "Admin"


@bot.command(name="restart", help="Restarts the bot (owner only).")
async def cmd_restart(ctx: commands.Context):
    if ctx.author.id != OWNER_ID:
        return await ctx.send("⛔ You are not authorized to restart the bot.")
    await ctx.send("🔄 Restarting bot…")
    await send_log("[INFO] Manual restart triggered by owner.")
    await bot.close()
    sys.exit()
cmd_restart.category = "Admin"

# ===================== GENERAL TEXT COMMANDS =====================
class ChannelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=120)
        self.add_item(discord.ui.Button(label="YouTube Channel", emoji="📺", url=YOUTUBE_URL))


@bot.command(name="channel", help="Shows the official YouTube channel.")
async def cmd_channel(ctx: commands.Context):
    embed = discord.Embed(
        title="📺 Kereviz YouTube Channel",
        description="Check out the official YouTube channel for awesome content!",
        color=discord.Color.green(),
    )
    embed.add_field(name="Channel Link", value=YOUTUBE_URL, inline=False)
    thumb_url = ("https://media.discordapp.net/attachments/1229049517790466178/1229049663500324905/"
                 "Kerevizzz.png?format=webp&quality=lossless")
    embed.set_thumbnail(url=thumb_url)
    embed.set_footer(text="Don't forget to like, comment and subscribe!")
    await ctx.send(embed=embed, view=ChannelView())
cmd_channel.category = "General"

# ===================== STATS =====================
class StatsView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=60)
        self.add_item(discord.ui.Button(label="YouTube", emoji="📺", url=YOUTUBE_URL))
        if GITHUB_URL:
            self.add_item(discord.ui.Button(label="GitHub", url=GITHUB_URL))

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):  # noqa: ARG002
        if interaction.user.id != OWNER_ID:
            return await interaction.response.send_message("⛔ Owner only.", ephemeral=True)
        try:
            await interaction.response.edit_message(embed=build_stats_embed(), view=StatsView())
        except discord.InteractionResponded:
            await interaction.edit_original_response(embed=build_stats_embed(), view=StatsView())


def build_stats_embed() -> discord.Embed:
    uptime  = human_time(time.time() - start_time)
    py      = platform.python_version()
    dpy     = discord.__version__
    ping    = int(bot.latency * 1000) if bot.latency else 0
    guilds  = len(bot.guilds)
    members = sum(g.member_count or 0 for g in bot.guilds)
    mem_used = mem_total = 0.0
    try:
        vm = psutil.virtual_memory()
        mem_used  = (vm.total - vm.available) / (1024 ** 3)
        mem_total = vm.total / (1024 ** 3)
    except Exception:
        pass
    e = discord.Embed(title="📊 Bot Statistics", color=discord.Color.green())
    e.add_field(name="Latency", value=f"{ping} ms")
    e.add_field(name="Uptime",  value=uptime)
    e.add_field(name="Servers", value=str(guilds))
    e.add_field(name="Users",   value=str(members))
    if mem_total:
        pct = mem_used / mem_total
        e.add_field(name="Memory",
                    value=f"{mem_used:.1f}/{mem_total:.1f} GB\n{progress_bar(pct)}",
                    inline=False)
    e.add_field(name="Python",     value=py)
    e.add_field(name="discord.py", value=dpy)
    if bot.user and bot.user.avatar:
        e.set_thumbnail(url=bot.user.avatar.url)
    e.set_footer(text="Use /announce to configure YouTube alerts | Kereviz Bot")
    return e


@bot.command(name="botstats", aliases=["binfo"], help="Show bot statistics and useful buttons")
async def botstats(ctx: commands.Context):
    await ctx.send(embed=build_stats_embed(), view=StatsView())
botstats.category = "General"

# ===================== ANNOUNCE SYSTEM (SLASH + BUTTONS) =====================
class AnnounceButtonsFix(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Enable Announce", style=discord.ButtonStyle.success, custom_id="enable_announce")
    async def enable_btn(self, interaction: discord.Interaction, button: discord.ui.Button):  # noqa: ARG002
        global announce_enabled
        if interaction.user.id != OWNER_ID:
            return await interaction.response.send_message("⛔ Owner only.", ephemeral=True)
        announce_enabled = True
        await interaction.response.send_message("✅ Announcements **enabled**.", ephemeral=True)
        await send_log("[CONFIG] Announcements enabled via panel button.")

    @discord.ui.button(label="Disable Announce", style=discord.ButtonStyle.danger, custom_id="disable_announce")
    async def disable_btn(self, interaction: discord.Interaction, button: discord.ui.Button):  # noqa: ARG002
        global announce_enabled
        if interaction.user.id != OWNER_ID:
            return await interaction.response.send_message("⛔ Owner only.", ephemeral=True)
        announce_enabled = False
        await interaction.response.send_message("✅ Announcements **disabled**.", ephemeral=True)
        await send_log("[CONFIG] Announcements disabled via panel button.")


@bot.tree.command(name="announce", description="Manage YouTube announcements (owner only).")
@app_commands.describe(
    action="enable/disable/status/set_channel/set_rss/set_freq/set_last/force_check",
    value="Value for the chosen action",
)
async def announce(interaction: discord.Interaction, action: str, value: str | None = None):
    global announce_enabled, DISCORD_CHANNEL_ID, YOUTUBE_CHANNEL_ID, ANNOUNCE_INTERVAL, last_video_id

    if interaction.user.id != OWNER_ID:
        return await interaction.response.send_message("⛔ You are not authorized to use this.", ephemeral=True)

    action = action.lower()

    if action == "enable":
        announce_enabled = True
        e = discord.Embed(title="📣 Announcements Enabled",
                          description="YouTube announcements are now **enabled**.",
                          color=discord.Color.green())
        await interaction.response.send_message(embed=e, view=AnnounceButtonsFix())
        await send_log("[CONFIG] Announcements enabled via slash command.")
        return

    if action == "disable":
        announce_enabled = False
        e = discord.Embed(title="📣 Announcements Disabled",
                          description="YouTube announcements are now **disabled**.",
                          color=discord.Color.green())
        await interaction.response.send_message(embed=e, view=AnnounceButtonsFix())
        await send_log("[CONFIG] Announcements disabled via slash command.")
        return

    if action == "status":
        ch_mention = f"<#{DISCORD_CHANNEL_ID}>" if DISCORD_CHANNEL_ID else "Not set"
        e = discord.Embed(title="📊 Announcement Status", color=discord.Color.green())
        e.add_field(name="Status",                 value=("Enabled" if announce_enabled else "Disabled"), inline=False)
        e.add_field(name="Announce Channel",        value=ch_mention,                                     inline=False)
        e.add_field(name="State Storage",           value=_state_storage_label(),                          inline=False)
        e.add_field(name="YouTube Channel ID",      value=(YOUTUBE_CHANNEL_ID or "Not set"),              inline=False)
        e.add_field(name="Check Interval",          value=f"{ANNOUNCE_INTERVAL} seconds",                 inline=False)
        e.add_field(name="Last Announced Video ID", value=(last_video_id or "None"),                      inline=False)
        return await interaction.response.send_message(embed=e, ephemeral=True, view=AnnounceButtonsFix())

    if action == "set_channel":
        try:
            DISCORD_CHANNEL_ID = int(value) if value else None
            await interaction.response.send_message(f"✅ Announcement channel set to <#{DISCORD_CHANNEL_ID}>.",
                                                    view=AnnounceButtonsFix())
            await send_log(f"[CONFIG] Announce channel set to {DISCORD_CHANNEL_ID} via slash.")
        except Exception:
            await interaction.response.send_message("Invalid channel ID.", ephemeral=True)
        return

    if action == "set_rss":
        if not value:
            return await interaction.response.send_message("Provide a YouTube channel ID, handle, or channel URL.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        async with aiohttp.ClientSession(headers=YT_FEED_HEADERS) as session:
            resolved_channel_id = await _resolve_youtube_channel_id(session, value, log_failures=False)
            if not resolved_channel_id:
                return await interaction.followup.send(
                    "❌ I could not validate that YouTube channel source. Use a channel ID, `@handle`, or channel URL.",
                    ephemeral=True,
                )

            feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={resolved_channel_id}"
            xml_text = await _fetch_youtube_feed_text(session, feed_url, log_failures=False)
            if xml_text is None:
                return await interaction.followup.send(
                    "❌ The channel resolved, but the YouTube feed did not respond successfully. Try again later.",
                    ephemeral=True,
                )

        YOUTUBE_CHANNEL_ID = resolved_channel_id
        await interaction.followup.send("✅ YouTube channel ID set and validated.", ephemeral=True, view=AnnounceButtonsFix())
        await send_log("[CONFIG] YouTube channel ID validated and updated via slash.")
        return

    if action == "set_freq":
        try:
            v = int(value) if value else ANNOUNCE_INTERVAL
            if v < 30:
                return await interaction.response.send_message("Interval too low.", ephemeral=True)
            ANNOUNCE_INTERVAL = v
            await interaction.response.send_message(f"✅ Interval set to {ANNOUNCE_INTERVAL} seconds.",
                                                    view=AnnounceButtonsFix())
            await send_log(f"[CONFIG] Announce interval set to {ANNOUNCE_INTERVAL} via slash.")
        except Exception:
            await interaction.response.send_message("Invalid interval.", ephemeral=True)
        return

    if action == "set_last":
        if not value:
            return await interaction.response.send_message("Provide a video ID or link.", ephemeral=True)
        # Accept full URL or raw ID
        if "watch?v=" in value:
            vid = value.split("watch?v=")[-1].split("&")[0]
        elif "/shorts/" in value:
            vid = value.split("/shorts/")[-1].split("?")[0]
        else:
            vid = value.strip()
        last_video_id = vid
        await _persist_last_video_state(vid)
        await interaction.response.send_message(f"✅ last_video_id set to `{last_video_id}`.", ephemeral=True)
        await send_log(f"[CONFIG] last_video_id manually set to {last_video_id}")
        return

    if action == "force_check":
        if not YOUTUBE_CHANNEL_ID:
            return await interaction.response.send_message("YOUTUBE_CHANNEL_ID is not set.", ephemeral=True)
        if not DISCORD_CHANNEL_ID:
            return await interaction.response.send_message("Announcement channel is not set.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        result = await _fetch_latest_video()
        if result is None:
            return await interaction.followup.send("❌ Failed to fetch YouTube feed.", ephemeral=True)

        vid, link = result
        ch = bot.get_channel(DISCORD_CHANNEL_ID)
        if ch is None:
            return await interaction.followup.send("❌ Announcement channel not found.", ephemeral=True)

        if vid != last_video_id:
            try:
                sent = await _announce_video_once(ch, vid, link, "[FORCE ANNOUNCED] New video:")
            except Exception as exc:
                await send_log(f"[YT] Force check failed: {exc}")
                return await interaction.followup.send("❌ Force check failed while saving or sending the announcement.", ephemeral=True)
            if not sent:
                return await interaction.followup.send("Duplicate announcement prevented. No message was sent.", ephemeral=True)
            return await interaction.followup.send("✅ Force check done: announcement sent.", ephemeral=True)
        else:
            return await interaction.followup.send("No new video yet (feed matches last_video_id).", ephemeral=True)

    await interaction.response.send_message("Unknown action.", ephemeral=True)

# ===================== OWNER COOLDOWN BYPASS =====================
@bot.listen("on_command_completion")
async def _owner_bypass(ctx: commands.Context):
    if OWNER_ID and ctx.author.id == OWNER_ID:
        try:
            ctx.command.reset_cooldown(ctx)  # type: ignore[attr-defined]
        except Exception:
            pass

# ===================== YOUTUBE LOOP =====================
async def youtube_loop():
    """
    Polls the YouTube RSS feed every ANNOUNCE_INTERVAL seconds.
    Uses aiohttp for fully async HTTP — no blocking calls.
    A lock prevents concurrent checks from double-posting.
    """
    global last_video_id
    await bot.wait_until_ready()
    _lock = asyncio.Lock()

    while not bot.is_closed():
        await asyncio.sleep(ANNOUNCE_INTERVAL)   # wait FIRST, then check (avoids instant post on restart)

        if not announce_enabled:
            continue

        async with _lock:
            try:
                ch = bot.get_channel(DISCORD_CHANNEL_ID) if DISCORD_CHANNEL_ID else None
                if ch is None:
                    await send_log("[YT] Announcement channel not found.")
                    continue

                result = await _fetch_latest_video()
                if result is None:
                    continue  # error already logged inside _fetch_latest_video

                vid, link = result

                if vid == last_video_id:
                    await send_log("✅ [YT] No new video.")
                    continue

                await _announce_video_once(ch, vid, link, "[YT] Announced:")

            except Exception as e:
                await send_log(f"[YT] Unexpected error in loop: {e}")

# ===================== EVENTS =====================
@bot.event
async def on_ready():
    global extensions_loaded, slash_synced, youtube_task, announce_view_registered, last_video_id

    print(f"Logged in as: {bot.user}")
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="YouTube: @kerevizYT"))

    if last_video_id is None:
        try:
            last_video_id = await _load_last_video_state()
        except Exception as exc:
            await send_log(f"[YT] Could not load Firebase state: {exc}")

    # Load command extensions
    if not extensions_loaded:
        for folder, prefix in (("./commands", "commands"), ("./commands/fun", "commands.fun")):
            if os.path.isdir(folder):
                for fn in os.listdir(folder):
                    if fn.endswith(".py"):
                        if "kerevizcraft" in fn.lower():
                            await send_log(f"[EXT] Skipped removed KerevizCraft module: {fn}")
                            continue
                        module = f"{prefix}.{fn[:-3]}"
                        if module in bot.extensions:
                            continue
                        try:
                            await bot.load_extension(module)
                            await send_log(f"[EXT] Loaded: {('fun/' if 'fun' in prefix else '')}{fn}")
                        except Exception as exc:
                            await send_log(f"[EXT] Failed: {fn} -> {exc}")
        extensions_loaded = True

    removed_kerevizcraft = remove_kerevizcraft_commands()
    if removed_kerevizcraft:
        await send_log(f"[EXT] Removed KerevizCraft commands: {', '.join(removed_kerevizcraft)}")

    # Re-attach persistent views
    if not announce_view_registered:
        bot.add_view(AnnounceButtonsFix())
        announce_view_registered = True

    # Sync slash commands
    if not slash_synced:
        try:
            await bot.tree.sync()
            slash_synced = True
            await send_log("[SLASH] Command tree synced.")
        except Exception as e:
            await send_log(f"[SLASH] Sync error: {e}")

    if youtube_task is None or youtube_task.done():
        youtube_task = asyncio.create_task(youtube_loop())


@bot.event
async def on_member_join(member: discord.Member):
    channel = bot.get_channel(WELCOME_CHANNEL_ID) if WELCOME_CHANNEL_ID else None
    if channel:
        e = discord.Embed(
            title=f"📥 Welcome {member.name}!",
            description=(
                f"Hey {member.mention}, welcome to **{member.guild.name}**!\n\n"
                "📸 Please send a screenshot showing you're subscribed to **Kereviz** on YouTube.\n"
                "💬 Use `!help` any time if you need assistance!"
            ),
            color=discord.Color.green(),
        )
        e.set_thumbnail(url=member.avatar.url if member.avatar else member.default_avatar.url)
        e.set_footer(text="Glad to have you here! | Kereviz Bot")
        await channel.send(embed=e)
    try:
        dm = discord.Embed(
            title="🌟 Welcome to Kereviz Community!",
            description=(
                "Hey there!\n\n"
                "📺 **Quick favor:** Make sure you're **subscribed** to "
                f"[Kereviz YouTube]({YOUTUBE_URL}) and then "
                "send a screenshot in the server so we can verify you and give you the **Subscriber** role.\n\n"
                "Need help? Just type `!help` anywhere in the server or reply here!"
            ),
            color=discord.Color.green(),
        )
        dm.set_thumbnail(url=member.avatar.url if member.avatar else member.default_avatar.url)
        dm.set_footer(text="Glad to have you on board! – Kereviz Bot")
        await member.send(embed=dm)
    except discord.Forbidden:
        await send_log(f"[DM] {member} has DMs closed; welcome DM skipped.")
    except Exception as e:
        await send_log(f"[DM ERROR] Could not DM {member}: {e}")


@bot.event
async def on_message_delete(message: discord.Message):
    if not message.guild or message.author.bot:
        return
    ch = bot.get_channel(MESSAGES_LOG_CHANNEL_ID) if MESSAGES_LOG_CHANNEL_ID else None
    if not ch:
        return
    try:
        e = discord.Embed(
            title="🗑 Message Deleted",
            description=(message.content[:4000] if message.content else "*(no content)*"),
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow(),
        )
        e.add_field(name="Channel", value=message.channel.mention if hasattr(message.channel, "mention") else str(message.channel), inline=False)
        e.add_field(name="Author",  value=f"{message.author} (ID: {message.author.id})",                                                       inline=False)
        if message.attachments:
            files_list = "\n".join(a.url for a in message.attachments[:5])
            e.add_field(name="Attachments", value=files_list, inline=False)
        e.set_author(name=str(message.author),
                     icon_url=message.author.display_avatar.url if hasattr(message.author, "display_avatar") else None)
        e.set_footer(text=f"Message ID: {message.id}")
        await ch.send(embed=e)
    except Exception as e:
        await send_log(f"[MSG-DELETE-LOG ERROR] {e}")


@bot.event
async def on_member_remove(member: discord.Member):
    ch = bot.get_channel(LEAVES_LOG_CHANNEL_ID) if LEAVES_LOG_CHANNEL_ID else None
    if not ch:
        return
    try:
        e = discord.Embed(
            title="📤 Member Left",
            description=f"{member.mention} has left the server.",
            color=discord.Color.orange(),
            timestamp=discord.utils.utcnow(),
        )
        e.set_author(name=str(member),
                     icon_url=member.display_avatar.url if hasattr(member, "display_avatar") else None)
        e.add_field(name="User ID", value=member.id, inline=False)
        e.set_footer(text=f"Joined at: {member.joined_at}")
        await ch.send(embed=e)
    except Exception as e:
        await send_log(f"[MEMBER-LEAVE-LOG ERROR] {e}")


@bot.event
async def on_command_error(ctx: commands.Context, error: Exception):
    try:
        await ctx.send("❌ An error occurred while processing your command.")
    finally:
        await send_log(f"[COMMAND ERROR] {error}")

# ===================== !s — Give Subscriber Role =====================
@bot.command(name="s", help="Give the subscriber role to a user by reply or mention. (Owner only)")
@commands.guild_only()
async def s(ctx: commands.Context):
    if ctx.author.id != OWNER_ID:
        return await ctx.send("⛔ Only the bot owner can use this command.", delete_after=5)

    target = None
    if ctx.message.reference:
        try:
            replied_msg = await ctx.channel.fetch_message(ctx.message.reference.message_id)
            target = replied_msg.author
        except Exception:
            pass
    if not target and ctx.message.mentions:
        target = ctx.message.mentions[0]
    if not target:
        return await ctx.send("❌ Please reply to a message or mention a user.", delete_after=6)

    sub_role = discord.utils.get(ctx.guild.roles, name="Subscriber")
    if not sub_role:
        return await ctx.send("❌ Role **Subscriber** not found in this server.", delete_after=6)

    try:
        await target.add_roles(sub_role, reason=f"Granted by bot owner {ctx.author}")
    except discord.Forbidden:
        return await ctx.send("❌ I don't have permission to give that role.", delete_after=6)
    except Exception as e:
        return await ctx.send(f"❌ An unexpected error occurred: `{e}`", delete_after=6)

    embed = discord.Embed(
        title="🎉 Subscriber Role Granted",
        description=(
            f"Successfully granted the **Subscriber** role to:\n"
            f"**{target.name}**"
        ),
        color=discord.Color.green(),
    )
    embed.set_footer(text=f"Granted by {ctx.author}", icon_url=ctx.author.display_avatar.url)
    await ctx.send(embed=embed)
s.category = "Admin"

# ===================== RUN =====================
if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN not set in environment variables!")

bot.run(DISCORD_TOKEN)
