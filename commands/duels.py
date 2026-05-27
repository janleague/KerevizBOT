from typing import Any

import discord
from discord.ext import commands

from services.hypixel_client import (
    HypixelClientError,
    as_int,
    fetch_hypixel_player,
    format_number,
    get_rank,
    network_level,
    percent,
    ratio,
)


RANK_COLORS = {
    "MVP++": discord.Color.gold(),
    "MVP+": discord.Color.blue(),
    "MVP": discord.Color.teal(),
    "VIP+": discord.Color.green(),
    "VIP": discord.Color.dark_green(),
    "YOUTUBER": discord.Color.red(),
    "None": discord.Color.light_grey(),
}

DUEL_MODES = [
    {
        "label": "Overall",
        "value": "overall",
        "description": "All Duels stats",
        "prefixes": [],
        "streak_aliases": ["overall"],
    },
    {
        "label": "Bridge",
        "value": "bridge",
        "description": "Bridge modes",
        "prefixes": ["bridge_duel", "bridge_doubles", "bridge_threes", "bridge_four", "bridge_2v2v2v2", "bridge_3v3v3v3"],
        "streak_aliases": ["bridge"],
    },
    {
        "label": "UHC",
        "value": "uhc",
        "description": "UHC duel modes",
        "prefixes": ["uhc_duel", "uhc_doubles", "uhc_four"],
        "streak_aliases": ["uhc"],
    },
    {
        "label": "Classic",
        "value": "classic",
        "description": "Classic Duels",
        "prefixes": ["classic_duel"],
        "streak_aliases": ["classic"],
    },
    {
        "label": "OP",
        "value": "op",
        "description": "OP duel modes",
        "prefixes": ["op_duel", "op_doubles"],
        "streak_aliases": ["op"],
    },
    {
        "label": "SkyWars",
        "value": "skywars",
        "description": "SkyWars duel modes",
        "prefixes": ["sw_duel", "sw_doubles"],
        "streak_aliases": ["sw", "skywars"],
    },
    {
        "label": "Sumo",
        "value": "sumo",
        "description": "Sumo Duels",
        "prefixes": ["sumo_duel"],
        "streak_aliases": ["sumo"],
    },
    {
        "label": "Boxing",
        "value": "boxing",
        "description": "Boxing Duels",
        "prefixes": ["boxing_duel"],
        "streak_aliases": ["boxing"],
    },
    {
        "label": "Bow",
        "value": "bow",
        "description": "Bow Duels",
        "prefixes": ["bow_duel"],
        "streak_aliases": ["bow"],
    },
    {
        "label": "Mega Walls",
        "value": "mega_walls",
        "description": "Mega Walls Duels",
        "prefixes": ["mw_duel", "mw_doubles"],
        "streak_aliases": ["mw", "mega_walls"],
    },
    {
        "label": "Blitz",
        "value": "blitz",
        "description": "Blitz Duels",
        "prefixes": ["blitz_duel"],
        "streak_aliases": ["blitz"],
    },
    {
        "label": "Bow Spleef",
        "value": "bow_spleef",
        "description": "Bow Spleef Duels",
        "prefixes": ["bowspleef_duel"],
        "streak_aliases": ["bowspleef", "bow_spleef"],
    },
    {
        "label": "Combo",
        "value": "combo",
        "description": "Combo Duels",
        "prefixes": ["combo_duel"],
        "streak_aliases": ["combo"],
    },
    {
        "label": "NoDebuff",
        "value": "nodebuff",
        "description": "NoDebuff Duels",
        "prefixes": ["potion_duel", "nodebuff_duel"],
        "streak_aliases": ["potion", "nodebuff"],
    },
]


def get_mode(value: str) -> dict[str, Any]:
    return next((mode for mode in DUEL_MODES if mode["value"] == value), DUEL_MODES[0])


def sum_mode_stat(stats: dict[str, Any], prefixes: list[str], stat_name: str) -> int:
    if not prefixes:
        return as_int(stats.get(stat_name, 0))
    return sum(as_int(stats.get(f"{prefix}_{stat_name}", 0)) for prefix in prefixes)


def first_mode_stat(stats: dict[str, Any], aliases: list[str], names: list[str]) -> int:
    for name in names:
        value = stats.get(name)
        if value is not None:
            return as_int(value)
    for alias in aliases:
        candidates = [
            f"current_{alias}_winstreak",
            f"best_{alias}_winstreak",
            f"{alias}_winstreak",
        ]
        for candidate in candidates:
            value = stats.get(candidate)
            if value is not None:
                return as_int(value)
    return 0


def current_winstreak(stats: dict[str, Any], mode: dict[str, Any]) -> int:
    if mode["value"] == "overall":
        return first_mode_stat(stats, [], ["current_winstreak", "current_overall_winstreak"])
    for alias in mode["streak_aliases"]:
        for key in (f"current_{alias}_winstreak", f"current_winstreak_{alias}", f"{alias}_winstreak"):
            value = stats.get(key)
            if value is not None:
                return as_int(value)
    return 0


def best_winstreak(stats: dict[str, Any], mode: dict[str, Any]) -> int:
    if mode["value"] == "overall":
        return first_mode_stat(stats, [], ["best_overall_winstreak", "best_winstreak"])
    for alias in mode["streak_aliases"]:
        for key in (f"best_{alias}_winstreak", f"best_winstreak_{alias}"):
            value = stats.get(key)
            if value is not None:
                return as_int(value)
    return 0


