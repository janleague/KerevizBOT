# KerevizBOT

A polished, modular Discord bot built for community management and creator-focused servers. KerevizBOT combines moderation tools, giveaway automation, invite tracking, YouTube upload alerts, AI utilities, Hypixel stats, and Firebase-backed persistence in one Python application.

## Highlights

- **Moderation tools**: ban, unban, command toggling, owner utilities, and subscriber role management.
- **Guard tools**: anti-ad and anti-ghost-ping protection for cleaner community chat.
- **Deleted image logs**: caches image attachments and reposts deleted images to a dedicated log channel.
- **YouTube announcements**: polls a YouTube RSS feed with retry protection and posts new upload alerts with Firestore-backed duplicate protection.
- **Invite tracking**: tracks invite usage, member joins/leaves, reward roles, leaderboards, and logging.
- **Giveaways**: persistent button-based giveaways with rerolls, role requirements, bonus entries, and recovery for missed announcements.
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

The Minecraft server command stores:

- `minecraft_servers/{server_host}`

Guard settings store:

- `guard_configs/{guild_id}`

Deleted image logging stores temporary metadata in:

- `deleted_image_cache/{message_id}`

Hypixel API configuration stores:

- `bot_state/hypixel_api`

Legacy local files such as `last_video_id.txt`, `invite_tracker.json`, and `giveaways.json` are migrated automatically when possible.
The bundled `servers.txt` file is used as the initial seed list for Minecraft servers.
Deleted image files are cached locally in `deleted_image_cache/` until the deleted-image log is sent.

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
