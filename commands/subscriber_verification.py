import asyncio
import os
import secrets
from datetime import datetime, timezone
from typing import Any

import discord
from discord.ext import commands

from services.subscriber_verification_store import (
    SubscriberVerificationStore,
    normalize_panel,
    normalize_request,
)


SUBSCRIBER_ROLE_ID = 1356275389592502513
PANEL_CHANNEL_ID = 1136310625056858153
PUBLIC_LOG_CHANNEL_ID = 1521420636231172140
PRIVATE_REVIEW_CHANNEL_ID = 1521434258475061339
YOUTUBE_CHANNEL_URL = "https://www.youtube.com/@kerevizYT"
SUBMISSION_COOLDOWN_SECONDS = 24 * 60 * 60
REQUEST_RETENTION_SECONDS = 30 * 24 * 60 * 60
CLEANUP_INTERVAL_SECONDS = 24 * 60 * 60
MAX_SCREENSHOT_SIZE_BYTES = 8 * 1024 * 1024
ALLOWED_SCREENSHOT_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif")

PANEL_COLOR = 0x5865F2
PENDING_COLOR = 0xFEE75C
APPROVED_COLOR = 0x57F287
REJECTED_COLOR = 0xED4245

PANEL_MARKER = "Kereviz Subscriber Verification"
PANEL_BUTTON_CUSTOM_ID = "kereviz_sub_verify_open"
ACCEPT_CUSTOM_ID = "kereviz_sub_verify_accept"
REJECT_CUSTOM_ID = "kereviz_sub_verify_reject"


def now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def is_supported_image_file(filename: str | None, content_type: str | None) -> bool:
    normalized_type = (content_type or "").lower()
    if normalized_type.startswith("image/"):
        return True
    normalized_name = (filename or "").lower()
    return normalized_name.endswith(ALLOWED_SCREENSHOT_EXTENSIONS)


def valid_http_url(value: str) -> bool:
    normalized = value.strip().lower()
    return normalized.startswith("https://") or normalized.startswith("http://")


def seconds_until_next_submission(
    requests: dict[str, dict[str, Any]],
    guild_id: int,
    user_id: int,
    *,
    current_ts: int | None = None,
) -> int:
    current_ts = now_ts() if current_ts is None else current_ts
    remaining_seconds = 0
    for record in requests.values():
        if record.get("guild_id") != guild_id or record.get("user_id") != user_id:
            continue
        try:
            created_at = int(record.get("created_at") or 0)
        except (TypeError, ValueError):
            continue
        elapsed = max(0, current_ts - created_at)
        if elapsed < SUBMISSION_COOLDOWN_SECONDS:
            remaining_seconds = max(remaining_seconds, SUBMISSION_COOLDOWN_SECONDS - elapsed)
    return remaining_seconds


def format_cooldown(seconds: int) -> str:
    seconds = max(1, int(seconds))
    total_minutes = (seconds + 59) // 60
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


def should_delete_request(record: dict[str, Any], *, current_ts: int | None = None) -> bool:
    if record.get("status") not in {"approved", "rejected"}:
        return False

    current_ts = now_ts() if current_ts is None else current_ts
    try:
        reference_ts = int(record.get("decided_at") or record.get("created_at") or 0)
    except (TypeError, ValueError):
        return False
    return reference_ts > 0 and current_ts - reference_ts >= REQUEST_RETENTION_SECONDS


class SubscriberVerificationPanelView(discord.ui.View):
    def __init__(self, cog: "SubscriberVerification"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Request Subscriber Role",
        style=discord.ButtonStyle.primary,
        custom_id=PANEL_BUTTON_CUSTOM_ID,
    )
    async def request_role(self, interaction: discord.Interaction, button: discord.ui.Button):  # noqa: ARG002
        await self.cog.open_submission_modal(interaction)


class SubscriberVerificationReviewView(discord.ui.View):
    def __init__(self, cog: "SubscriberVerification", *, disabled: bool = False):
        super().__init__(timeout=None)
        self.cog = cog
        if disabled:
            for item in self.children:
                if isinstance(item, discord.ui.Button):
                    item.disabled = True

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, custom_id=ACCEPT_CUSTOM_ID)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):  # noqa: ARG002
        await self.cog.review_request(interaction, approved=True)

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger, custom_id=REJECT_CUSTOM_ID)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):  # noqa: ARG002
        await self.cog.open_rejection_modal(interaction)


