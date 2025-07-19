import discord
import asyncio
import os
import sys
import time
import platform
import psutil
import aiohttp  # reserved for future http tasks
import feedparser
from dotenv import load_dotenv
from discord.ext import commands  # <-- missing earlier, added now

load_dotenv()

# ===================== ENV VARIABLES =====================
HYPIXEL_API_KEY      = os.getenv("HYPIXEL_API_KEY")
DISCORD_TOKEN        = os.getenv("DISCORD_TOKEN")
YOUTUBE_CHANNEL_ID   = os.getenv("YOUTUBE_CHANNEL_ID")
DISCORD_CHANNEL_ID   = int(os.getenv("DISCORD_CHANNEL_ID"))
OWNER_ID             = int(os.getenv("OWNER_ID"))
LOG_CHANNEL_ID       = int(os.getenv("LOG_CHANNEL_ID"))
WELCOME_CHANNEL_ID   = int(os.getenv("WELCOME_CHANNEL_ID"))

# ===================== DISCORD SETUP =====================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)  # fixed constructor
bot.HYPIXEL_API_KEY = HYPIXEL_API_KEY

start_time        = time.time()
log_enabled       = True
announce_enabled  = False  # manual toggle, starts OFF
last_video_id     = None

# ===================== HELPER: LOG =====================
async def send_log(message: str):
    if not log_enabled:
        return
    ch = bot.get_channel(LOG_CHANNEL_ID)
    if ch:
        await ch.send(f"🛠️ {message}")
    print(message)

# ===================== ADMIN COMMANDS =====================
@bot.command(name="log", help="Toggle bot log messages on/off.")
async def cmd_log(ctx):
    global log_enabled
    if ctx.author.id != OWNER_ID:
        await ctx.send("⛔ You are not authorized to toggle logs.")
        return
    log_enabled = not log_enabled
    await ctx.send(f"🛠 Logs are now **{'enabled' if log_enabled else 'disabled'}**.")
cmd_log.category = "Admin"

@bot.command(name="restart", help="Restarts the bot (owner only).")
async def cmd_restart(ctx):
    if ctx.author.id != OWNER_ID:
        await ctx.send("⛔ You are not authorized to restart the bot.")
        return
    await ctx.send("🔄 Restarting bot…")
    await send_log("[INFO] Manual restart triggered by owner.")
    await bot.close()
    sys.exit()
cmd_restart.category = "Admin"

@bot.command(name="announce", help="Enable/Disable automatic YouTube announcements (owner only).")
async def cmd_announce(ctx):
    global announce_enabled
    if ctx.author.id != OWNER_ID:
        await ctx.send("⛔ You are not authorized to toggle announcements.")
        return
    announce_enabled = not announce_enabled
    await ctx.send(f"📣 YouTube announcements are now **{'enabled' if announce_enabled else 'disabled'}**.")
    await send_log(f"[CONFIG] Announce toggled: {'enabled' if announce_enabled else 'disabled'}")
cmd_announce.category = "Admin"

# ===================== GENERAL COMMANDS =====================
@bot.command(name="channel", help="Shows the official YouTube channel.")
async def cmd_channel(ctx):
    embed = discord.Embed(
        title="📺 Kereviz YouTube Channel",
        description="Check out the official YouTube channel for awesome content!",
        color=discord.Color.green())
    embed.add_field(name="Channel Link", value="https://www.youtube.com/@kerevizYT", inline=False)
    thumb_url = ("https://media.discordapp.net/attachments/1229049517790466178/1229049663500324905/"
                 "Kerevizzz.png?format=webp&quality=lossless")
    embed.set_thumbnail(url=thumb_url)
    embed.set_footer(text="Don't forget to like, comment and subscribe!")
    await ctx.send(embed=embed)
cmd_channel.category = "General"

@bot.command(name="stats", help="Shows detailed bot statistics.")
async def cmd_stats(ctx):
    up = int(time.time() - start_time)

    # ---- ⏱️ fancy uptime formatter ----
    years, rem = divmod(up, 31536000)   # 365*24*60*60
    days,  rem = divmod(rem, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)

    parts = []
    if years:
        parts.append(f"{years}y")
    if days:
        parts.append(f"{days}d")
    parts.append(f"{hours:02}h {minutes:02}m {seconds:02}s")
    pretty_up = " ".join(parts)
    # -----------------------------------

    embed = discord.Embed(title="⚙️ Bot Statistics", color=discord.Color.green())
    embed.add_field(name="Developer", value=f"<@{OWNER_ID}>", inline=True)
    embed.add_field(name="Uptime", value=pretty_up, inline=True)           # ← tek değişen burası
    embed.add_field(name="Ping", value=f"{round(bot.latency*1000)} ms", inline=True)
    embed.add_field(name="Servers", value=len(bot.guilds), inline=True)
    embed.add_field(name="Users", value=sum(g.member_count for g in bot.guilds), inline=True)
    embed.add_field(name="Commands", value=len(bot.commands), inline=True)
    embed.add_field(name="CPU Usage", value=f"{psutil.cpu_percent()}%", inline=True)
    embed.add_field(name="RAM Usage", value=f"{psutil.virtual_memory().percent}%", inline=True)
    embed.add_field(name="Python / discord.py",
                    value=f"{platform.python_version()} / {discord.__version__}", inline=True)
    embed.set_footer(text="Bot is alive and kicking! | Kereviz Bot")
    await ctx.send(embed=embed)
