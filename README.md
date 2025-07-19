# 🥕 Kereviz Bot

**Kereviz Bot** is a feature‑rich, modular Discord bot written in Python.  
It’s designed to be **fast**, **reliable**, and **easy to extend**, offering everything from moderation tools to uptime statistics — all wrapped in a sleek experience.

---

## ✨ Features

✅ **AFK System** – Mark yourself as AFK, auto‑notifies when you’re mentioned.  
✅ **Moderation Tools** – Ban & manage users with rich permission checks.  
✅ **Statistics** – Live uptime (with days/years), CPU/RAM usage, ping, and more.  
✅ **Dynamic Command Toggle** – Enable/disable any command on the fly.  
✅ **Modular Architecture** – Commands separated into clean cogs for easy scaling.  
✅ **Modern UX** – Rich embeds, green accent color, detailed feedback.

---

## 🚀 Tech Stack

- **Language:** Python 3.10+
- **Library:** [discord.py](https://github.com/Rapptz/discord.py)
- **Structure:** Cog‑based, easy to expand
- **Hosting:** Fully self‑hostable (run on VPS, Docker, or any environment)

---

## 📦 Installation

```bash
# 1. Clone this repo
git clone https://github.com/yourname/kereviz-bot.git
cd kereviz-bot

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create your .env file with your tokens
# (see .env.example)

# 4. Run the bot
python bot.py