class SubscriberVerificationModal(discord.ui.Modal, title="Subscriber Verification"):
    youtube_username = discord.ui.TextInput(
        label="YouTube username",
        placeholder="@your-youtube-username",
        min_length=2,
        max_length=100,
    )
    screenshot_file = discord.ui.Label(
        text="Subscription screenshot",
        description="Upload an image, or paste an image link below.",
        component=discord.ui.FileUpload(
            custom_id="kereviz_sub_verify_screenshot",
            required=False,
            min_values=0,
            max_values=1,
        ),
    )
    screenshot_url = discord.ui.TextInput(
        label="Screenshot image URL",
        placeholder="Optional: paste a Discord CDN, Imgur, or direct image link.",
        required=False,
        min_length=0,
        max_length=500,
    )

    def __init__(self, cog: "SubscriberVerification"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        attachments = self.screenshot_file.component.values
        screenshot = attachments[0] if attachments else None
        await self.cog.submit_request(
            interaction,
            youtube_username=str(self.youtube_username.value),
            screenshot=screenshot,
            screenshot_url=str(self.screenshot_url.value),
        )


class SubscriberRejectionModal(discord.ui.Modal, title="Reject Subscriber Request"):
    reason = discord.ui.TextInput(
        label="Rejection reason",
        placeholder="Explain what is missing or why the proof was rejected.",
        style=discord.TextStyle.paragraph,
        min_length=3,
        max_length=500,
    )

    def __init__(self, cog: "SubscriberVerification", review_message_id: int):
        super().__init__()
        self.cog = cog
        self.review_message_id = review_message_id

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.review_request(
            interaction,
            approved=False,
            review_message_id=self.review_message_id,
            reason=str(self.reason.value),
        )


class SubscriberVerification(commands.Cog):
    """Button and modal based Subscriber role verification workflow."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = SubscriberVerificationStore()
        self._requests: dict[str, dict[str, Any]] = {}
        self._panels: dict[int, dict[str, Any]] = {}
        self._store_available = True
        self._panel_task: asyncio.Task | None = None
        self._cleanup_task: asyncio.Task | None = None
        self._panel_lock = asyncio.Lock()
        self._request_lock = asyncio.Lock()

    async def initialize(self) -> None:
        try:
            self._requests = await self.store.load_all_requests()
            self._panels = await self.store.load_all_panels()
        except Exception as exc:
            self._store_available = False
            print(f"[SUB-VERIFY] Firestore state is unavailable: {exc}")

    def cog_unload(self) -> None:
        if self._panel_task and not self._panel_task.done():
            self._panel_task.cancel()
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()

    @property
    def owner_id(self) -> int | None:
        raw_owner = os.getenv("OWNER_ID")
        if raw_owner:
            try:
                return int(raw_owner)
            except ValueError:
                return None
        return self.bot.owner_id

    async def _fetch_text_channel(self, channel_id: int) -> discord.TextChannel | None:
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
                print(f"[SUB-VERIFY] Could not fetch channel {channel_id}: {exc}")
                return None

        if not isinstance(channel, discord.TextChannel):
            print(f"[SUB-VERIFY] Configured channel is not a text channel: {channel_id}")
            return None
        return channel

    async def _channels(self) -> tuple[discord.TextChannel | None, discord.TextChannel | None, discord.TextChannel | None]:
        panel = await self._fetch_text_channel(PANEL_CHANNEL_ID)
        public_log = await self._fetch_text_channel(PUBLIC_LOG_CHANNEL_ID)
        private_review = await self._fetch_text_channel(PRIVATE_REVIEW_CHANNEL_ID)
        return panel, public_log, private_review

    def _bot_member(self, guild: discord.Guild) -> discord.Member | None:
        if guild.me:
            return guild.me
        if self.bot.user:
            return guild.get_member(self.bot.user.id)
        return None

    def _can_manage_subscriber_role(self, guild: discord.Guild) -> bool:
        me = self._bot_member(guild)
        role = guild.get_role(SUBSCRIBER_ROLE_ID)
        return bool(me and role and me.guild_permissions.manage_roles and not role.managed and role < me.top_role)

    def _setup_issues_for_channel(
        self,
        guild: discord.Guild,
        channel: discord.TextChannel | None,
        channel_name: str,
    ) -> list[str]:
        if channel is None:
            return [f"The configured **{channel_name}** channel could not be found."]
        if channel.guild.id != guild.id:
            return [f"The configured **{channel_name}** channel belongs to another server."]

        me = self._bot_member(guild)
        if me is None:
            return ["I could not verify my server permissions."]

        perms = channel.permissions_for(me)
        required = [
            ("view_channel", "View Channel"),
            ("send_messages", "Send Messages"),
            ("embed_links", "Embed Links"),
            ("read_message_history", "Read Message History"),
        ]
        issues = []
        for attr, label in required:
            if not getattr(perms, attr):
                issues.append(f"I need **{label}** in {channel.mention}.")
        return issues

    def _setup_issues(
        self,
        guild: discord.Guild,
        panel: discord.TextChannel | None,
        public_log: discord.TextChannel | None,
        private_review: discord.TextChannel | None,
    ) -> list[str]:
        issues: list[str] = []
        issues.extend(self._setup_issues_for_channel(guild, panel, "panel"))
        issues.extend(self._setup_issues_for_channel(guild, public_log, "public log"))
        issues.extend(self._setup_issues_for_channel(guild, private_review, "private review"))

        role = guild.get_role(SUBSCRIBER_ROLE_ID)
        if role is None:
            issues.append(f"The Subscriber role (`{SUBSCRIBER_ROLE_ID}`) was not found.")
        elif not self._can_manage_subscriber_role(guild):
            issues.append("I need **Manage Roles** and my bot role must be above the Subscriber role.")

        if self.owner_id is None:
            issues.append("OWNER_ID is not configured, so I cannot ping the bot owner for reviews.")

        return issues

    def _build_panel_embed(self, guild: discord.Guild) -> discord.Embed:
        embed = discord.Embed(
            title="Subscriber Role Verification",
            description=(
                "Use the button below to request the **Subscriber** role.\n\n"
                "You will be asked for your YouTube username and proof showing that you are subscribed."
            ),
            color=discord.Color(PANEL_COLOR),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name="What You Need",
            value=(
                "- Your YouTube username\n"
                "- A screenshot image upload or direct image link\n"
                "- One request every 24 hours"
            ),
            inline=False,
        )
        embed.add_field(
            name="YouTube Channel",
            value=f"[Open Kereviz YouTube]({YOUTUBE_CHANNEL_URL})",
            inline=False,
        )
        embed.add_field(
            name="Review Flow",
            value="Your proof is sent privately to staff. Public logs only show the request status.",
            inline=False,
        )
        role = guild.get_role(SUBSCRIBER_ROLE_ID)
        if role:
            embed.add_field(name="Reward", value=role.mention, inline=True)
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.set_footer(text=f"{PANEL_MARKER} | Proof is reviewed manually")
        return embed

    def _looks_like_panel(self, message: discord.Message) -> bool:
        if not self.bot.user or message.author.id != self.bot.user.id:
            return False
        for embed in message.embeds:
            footer = embed.footer.text if embed.footer else ""
            if embed.title == "Subscriber Role Verification" and PANEL_MARKER in footer:
                return True
        return False

    async def _find_existing_panel(self, channel: discord.TextChannel) -> discord.Message | None:
        try:
            async for message in channel.history(limit=50):
                if self._looks_like_panel(message):
                    return message
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"[SUB-VERIFY] Could not scan panel history in {channel.id}: {exc}")
        return None

    async def _save_panel(self, record: dict[str, Any]) -> None:
        normalized = normalize_panel(record)
        guild_id = normalized.get("guild_id")
        if guild_id is None:
            return
        self._panels[int(guild_id)] = normalized
        if not self._store_available:
            return
        try:
            await self.store.save_panel(normalized)
        except Exception as exc:
            self._store_available = False
            print(f"[SUB-VERIFY] Could not save panel state: {exc}")

    async def _save_request(self, record: dict[str, Any]) -> None:
        normalized = normalize_request(record)
        request_id = normalized.get("id")
        if not request_id:
            return
        self._requests[str(request_id)] = normalized
        if not self._store_available:
            return
        try:
            await self.store.save_request(normalized)
        except Exception as exc:
            self._store_available = False
            print(f"[SUB-VERIFY] Could not save request state: {exc}")

    async def _upsert_panel(
        self,
        channel: discord.TextChannel,
        *,
        created_by_id: int | None = None,
    ) -> discord.Message | None:
        async with self._panel_lock:
            record = self._panels.get(channel.guild.id, {})
            message: discord.Message | None = None

            message_id = record.get("message_id")
            if message_id and int(record.get("channel_id") or channel.id) == channel.id:
                try:
                    message = await channel.fetch_message(int(message_id))
                except discord.NotFound:
                    message = None
                except (discord.Forbidden, discord.HTTPException) as exc:
                    print(f"[SUB-VERIFY] Could not fetch stored panel message {message_id}: {exc}")

            if message is None:
                message = await self._find_existing_panel(channel)

            embed = self._build_panel_embed(channel.guild)
            view = SubscriberVerificationPanelView(self)
            if message is None:
                try:
                    message = await channel.send(
                        embed=embed,
                        view=view,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except (discord.Forbidden, discord.HTTPException) as exc:
                    print(f"[SUB-VERIFY] Could not create panel in {channel.id}: {exc}")
                    return None
            else:
                try:
                    await message.edit(
                        embed=embed,
                        view=view,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except (discord.Forbidden, discord.HTTPException) as exc:
                    print(f"[SUB-VERIFY] Could not refresh panel {message.id}: {exc}")
                    return None

            await self._save_panel(
                {
                    "guild_id": channel.guild.id,
                    "channel_id": channel.id,
                    "message_id": message.id,
                    "created_by_id": created_by_id or record.get("created_by_id"),
                    "status": "active",
                }
            )
            return message

    def _start_panel_setup(self) -> None:
        if self._panel_task and not self._panel_task.done():
            return
        self._panel_task = asyncio.create_task(self._setup_panel())

    def _start_cleanup_runner(self) -> None:
        if self._cleanup_task and not self._cleanup_task.done():
            return
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def _cleanup_loop(self) -> None:
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            await self.cleanup_old_requests()
            await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)

    async def cleanup_old_requests(self) -> int:
        if not self._store_available:
            return 0

        deleted = 0
        async with self._request_lock:
            request_ids = [
                request_id
                for request_id, record in self._requests.items()
                if should_delete_request(record)
            ]
            for request_id in request_ids:
                try:
                    await self.store.delete_request(request_id)
                except Exception as exc:
                    self._store_available = False
                    print(f"[SUB-VERIFY] Could not delete old request {request_id}: {exc}")
                    break
                else:
                    self._requests.pop(request_id, None)
                    deleted += 1

        if deleted:
            print(f"[SUB-VERIFY] Deleted {deleted} old Subscriber verification request(s).")
        return deleted

    async def _setup_panel(self) -> None:
        await self.bot.wait_until_ready()
        panel, public_log, private_review = await self._channels()
        if panel is None:
            return

        issues = self._setup_issues(panel.guild, panel, public_log, private_review)
        if issues:
            print("[SUB-VERIFY] Panel setup needs attention: " + " | ".join(issues))
            return

        message = await self._upsert_panel(panel)
        if message:
            print(f"[SUB-VERIFY] Panel ready: {message.jump_url}")

    def _pending_request_for_user(self, guild_id: int, user_id: int) -> dict[str, Any] | None:
        for record in self._requests.values():
            if (
                record.get("guild_id") == guild_id
                and record.get("user_id") == user_id
                and record.get("status") == "pending"
            ):
                return record
        return None

    def _request_from_review_message(self, message_id: int) -> dict[str, Any] | None:
        for record in self._requests.values():
            if record.get("review_message_id") == message_id:
                return record
        return None

    def _new_request_id(self) -> str:
        while True:
            request_id = secrets.token_hex(5)
            if request_id not in self._requests:
                return request_id

    async def open_submission_modal(self, interaction: discord.Interaction) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("This form can only be used in the server.", ephemeral=True)

        role = interaction.guild.get_role(SUBSCRIBER_ROLE_ID)
        if role and role in interaction.user.roles:
            return await interaction.response.send_message("You already have the Subscriber role.", ephemeral=True)

        pending = self._pending_request_for_user(interaction.guild.id, interaction.user.id)
        if pending:
            return await interaction.response.send_message(
                "You already have a pending Subscriber verification request.",
                ephemeral=True,
            )

        cooldown = seconds_until_next_submission(self._requests, interaction.guild.id, interaction.user.id)
        if cooldown:
            return await interaction.response.send_message(
                f"You can submit another Subscriber verification request in about {format_cooldown(cooldown)}.",
                ephemeral=True,
            )

        panel, public_log, private_review = await self._channels()
        issues = self._setup_issues(interaction.guild, panel, public_log, private_review)
        if issues:
            return await interaction.response.send_message(
                "Subscriber verification is not ready yet. Please contact staff.",
                ephemeral=True,
            )

        await interaction.response.send_modal(SubscriberVerificationModal(self))

    def _public_embed(self, record: dict[str, Any]) -> discord.Embed:
        status = str(record.get("status") or "pending")
        color = {
            "pending": PENDING_COLOR,
            "approved": APPROVED_COLOR,
            "rejected": REJECTED_COLOR,
        }.get(status, PENDING_COLOR)
        title = {
            "pending": "Subscriber Role Request Received",
            "approved": "Subscriber Role Request Approved",
            "rejected": "Subscriber Role Request Rejected",
        }.get(status, "Subscriber Role Request")

        descriptions = {
            "pending": "A Subscriber role request has been received and is waiting for approval.",
            "approved": "This Subscriber role request has been approved.",
            "rejected": "This Subscriber role request has been rejected.",
        }
        embed = discord.Embed(
            title=title,
            description=descriptions.get(status),
            color=discord.Color(color),
            timestamp=datetime.fromtimestamp(int(record.get("created_at") or now_ts()), timezone.utc),
        )
        embed.add_field(name="Member", value=f"<@{record['user_id']}>", inline=True)
        embed.add_field(name="Status", value=status.title(), inline=True)
        embed.add_field(name="Request ID", value=f"`{record['id']}`", inline=True)
        if status == "rejected" and record.get("decision_reason"):
            embed.add_field(name="Reason", value=str(record["decision_reason"])[:1024], inline=False)
        if record.get("decided_at"):
            embed.add_field(name="Reviewed", value=f"<t:{int(record['decided_at'])}:R>", inline=True)
        embed.set_footer(text="Subscriber verification status")
        return embed

    def _review_embed(self, record: dict[str, Any]) -> discord.Embed:
        status = str(record.get("status") or "pending")
        color = {
            "pending": PENDING_COLOR,
            "approved": APPROVED_COLOR,
            "rejected": REJECTED_COLOR,
        }.get(status, PENDING_COLOR)
        embed = discord.Embed(
            title="Subscriber Verification Review",
            description="Private proof for manual Subscriber role approval.",
            color=discord.Color(color),
            timestamp=datetime.fromtimestamp(int(record.get("created_at") or now_ts()), timezone.utc),
        )
        embed.add_field(name="Member", value=f"<@{record['user_id']}> (`{record['user_id']}`)", inline=False)
        embed.add_field(name="YouTube Username", value=record.get("youtube_username") or "Not provided", inline=False)
        embed.add_field(name="Screenshot File", value=record.get("screenshot_url") or "Not provided", inline=False)
        embed.add_field(name="Status", value=status.title(), inline=True)
        embed.add_field(name="Request ID", value=f"`{record['id']}`", inline=True)

        public_message_id = record.get("public_message_id")
        if public_message_id:
            jump_url = (
                f"https://discord.com/channels/{record['guild_id']}/"
                f"{record['public_log_channel_id']}/{public_message_id}"
            )
            embed.add_field(name="Public Log", value=f"[Open message]({jump_url})", inline=True)

        if record.get("decided_by_id"):
            embed.add_field(name="Reviewed By", value=f"<@{record['decided_by_id']}>", inline=True)
        if record.get("decided_at"):
            embed.add_field(name="Reviewed At", value=f"<t:{int(record['decided_at'])}:F>", inline=True)
        if status == "rejected" and record.get("decision_reason"):
            embed.add_field(name="Rejection Reason", value=str(record["decision_reason"])[:1024], inline=False)

        screenshot_url = record.get("screenshot_url")
        if screenshot_url:
            embed.set_image(url=screenshot_url)
        embed.set_footer(text="Accept grants Subscriber. Reject pings the member in public logs.")
        return embed

    def _review_content(self, record: dict[str, Any]) -> str | None:
        status = str(record.get("status") or "pending")
        if status == "pending" and self.owner_id:
            return f"<@{self.owner_id}> New Subscriber verification request."
        if status == "approved":
            return f"Request `{record['id']}` approved."
        if status == "rejected":
            return f"Request `{record['id']}` rejected."
        return None

    def _public_content(self, record: dict[str, Any]) -> str | None:
        if record.get("status") == "approved":
            return f"<@{record['user_id']}> your Subscriber verification request was approved."
        if record.get("status") == "rejected":
            content = f"<@{record['user_id']}> your Subscriber verification request was rejected."
            if record.get("decision_reason"):
                content += f"\nReason: {str(record['decision_reason'])[:1500]}"
            return content
        return None

    async def submit_request(
        self,
        interaction: discord.Interaction,
        *,
        youtube_username: str,
        screenshot: discord.Attachment | None,
        screenshot_url: str,
    ) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("This form can only be used in the server.", ephemeral=True)

        youtube_username = youtube_username.strip()
        screenshot_url = screenshot_url.strip()
        proof_url = screenshot_url
        if screenshot is not None:
            if not is_supported_image_file(screenshot.filename, screenshot.content_type):
                return await interaction.response.send_message(
                    "Please upload a valid image file: PNG, JPG, JPEG, WEBP, or GIF.",
                    ephemeral=True,
                )
            if screenshot.size and screenshot.size > MAX_SCREENSHOT_SIZE_BYTES:
                return await interaction.response.send_message(
                    "Please upload a screenshot smaller than 8 MB.",
                    ephemeral=True,
                )
            proof_url = screenshot.url

        if not proof_url:
            return await interaction.response.send_message(
                "Please upload a screenshot image or paste a direct image URL.",
                ephemeral=True,
            )
        if screenshot is None and not valid_http_url(proof_url):
            return await interaction.response.send_message(
                "Please paste a valid screenshot URL starting with http:// or https://.",
                ephemeral=True,
            )

        role = interaction.guild.get_role(SUBSCRIBER_ROLE_ID)
        if role and role in interaction.user.roles:
            return await interaction.response.send_message("You already have the Subscriber role.", ephemeral=True)

        panel, public_log, private_review = await self._channels()
        issues = self._setup_issues(interaction.guild, panel, public_log, private_review)
        if issues or public_log is None or private_review is None:
            return await interaction.response.send_message(
                "Subscriber verification is not ready yet. Please contact staff.",
                ephemeral=True,
            )

        async with self._request_lock:
            pending = self._pending_request_for_user(interaction.guild.id, interaction.user.id)
            if pending:
                return await interaction.response.send_message(
                    "You already have a pending Subscriber verification request.",
                    ephemeral=True,
                )

            cooldown = seconds_until_next_submission(self._requests, interaction.guild.id, interaction.user.id)
            if cooldown:
                return await interaction.response.send_message(
                    f"You can submit another Subscriber verification request in about {format_cooldown(cooldown)}.",
                    ephemeral=True,
                )

            request_id = self._new_request_id()
            record = {
                "id": request_id,
                "guild_id": interaction.guild.id,
                "user_id": interaction.user.id,
                "youtube_username": youtube_username,
                "screenshot_url": proof_url,
                "status": "pending",
                "created_at": now_ts(),
                "decided_at": None,
                "decided_by_id": None,
                "public_log_channel_id": public_log.id,
                "public_message_id": None,
                "review_channel_id": private_review.id,
                "review_message_id": None,
                "decision_reason": "",
            }

            public_message: discord.Message | None = None
            try:
                public_message = await public_log.send(
                    embed=self._public_embed(record),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                record["public_message_id"] = public_message.id

                review_message = await private_review.send(
                    content=self._review_content(record),
                    embed=self._review_embed(record),
                    view=SubscriberVerificationReviewView(self),
                    allowed_mentions=discord.AllowedMentions(
                        users=[discord.Object(id=self.owner_id)] if self.owner_id else False,
                        roles=False,
                        everyone=False,
                    ),
                )
                record["review_message_id"] = review_message.id
            except (discord.Forbidden, discord.HTTPException) as exc:
                if public_message is not None:
                    try:
                        await public_message.delete()
                    except discord.HTTPException:
                        pass
                print(f"[SUB-VERIFY] Could not create verification request: {exc}")
                return await interaction.response.send_message(
                    "I could not submit your request. Please contact staff.",
                    ephemeral=True,
                )

            await self._save_request(record)

        await interaction.response.send_message(
            "Your Subscriber verification request was submitted and is waiting for approval.",
            ephemeral=True,
        )

    async def _fetch_member(self, guild: discord.Guild, user_id: int) -> discord.Member | None:
        member = guild.get_member(user_id)
        if member is not None:
            return member
        try:
            return await guild.fetch_member(user_id)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            return None

    async def _update_public_message(self, record: dict[str, Any]) -> None:
        channel = await self._fetch_text_channel(int(record["public_log_channel_id"]))
        if not channel or not record.get("public_message_id"):
            return
        try:
            message = await channel.fetch_message(int(record["public_message_id"]))
            await message.edit(
                content=None,
                embed=self._public_embed(record),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            return

    async def _send_public_decision_ping(self, record: dict[str, Any]) -> None:
        content = self._public_content(record)
        if not content:
            return

        channel = await self._fetch_text_channel(int(record["public_log_channel_id"]))
        if not channel:
            return

        public_message_id = record.get("public_message_id")
        if public_message_id:
            content += (
                f"\nStatus: https://discord.com/channels/{record['guild_id']}/"
                f"{record['public_log_channel_id']}/{public_message_id}"
            )

        try:
            await channel.send(
                content,
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )
        except (discord.Forbidden, discord.HTTPException):
            return

    async def open_rejection_modal(self, interaction: discord.Interaction) -> None:
        if not interaction.guild or not interaction.message:
            return await interaction.response.send_message("This review action is not available here.", ephemeral=True)

        if self.owner_id is None or interaction.user.id != self.owner_id:
            return await interaction.response.send_message("Only the bot owner can review Subscriber requests.", ephemeral=True)

        record = self._request_from_review_message(interaction.message.id)
        if not record:
            return await interaction.response.send_message("I could not find this Subscriber request.", ephemeral=True)
        if record.get("status") != "pending":
            return await interaction.response.send_message("This Subscriber request was already reviewed.", ephemeral=True)

        await interaction.response.send_modal(SubscriberRejectionModal(self, interaction.message.id))

    async def _update_review_message(self, record: dict[str, Any]) -> None:
        channel = await self._fetch_text_channel(int(record["review_channel_id"]))
        if not channel or not record.get("review_message_id"):
            return
        try:
            message = await channel.fetch_message(int(record["review_message_id"]))
            await message.edit(
                content=self._review_content(record),
                embed=self._review_embed(record),
                view=SubscriberVerificationReviewView(self, disabled=record.get("status") != "pending"),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            return

    async def review_request(
        self,
        interaction: discord.Interaction,
        *,
        approved: bool,
        review_message_id: int | None = None,
        reason: str | None = None,
    ) -> None:
        if not interaction.guild:
            return await interaction.response.send_message("This review action is not available here.", ephemeral=True)

        if self.owner_id is None or interaction.user.id != self.owner_id:
            return await interaction.response.send_message("Only the bot owner can review Subscriber requests.", ephemeral=True)

        message_id = review_message_id or (interaction.message.id if interaction.message else None)
        if message_id is None:
            return await interaction.response.send_message("This review action is not available here.", ephemeral=True)

        record = self._request_from_review_message(message_id)
        if not record:
            return await interaction.response.send_message("I could not find this Subscriber request.", ephemeral=True)
        if record.get("status") != "pending":
            return await interaction.response.send_message("This Subscriber request was already reviewed.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild

        if approved:
            role = guild.get_role(SUBSCRIBER_ROLE_ID)
            if role is None:
                return await interaction.followup.send("Subscriber role was not found.", ephemeral=True)
            if not self._can_manage_subscriber_role(guild):
                return await interaction.followup.send("I cannot manage the Subscriber role.", ephemeral=True)
            member = await self._fetch_member(guild, int(record["user_id"]))
            if member is None:
                return await interaction.followup.send("That member is no longer in the server.", ephemeral=True)
            try:
                if role not in member.roles:
                    await member.add_roles(role, reason=f"Subscriber verification approved by {interaction.user}")
            except (discord.Forbidden, discord.HTTPException):
                return await interaction.followup.send("I could not grant the Subscriber role.", ephemeral=True)

        async with self._request_lock:
            stored = self._requests.get(str(record["id"]))
            if not stored or stored.get("status") != "pending":
                return await interaction.followup.send("This Subscriber request was already reviewed.", ephemeral=True)
            stored["status"] = "approved" if approved else "rejected"
            stored["decided_at"] = now_ts()
            stored["decided_by_id"] = interaction.user.id
            stored["decision_reason"] = "" if approved else (reason or "").strip()
            await self._save_request(stored)
            updated = dict(stored)

        await self._update_public_message(updated)
        await self._send_public_decision_ping(updated)
        await self._update_review_message(updated)

        action = "approved" if approved else "rejected"
        await interaction.followup.send(f"Subscriber request `{updated['id']}` was {action}.", ephemeral=True)

    @commands.Cog.listener()
    async def on_ready(self):
        self._start_panel_setup()
        self._start_cleanup_runner()

    @commands.group(
        name="subverify",
        invoke_without_command=True,
        help="Show the Subscriber verification panel status.",
    )
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    async def subverify(self, ctx: commands.Context):
        panel, public_log, private_review = await self._channels()
        issues = self._setup_issues(ctx.guild, panel, public_log, private_review)
        pending_count = sum(
            1
            for record in self._requests.values()
            if record.get("guild_id") == ctx.guild.id and record.get("status") == "pending"
        )

        embed = discord.Embed(
            title="Subscriber Verification",
            color=discord.Color(PANEL_COLOR),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Panel Channel", value=panel.mention if panel else f"`{PANEL_CHANNEL_ID}`", inline=True)
        embed.add_field(
            name="Public Logs",
            value=public_log.mention if public_log else f"`{PUBLIC_LOG_CHANNEL_ID}`",
            inline=True,
        )
        embed.add_field(
            name="Private Review",
            value=private_review.mention if private_review else f"`{PRIVATE_REVIEW_CHANNEL_ID}`",
            inline=True,
        )
        embed.add_field(name="Pending Requests", value=str(pending_count), inline=True)
        embed.add_field(name="Health", value="Ready" if not issues else "\n".join(f"- {issue}" for issue in issues), inline=False)
        embed.set_footer(text="Use !subverify post to create or refresh the panel.")
        await ctx.send(embed=embed)

    @subverify.command(name="post", aliases=["refresh"], help="Create or refresh the Subscriber verification panel.")
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    async def subverify_post(self, ctx: commands.Context):
        panel, public_log, private_review = await self._channels()
        issues = self._setup_issues(ctx.guild, panel, public_log, private_review)
        if issues or panel is None:
            return await ctx.send(
                "**I cannot publish the Subscriber verification panel yet:**\n"
                + "\n".join(f"- {issue}" for issue in issues)
            )

        message = await self._upsert_panel(panel, created_by_id=ctx.author.id)
        if message is None:
            return await ctx.send("I could not create or refresh the Subscriber verification panel.")
        await ctx.send(f"Subscriber verification panel is ready in {panel.mention}: {message.jump_url}")

    @subverify.error
    @subverify_post.error
    async def subverify_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingPermissions):
            return await ctx.send("You need **Manage Roles** permission to use Subscriber verification commands.")
        if isinstance(error, commands.NoPrivateMessage):
            return await ctx.send("Subscriber verification commands can only be used in a server.")
        raise error


async def setup(bot: commands.Bot):
    cog = SubscriberVerification(bot)
    await cog.initialize()
    await bot.add_cog(cog)
    bot.add_view(SubscriberVerificationPanelView(cog))
    bot.add_view(SubscriberVerificationReviewView(cog))
    command = bot.get_command("subverify")
    if command:
        command.category = "Admin"
    if bot.is_ready():
        cog._start_panel_setup()
        cog._start_cleanup_runner()