cmd_stats.category = "General"


# ===================== YOUTUBE LOOP =====================
async def youtube_loop():
    await bot.wait_until_ready()
    global last_video_id
    ch = bot.get_channel(DISCORD_CHANNEL_ID)

    # fetch recent message for last video id
    if ch:
        async for m in ch.history(limit=20):
            if "youtube.com/" in m.content:
                if "watch?v=" in m.content:
                    last_video_id = m.content.split("watch?v=")[-1].split("&")[0]
                elif "/shorts/" in m.content:
                    last_video_id = m.content.split("/shorts/")[-1].split("?")[0]
                break

    while not bot.is_closed():
        await asyncio.sleep(1200)
        if not announce_enabled:
            continue
        try:
            feed = feedparser.parse(
                f"https://www.youtube.com/feeds/videos.xml?channel_id={YOUTUBE_CHANNEL_ID}"
            )
            if not feed.entries:
                continue

            latest = feed.entries[0]
            link = latest.link
            vid = link.split("watch?v=")[-1] if "watch?v=" in link else link.split("/")[-1]

            if vid == last_video_id:
                await send_log("[INFO] Same video as last time – skipping.")
                continue

            last_video_id = vid
            if ch:
                await ch.send(f"@everyone 📢 A new video has just been uploaded!\n{link}")
                await send_log(f"[ANNOUNCED] {vid}")

        except Exception as e:
            await send_log(f"[ERROR] YouTube loop: {e}")

# ===================== EVENTS =====================
@bot.event
async def on_ready():
    print(f"Logged in as: {bot.user}")
    await bot.change_presence(activity=discord.Game(name="!help, !channel 💚"))
    # load possible cogs
    for fn in os.listdir("./commands"):
        if fn.endswith(".py"):
            try:
                await bot.load_extension(f"commands.{fn[:-3]}")
                await send_log(f"[EXT] Loaded: {fn}")
            except Exception as exc:
                await send_log(f"[EXT] Failed: {fn} -> {exc}")

    await bot.tree.sync()

    # schedule YouTube announce loop *after* client is ready (discord.py ≥2.3)
    asyncio.create_task(youtube_loop())

@bot.event
async def on_member_join(member):
    # — welcome message —
    channel = bot.get_channel(WELCOME_CHANNEL_ID)
    if channel:
        embed = discord.Embed(
            title=f"📥 Welcome {member.name}!",
            description=(
                f"Hey {member.mention}, welcome to **{member.guild.name}**!\n\n"
                "📸 Please send a screenshot showing you’re subscribed to **Kereviz** on YouTube.\n"
                "💬 Use `!help` any time if you need assistance!"
            ),
            color=discord.Color.green()
        )
        embed.set_thumbnail(url=member.avatar.url if member.avatar else member.default_avatar.url)
        embed.set_footer(text="Glad to have you here! | Kereviz Bot")
        await channel.send(embed=embed)

    # — DM notification —
    try:
        dm_embed = discord.Embed(
            title="🌟 Welcome to Kereviz Community!",
            description=(
                "Hey there!\n\n"
                "📺 **Quick favor:** Make sure you’re **subscribed** to "
                "[Kereviz YouTube](https://www.youtube.com/@kerevizYT) and then "
                "send a screenshot in the server so we can verify you and give "
                "you the **Subscriber** role.\n\n"
                "Need help? Just type `!help` anywhere in the server or reply here!"
            ),
            color=discord.Color.green()
        )
        dm_embed.set_thumbnail(
            url=member.avatar.url if member.avatar else member.default_avatar.url
        )
        dm_embed.set_footer(text="Glad to have you on board! – Kereviz Bot")
        await member.send(embed=dm_embed)

    except discord.Forbidden:
        await send_log(f"[DM] {member} has DMs closed; welcome DM skipped.")
    except Exception as e:
        await send_log(f"[DM ERROR] Could not DM {member}: {e}")

@bot.event
async def on_command_error(ctx, error):
    await ctx.send("❌ An error occurred while processing your command.")
    await send_log(f"[COMMAND ERROR] {error}")

# ===================== RUN =====================
if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN not set in environment variables!")

bot.run(DISCORD_TOKEN)