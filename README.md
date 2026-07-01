# KerevizBOT

A polished, modular Discord bot built for community management and creator-focused servers. KerevizBOT combines moderation tools, giveaway automation, invite tracking, YouTube upload alerts, AI utilities, Hypixel stats, and Firebase-backed persistence in one Python application.

## Highlights

- **Moderation tools**: ban, unban, command toggling, owner utilities, and subscriber role management.
- **Guard tools**: anti-ad and anti-ghost-ping protection for cleaner community chat.
- **Deleted image logs**: caches image attachments and reposts deleted images to a dedicated log channel.
- **YouTube announcements**: polls a YouTube RSS feed with retry protection and posts new upload alerts with Firestore-backed duplicate protection.
- **Invite tracking**: tracks invite usage, member joins/leaves, reward roles, leaderboards, and logging.
- **Giveaways**: persistent button-based giveaways with rerolls, role requirements, bonus entries, and recovery for missed announcements.
- **Reaction roles**: persistent notification-role panel for YouTube and giveaway pings.
- **Subscriber verification**: button-based Subscriber role request flow with temporary private proof upload channels, staff review, public status logs, and one request per member every 24 hours.
- **Firestore storage alerts**: checks Firestore data and index storage and warns staff before the free-tier storage limit is reached.
- **AI commands**: free text and image utilities powered by Pollinations.
- **Hypixel stats**: profile, BedWars, SkyWars, and Duels player statistics with clean Discord embeds.
- **Minecraft server discovery**: random live server lookup from a Firestore-backed server list.
- **Firebase persistence**: Firestore stores YouTube announcements, invite tracking, and Minecraft server data.

## Tech Stack

- Python
- discord.py
- Firebase Admin SDK / Cloud Firestore
- aiohttp
- python-dotenv
- psutil

## Project Structure

```text
KerevizBOT/
  bot.py
  commands/
    fun/
    ai.py
    ban.py
    deleted_image_logs.py
    giveaway.py
    guard.py
    invite_tracker.py
    ...
  services/
    deleted_image_store.py
    firebase_client.py
    guard_store.py
    invite_store.py
    minecraft_server_store.py
    youtube_store.py
    blocked_commands.py
  requirements.txt
  servers.txt
```

## Setup

1. Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

2. Create a `.env` file:

```env
DISCORD_TOKEN=your-discord-bot-token
OWNER_ID=your-discord-user-id
YOUTUBE_CHANNEL_ID=your-youtube-channel-id-or-handle
DISCORD_CHANNEL_ID=your-announcement-channel-id
LOG_CHANNEL_ID=optional-log-channel-id
WELCOME_CHANNEL_ID=optional-welcome-channel-id
LEAVES_LOG_CHANNEL_ID=optional-leaves-log-channel-id
MESSAGES_LOG_CHANNEL_ID=optional-message-log-channel-id
DELETED_IMAGE_LOG_CHANNEL_ID=optional-deleted-image-log-channel-id
HYPIXEL_API_KEY=optional-initial-hypixel-api-key
POLLINATIONS_API_KEY=optional-pollinations-key
GITHUB_URL=https://github.com/your-name/your-repo
FIREBASE_CREDENTIALS_PATH=firebase-service-account.json
FIREBASE_PROJECT_ID=your-firebase-project-id
FIRESTORE_ALERT_CHANNEL_ID=1521808241233760337
FIRESTORE_STORAGE_LIMIT_BYTES=1073741824
FIRESTORE_STORAGE_WARN_THRESHOLDS=70,85,95
FIRESTORE_STORAGE_CHECK_INTERVAL=21600
```

3. Place your Firebase service account file in the project root:

```text
firebase-service-account.json
```

4. Run the bot:

```powershell
python bot.py
```

## Discloud CLI

Use the repo wrapper instead of calling `discloud` directly on Windows:

```powershell
.\discloud.ps1 user info
.\discloud.ps1 app info
.\discloud.ps1 app status <app_id>
.\discloud.ps1 app upload
```

The wrapper reads the local Discloud login config from `C:\Users\janle\.discloud\.cli`,
extracts the real token into `DISCLOUD_TOKEN` only for the child process, and avoids
storing secrets in this repository.

## Firebase

KerevizBOT uses Cloud Firestore for state that should survive restarts and deployments.

The YouTube announcement system stores:

- `bot_state/youtube`
- `youtube_announcements/{video_id}`

The invite tracker stores:

- `invite_trackers/{guild_id}`
- `invite_trackers/{guild_id}/invite_cache/{invite_code}`
- `invite_trackers/{guild_id}/member_invites/{user_id}`
- `invite_trackers/{guild_id}/member_joins/{member_id}`

The giveaway system stores:

- `giveaways/{giveaway_id}`

The notification reaction-role panel stores:

- `reaction_role_panels/{guild_id}`

The Subscriber verification system stores:

- `subscriber_verifications/{request_id}`
- `subscriber_verification_panels/{guild_id}`

Subscriber verification opens a temporary private proof upload channel as soon as a member presses the panel button. Members upload one screenshot image attachment there, including on mobile clients where Discord modal inputs can be unreliable. The channel is deleted after the request is created or after 10 minutes. If the temporary channel expires before a screenshot is submitted, the member can press the button again.