def mode_stats(stats: dict[str, Any], mode: dict[str, Any]) -> dict[str, int]:
    prefixes = mode["prefixes"]
    wins = sum_mode_stat(stats, prefixes, "wins")
    losses = sum_mode_stat(stats, prefixes, "losses")
    kills = sum_mode_stat(stats, prefixes, "kills")
    deaths = sum_mode_stat(stats, prefixes, "deaths")
    games = sum_mode_stat(stats, prefixes, "rounds_played") or wins + losses
    return {
        "wins": wins,
        "losses": losses,
        "kills": kills,
        "deaths": deaths,
        "games": games,
        "melee_hits": sum_mode_stat(stats, prefixes, "melee_hits"),
        "melee_swings": sum_mode_stat(stats, prefixes, "melee_swings"),
        "bow_hits": sum_mode_stat(stats, prefixes, "bow_hits"),
        "bow_shots": sum_mode_stat(stats, prefixes, "bow_shots"),
        "blocks_placed": sum_mode_stat(stats, prefixes, "blocks_placed"),
        "goals": sum_mode_stat(stats, prefixes, "goals"),
        "current_winstreak": current_winstreak(stats, mode),
        "best_winstreak": best_winstreak(stats, mode),
    }


class DuelsModeSelect(discord.ui.Select):
    def __init__(self, view: "DuelsView"):
        self.duels_view = view
        options = [
            discord.SelectOption(
                label=mode["label"],
                value=mode["value"],
                description=mode["description"],
                default=(mode["value"] == view.selected_mode),
            )
            for mode in DUEL_MODES
        ]
        super().__init__(placeholder="Choose a Duels mode", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.duels_view.owner_id:
            return await interaction.response.send_message("This Duels menu belongs to another user.", ephemeral=True)

        self.duels_view.selected_mode = self.values[0]
        self.duels_view.clear_items()
        self.duels_view.add_item(DuelsModeSelect(self.duels_view))
        await interaction.response.edit_message(
            embed=self.duels_view.build_embed(),
            view=self.duels_view,
        )


class DuelsView(discord.ui.View):
    def __init__(self, owner_id: int, bundle, player: dict[str, Any]):
        super().__init__(timeout=180)
        self.owner_id = owner_id
        self.bundle = bundle
        self.player = player
        self.duels = (player.get("stats", {}) or {}).get("Duels", {}) or {}
        self.selected_mode = "overall"
        self.add_item(DuelsModeSelect(self))

    def build_embed(self) -> discord.Embed:
        mode = get_mode(self.selected_mode)
        stats = mode_stats(self.duels, mode)
        displayname = self.player.get("displayname", self.bundle.username)
        rank = get_rank(self.player)
        level = network_level(self.player.get("networkExp"))

        embed = discord.Embed(
            title=f"{displayname} | Duels - {mode['label']}",
            description=f"Rank: `{rank}` | Network Level: `{level:.2f}`",
            color=RANK_COLORS.get(rank, discord.Color.dark_grey()),
        )
        embed.add_field(name="Wins", value=format_number(stats["wins"]), inline=True)
        embed.add_field(name="Losses", value=format_number(stats["losses"]), inline=True)
        embed.add_field(name="W/L Ratio", value=str(ratio(stats["wins"], stats["losses"])), inline=True)
        embed.add_field(name="Kills", value=format_number(stats["kills"]), inline=True)
        embed.add_field(name="Deaths", value=format_number(stats["deaths"]), inline=True)
        embed.add_field(name="KDR", value=str(ratio(stats["kills"], stats["deaths"])), inline=True)
        embed.add_field(name="Games", value=format_number(stats["games"]), inline=True)
        embed.add_field(name="Current WS", value=format_number(stats["current_winstreak"]), inline=True)
        embed.add_field(name="Best WS", value=format_number(stats["best_winstreak"]), inline=True)

        extra_lines = [
            f"Melee Accuracy: `{percent(stats['melee_hits'], stats['melee_swings'])}%`",
            f"Bow Accuracy: `{percent(stats['bow_hits'], stats['bow_shots'])}%`",
            f"Blocks Placed: `{format_number(stats['blocks_placed'])}`",
        ]
        if stats["goals"]:
            extra_lines.append(f"Bridge Goals: `{format_number(stats['goals'])}`")
        embed.add_field(name="Detailed Stats", value="\n".join(extra_lines), inline=False)

        if not any(stats.values()):
            embed.add_field(name="Note", value="No public stats were found for this selected Duels mode.", inline=False)

        embed.set_thumbnail(url=f"https://visage.surgeplay.com/head/{self.bundle.uuid}.png")
        embed.set_footer(text="Use the dropdown to switch Duels mode. Data fetched from the official Hypixel API.")
        return embed


class Duels(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.api_key = bot.HYPIXEL_API_KEY

    @commands.command(name="duels", aliases=["duel"], help="Show detailed Duels stats with a selectable mode menu.")
    async def duels(self, ctx: commands.Context, username: str):
        try:
            bundle = await fetch_hypixel_player(self.api_key, username)
        except HypixelClientError as exc:
            return await ctx.send(f"Error: {exc}")

        view = DuelsView(ctx.author.id, bundle, bundle.player)
        await ctx.send(embed=view.build_embed(), view=view)

    @duels.error
    async def duels_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingRequiredArgument):
            return await ctx.send("Usage: `!duels <minecraft_username>`")
        raise error


async def setup(bot):
    cog = Duels(bot)
    for command in cog.get_commands():
        command.category = "Hypixel"
    await bot.add_cog(cog)
