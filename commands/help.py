import discord
from discord.ext import commands
from services.blocked_commands import KEREVIZCRAFT_CATEGORY, KEREVIZCRAFT_COMMAND_NAMES

# Category visuals updated with emojis from your original help command and correct descriptions
CATEGORY_META = {
    "Admin": {"emoji": "🛡️", "desc": "Shows moderation commands."},
    "AI": {"emoji": "🤖", "desc": "Shows AI assistant commands."},
    "Fun": {"emoji": "🎉", "desc": "Shows fun & entertainment commands."},
    "General": {"emoji": "✨", "desc": "Shows general commands."},
    "Guard": {"emoji": "🛡️", "desc": "Shows server protection commands."},
    "Hypixel": {"emoji": "📕", "desc": "Shows Hypixel-related commands."},
    "Invites": {"emoji": "📨", "desc": "Shows invite tracking commands."},
}
FALLBACK_META = {"emoji": "📦", "desc": "Shows commands."}

class HelpSelect(discord.ui.Select):
    def __init__(self, bot: commands.Bot, cats: dict[str, list[commands.Command]]):
        self.bot = bot
        self.cats = cats
        options = self._build_options()
        super().__init__(
            placeholder="Commands",
            min_values=1,
            max_values=1,
            options=options,
        )

    def _build_options(self) -> list[discord.SelectOption]:
        options: list[discord.SelectOption] = []
        for cat, cmds in sorted(self.cats.items(), key=lambda kv: kv[0].lower()):
            meta = CATEGORY_META.get(cat, FALLBACK_META)
            options.append(
                discord.SelectOption(
                    label=cat,
                    description=meta["desc"],
                    emoji=meta["emoji"],
                    value=cat,
                )
            )
        return options

    async def callback(self, interaction: discord.Interaction):
        chosen = self.values[0]
        cmds = self.cats.get(chosen, [])
        embed = build_category_embed(chosen, cmds)
        await interaction.response.edit_message(embed=embed, view=HelpView(self.bot, self.cats))

class HelpView(discord.ui.View):
    def __init__(self, bot: commands.Bot, cats: dict[str, list[commands.Command]]):
        super().__init__(timeout=120)
        self.add_item(HelpSelect(bot, cats))

def scan_categories(bot: commands.Bot) -> dict[str, list[commands.Command]]:
    cats: dict[str, list[commands.Command]] = {}
    for cmd in sorted(bot.commands, key=lambda c: c.name):
        if getattr(cmd, "hidden", False):
            continue
        cat = getattr(cmd, "category", "General") or "General"
        if cat == KEREVIZCRAFT_CATEGORY or cmd.name in KEREVIZCRAFT_COMMAND_NAMES:
            continue
        cats.setdefault(cat, []).append(cmd)
    return cats

def build_overview_embed(author: discord.abc.User, cats: dict[str, list[commands.Command]]) -> discord.Embed:
    embed = discord.Embed(
        title="Help Menu",
        description=f"👋 {author.mention}, you can check the commands from the **Commands** dropdown below.\n**Good luck!**",
        color=discord.Color.green(),
    )
    for cat in CATEGORY_META.keys():
        if cat in cats:
            meta = CATEGORY_META[cat]
            name = f"{meta['emoji']} | {cat} Commands"
            embed.add_field(name=name, value=meta["desc"], inline=False)
    return embed

def build_category_embed(cat: str, cmds: list[commands.Command]) -> discord.Embed:
    meta = CATEGORY_META.get(cat, FALLBACK_META)
    embed = discord.Embed(
        title=f"{meta['emoji']} | {cat} Commands",
        description=meta["desc"],
        color=discord.Color.green(),
    )
    lines = [f"`!{c.name}` — {c.help or 'No description'}" for c in cmds]
    embed.add_field(name="Commands", value="\n".join(lines) if lines else "(No commands)", inline=False)
    return embed

class HelpCommand(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="help", help="Show all available commands.")
    async def help_command(self, ctx: commands.Context):
        cats = scan_categories(self.bot)
        embed = build_overview_embed(ctx.author, cats)
        await ctx.send(embed=embed, view=HelpView(self.bot, cats))

async def setup(bot: commands.Bot):
    await bot.add_cog(HelpCommand(bot))