Subscriber proof upload channels are created under the `PROOFS` category. The bot hides that category from `@everyone`, opens each temporary channel only for the requesting member and the bot, and cleans up stale `sub-proof-*` channels on startup.

The Minecraft server command stores:

- `minecraft_servers/{server_host}`

Guard settings store:

- `guard_configs/{guild_id}`

Deleted image logging stores temporary metadata in:

- `deleted_image_cache/{message_id}`

Hypixel API configuration stores:

- `bot_state/hypixel_api`

Firestore storage alert state stores:

- `bot_state/firestore_storage_alert`

Legacy local files such as `last_video_id.txt`, `invite_tracker.json`, and `giveaways.json` are migrated automatically when possible.
The bundled `servers.txt` file is used as the initial seed list for Minecraft servers.
Deleted image files are cached locally in `deleted_image_cache/` until the deleted-image log is sent.

### Firestore Storage Alerts

The bot checks the Cloud Monitoring metric `firestore.googleapis.com/storage/data_and_index_storage_bytes` every 6 hours and posts alerts to `FIRESTORE_ALERT_CHANNEL_ID`.

Defaults:

- Alert channel: `1521808241233760337`
- Storage limit: `1073741824` bytes (1 GiB)
- Warning thresholds: `70,85,95`
- Duplicate suppression state: `bot_state/firestore_storage_alert`

The Firebase service account needs the Google Cloud IAM role `roles/monitoring.viewer` on project `kerevizbot`. Without that role, the bot sends a setup warning to the alert channel once per 24 hours.

Recommended Google Cloud backup alert:

1. Open Google Cloud Console > Monitoring > Alerting.
2. Create a notification channel, such as Email.
3. Create an alerting policy for metric `firestore.googleapis.com/storage/data_and_index_storage_bytes`.
4. Add conditions for 70%, 85%, and 95% of the 1 GiB limit, or equivalent byte values.
5. Attach the notification channel and save the policy.

## Commands

### General

- `!help` - Show the command menu.
- `!channel` - Show the official YouTube channel with a button.
- `!stats` - Show bot statistics.
- `!botstats` - Alias for `!stats`.
- `!binfo` - Alias for `!stats`.

### Moderation

- `!ban`
- `!unban`
- `!a`
- `!s`
- `!clear <message count>`
- `!reactionroles` - Show the notification reaction-role panel status.
- `!reactionroles post` - Create or refresh the YouTube/Giveaway ping reaction panel.
- `!reactionroles sync` - Grant missing notification roles from the current panel reactions.
- `!subverify` - Show the Subscriber verification panel status.
- `!subverify post` - Create or refresh the Subscriber verification panel.
- `!firestoreusage` - Show current Firestore storage usage.
- `!firestoreusage test` - Send a test Firestore storage alert.
- `/ban`
- `/timeout`
- `/clear`
- `/nuke`

### Giveaways

- `!giveaway`
- `/giveaway create`
- `/giveaway end`
- `/giveaway reroll`
- `/giveaway cancel`
- `/giveaway delete`
- `/giveaway list`
- `/giveaway info`

### Guard

- `!antiadd` - Show anti-ad status.
- `!antiadd on` - Block Discord invite advertisements.
- `!antiadd off` - Disable anti-ad protection.
- `!antighostping` - Show anti-ghost-ping status.
- `!antighostping on` - Warn users who delete messages after pinging someone.
- `!antighostping off` - Disable anti-ghost-ping protection.

### Invites

- `!invite`
- `!invite enable`
- `!invite disable`
- `!invite config`
- `!invite log`
- `!invite countleaves`
- `!invite resync`
- `!invite reset`
- `!invite reward add`
- `!invite reward remove`
- `!invites`
- `!inviteleaderboard`

### Fun and Utility

- `!joke`
- `!roll`
- `!8ball`
- `!meme`
- `!randomminecraftserver` - Show a random live Minecraft server from Firestore.
- `!rms` - Alias for `!randomminecraftserver`.
- `/rmsadd` - Owner-only command to add a Minecraft server to Firestore.

### AI

- `!ai`
- `/ai ask`
- `/ai summarize`
- `/ai rewrite`
- `/ai translate`
- `/ai image`

### Hypixel

- `!hstats`
- `!hypixel`
- `!duels`
- `!bedwars`
- `!skywars`
- `/hypixelapi` - Owner-only command to update the Hypixel API key.

## Security

Never commit secrets to GitHub. These files are intentionally ignored:

- `.env`
- `firebase-service-account.json`
- `deleted_image_cache/`
- `*.zip`
- local cache and migration files

If a Discord token or Firebase private key was ever committed or exposed, rotate it immediately.

## Development Workflow

Check local changes:

```powershell
git status
```

Commit changes:

```powershell
git add .
git commit -m "Describe the change"
```

Push to GitHub:

```powershell
git push
```

## License

Source-available proprietary project. Public visibility is for portfolio, review, demonstration, and Hypixel API application purposes only.

All rights are reserved. Copying, modifying, distributing, hosting, deploying, or reusing this software requires prior written permission from the copyright holder. See [LICENSE](LICENSE).
