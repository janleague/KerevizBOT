from __future__ import annotations

from typing import Any

import discord
from discord.ext import commands

from services.hypixel_client import (
    HypixelClientError,
    as_float,
    as_int,
    fetch_skyblock_profile,
    format_hypixel_error,
    format_number,
    format_timestamp,
    nested_value,
)


SKILL_XP_STEPS = [
    50,
    125,
    200,
    300,
    500,
    750,
    1000,
    1500,
    2000,
    3500,
    5000,
    7500,
    10000,
    15000,
    20000,
    30000,
    50000,
    75000,
    100000,
    200000,
    300000,
    400000,
    500000,
    600000,
    700000,
    800000,
    900000,
    1000000,
    1100000,
    1200000,
    1300000,
    1400000,
    1500000,
    1600000,
    1700000,
    1800000,
    1900000,
    2000000,
    2100000,
    2200000,
    2300000,
    2400000,
    2500000,
    2600000,
    2750000,
    2900000,
    3100000,
    3400000,
    3700000,
    4000000,
    4300000,
    4600000,
    4900000,
    5200000,
    5500000,
    5800000,
    6100000,
    6400000,
    6700000,
    7000000,
]

DUNGEON_XP_STEPS = [
    50,
    75,
    110,
    160,
    230,
    330,
    470,
    670,
    950,
    1340,
    1890,
    2665,
    3760,
    5260,
    7380,
    10300,
    14400,
    20000,
    27600,
    38000,
    52500,
    71500,
    97000,
    132000,
    180000,
    243000,
    328000,
    445000,
    600000,
    800000,
    1065000,
    1410000,
    1900000,
    2500000,
    3300000,
    4300000,
    5600000,
    7200000,
    9200000,
    12000000,
    15000000,
    19000000,
    24000000,
    30000000,
    38000000,
    48000000,
    60000000,
    75000000,
    93000000,
    116250000,
]

CORE_SKILLS = [
    ("Farming", "farming", "SKILL_FARMING"),
    ("Mining", "mining", "SKILL_MINING"),
    ("Combat", "combat", "SKILL_COMBAT"),
    ("Foraging", "foraging", "SKILL_FORAGING"),
    ("Fishing", "fishing", "SKILL_FISHING"),
    ("Enchanting", "enchanting", "SKILL_ENCHANTING"),
    ("Alchemy", "alchemy", "SKILL_ALCHEMY"),
    ("Taming", "taming", "SKILL_TAMING"),
]

EXTRA_SKILLS = [
    ("Carpentry", "carpentry", "SKILL_CARPENTRY"),
    ("Runecrafting", "runecrafting", "SKILL_RUNECRAFTING"),
    ("Social", "social", "SKILL_SOCIAL"),
]

SLAYERS = [
    ("Revenant", "zombie"),
    ("Tarantula", "spider"),
    ("Sven", "wolf"),
    ("Enderman", "enderman"),
    ("Blaze", "blaze"),
    ("Vampire", "vampire"),
]

CLASS_NAMES = [
    ("Healer", "healer"),
    ("Mage", "mage"),
    ("Berserk", "berserk"),
    ("Archer", "archer"),
    ("Tank", "tank"),
]

PAGE_LABELS = {
    "overview": "Overview",
    "skills": "Skills",
    "slayer": "Slayer",
    "dungeons": "Dungeons",
    "mining": "Mining & Rift",
    "collections": "Collections & Pets",
}


def compact_number(value: Any) -> str:
    number = as_float(value)
    abs_number = abs(number)
    for suffix, divisor in (("B", 1_000_000_000), ("M", 1_000_000), ("K", 1_000)):
        if abs_number >= divisor:
            return f"{number / divisor:.2f}{suffix}"
    if number == int(number):
        return f"{int(number):,}"
    return f"{number:,.2f}"


def titleize_id(value: Any) -> str:
    text = str(value or "Unknown").replace(":", " ").replace("_", " ").replace("-", " ")
    return " ".join(part.capitalize() for part in text.split())


def trim(value: str, limit: int = 1024) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def level_from_xp(xp: Any, steps: list[int], cap: int | None = None) -> float:
    remaining = as_float(xp)
    level = 0
    max_level = cap or len(steps)
    for required in steps[:max_level]:
        if remaining < required:
            return round(level + (remaining / required), 2)
        remaining -= required
        level += 1
    return float(max_level)


def skyblock_level(member: dict[str, Any]) -> float:
    return round(as_float(nested_value(member, ("leveling", "experience"))) / 100, 2)


def profile_bank(profile: dict[str, Any]) -> str:
    value = nested_value(profile, ("banking", "balance"))
    return "Hidden or unavailable" if value is None else compact_number(value)


def purse(member: dict[str, Any]) -> str:
    value = nested_value(member, ("currencies", "coin_purse"))
    if value is None:
        value = member.get("coin_purse")
    return "Hidden or unavailable" if value is None else compact_number(value)


def skill_experience(member: dict[str, Any], legacy_name: str, api_name: str) -> float | None:
    direct = member.get(f"experience_skill_{legacy_name}")
    if direct is not None:
        return as_float(direct)
    modern = nested_value(member, ("player_data", "experience", api_name))
    if modern is not None:
        return as_float(modern)
    return None


def skill_rows(member: dict[str, Any], skills: list[tuple[str, str, str]]) -> list[tuple[str, float, float]]:
    rows = []
    for label, legacy_name, api_name in skills:
        xp = skill_experience(member, legacy_name, api_name)
        if xp is None:
            continue
        rows.append((label, level_from_xp(xp, SKILL_XP_STEPS, 60), xp))
    return rows


def core_skill_average(member: dict[str, Any]) -> str:
    rows = skill_rows(member, CORE_SKILLS)
    if not rows:
        return "Hidden or unavailable"
    return f"{sum(row[1] for row in rows) / len(rows):.2f}"


def active_or_best_pet(member: dict[str, Any]) -> str:
    pets = member.get("pets") or []
    if not isinstance(pets, list) or not pets:
        return "Hidden or unavailable"

    active = next((pet for pet in pets if isinstance(pet, dict) and pet.get("active")), None)
    if active is None:
        active = max(
            (pet for pet in pets if isinstance(pet, dict)),
            key=lambda pet: as_float(pet.get("exp")),
            default=None,
        )
    if not active:
        return "Hidden or unavailable"
    tier = str(active.get("tier") or "").replace("_", " ").title()
    pet_type = titleize_id(active.get("type"))
    exp = compact_number(active.get("exp", 0))
    held_item = active.get("heldItem")
    suffix = f"\nItem: `{titleize_id(held_item)}`" if held_item else ""
    return f"{tier} {pet_type}\nEXP: `{exp}`{suffix}"


def total_slayer_xp(member: dict[str, Any]) -> int:
    bosses = member.get("slayer_bosses") or {}
    if not isinstance(bosses, dict):
        return 0
    return sum(as_int(data.get("xp")) for data in bosses.values() if isinstance(data, dict))


def slayer_level(data: dict[str, Any]) -> int:
    claimed = data.get("claimed_levels") or {}
    if not isinstance(claimed, dict):
        return 0
    return sum(1 for value in claimed.values() if value)


def catacombs_data(member: dict[str, Any]) -> dict[str, Any]:
    return nested_value(member, ("dungeons", "dungeon_types", "catacombs"), {}) or {}


def floor_label(raw: Any) -> str:
    value = str(raw)
    if value == "0":
        return "E"
    if value.isdigit():
        return f"F{value}"
    return value.upper()


def stat_value(member: dict[str, Any], *paths: tuple[str, ...]) -> Any:
    for path in paths:
        value = nested_value(member, path)
        if value is not None:
            return value
    return None


def minion_count(member: dict[str, Any]) -> int:
    crafted = member.get("crafted_generators") or []
    return len(set(crafted)) if isinstance(crafted, list) else 0


class SkyBlockPageSelect(discord.ui.Select):
    def __init__(self, view: "SkyBlockView"):
        self.skyblock_view = view
        options = [
            discord.SelectOption(
                label=label,
                value=value,
                default=value == view.page,
            )
            for value, label in PAGE_LABELS.items()
        ]
        super().__init__(placeholder="SkyBlock pages", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.skyblock_view.owner_id:
            return await interaction.response.send_message("This SkyBlock menu belongs to another user.", ephemeral=True)

        self.skyblock_view.page = self.values[0]
        self.skyblock_view.refresh_items()
        await interaction.response.edit_message(
            embed=self.skyblock_view.build_embed(),
            view=self.skyblock_view,
        )


class SkyBlockView(discord.ui.View):
    def __init__(self, owner_id: int, bundle):
        super().__init__(timeout=180)
        self.owner_id = owner_id
        self.bundle = bundle
        self.page = "overview"
        self.refresh_items()

    def refresh_items(self) -> None:
        self.clear_items()
        self.add_item(SkyBlockPageSelect(self))

    def build_embed(self) -> discord.Embed:
        builders = {
            "overview": self._overview_embed,
            "skills": self._skills_embed,
            "slayer": self._slayer_embed,
            "dungeons": self._dungeons_embed,
            "mining": self._mining_embed,
            "collections": self._collections_embed,
        }
        return builders.get(self.page, self._overview_embed)()

    def _base_embed(self, title_suffix: str) -> discord.Embed:
        profile = self.bundle.profile
        member = self.bundle.member
        selected = "Selected" if profile.get("selected") is True else "Best available"
        embed = discord.Embed(
            title=f"{self.bundle.username} | SkyBlock - {title_suffix}",
            description=(
                f"Profile: `{self.bundle.profile_name}` | Mode: `{self.bundle.game_mode}` | {selected}\n"
                f"SkyBlock Level: `{skyblock_level(member):.2f}`"
            ),
            color=discord.Color.gold(),
        )
        embed.set_thumbnail(url=f"https://visage.surgeplay.com/head/{self.bundle.uuid}.png")
        embed.set_footer(text="Official Hypixel SkyBlock API. Some fields can be hidden by player API settings.")
        return embed

    def _overview_embed(self) -> discord.Embed:
        member = self.bundle.member
        profile = self.bundle.profile
        catacombs = catacombs_data(member)
        catacombs_xp = as_float(catacombs.get("experience"))

        embed = self._base_embed("Overview")
        embed.add_field(name="Purse", value=purse(member), inline=True)
        embed.add_field(name="Bank", value=profile_bank(profile), inline=True)
        embed.add_field(name="Co-op Members", value=str(len(profile.get("members") or {})), inline=True)
        embed.add_field(name="Core Skill Avg", value=core_skill_average(member), inline=True)
        embed.add_field(name="Total Slayer XP", value=format_number(total_slayer_xp(member)), inline=True)
        embed.add_field(name="Catacombs", value=f"Level `{level_from_xp(catacombs_xp, DUNGEON_XP_STEPS, 50):.2f}`", inline=True)
        embed.add_field(name="Active/Best Pet", value=active_or_best_pet(member), inline=True)
        embed.add_field(name="Unique Minions", value=format_number(minion_count(member)), inline=True)
        embed.add_field(name="Fairy Souls", value=format_number(member.get("fairy_souls_collected", member.get("fairy_souls", 0))), inline=True)
        embed.add_field(name="First Join", value=format_timestamp(member.get("first_join")), inline=True)
        embed.add_field(name="Last Save", value=format_timestamp(member.get("last_save")), inline=True)
        embed.add_field(name="Profile ID", value=f"`{self.bundle.profile_id}`", inline=False)
        return embed

    def _skills_embed(self) -> discord.Embed:
        member = self.bundle.member
        embed = self._base_embed("Skills")
        core_rows = skill_rows(member, CORE_SKILLS)
        extra_rows = skill_rows(member, EXTRA_SKILLS)

        if core_rows:
            embed.add_field(
                name="Core Skills",
                value=trim("\n".join(f"{name}: `{level:.2f}` ({compact_number(xp)} XP)" for name, level, xp in core_rows)),
                inline=False,
            )
            embed.add_field(name="Core Average", value=f"`{sum(row[1] for row in core_rows) / len(core_rows):.2f}`", inline=True)
        else:
            embed.add_field(name="Core Skills", value="Skill API data is hidden or unavailable.", inline=False)

        if extra_rows:
            embed.add_field(
                name="Cosmetic / Extra Skills",
                value=trim("\n".join(f"{name}: `{level:.2f}` ({compact_number(xp)} XP)" for name, level, xp in extra_rows)),
                inline=False,
            )

        kills = stat_value(member, ("player_stats", "kills"), ("stats", "kills"))
        deaths = stat_value(member, ("player_stats", "deaths"), ("stats", "deaths"))
        if kills is not None or deaths is not None:
            embed.add_field(
                name="Combat Snapshot",
                value=f"Kills: `{format_number(kills or 0)}`\nDeaths: `{format_number(deaths or 0)}`",
                inline=True,
            )
        return embed

    def _slayer_embed(self) -> discord.Embed:
        member = self.bundle.member
        bosses = member.get("slayer_bosses") or {}
        embed = self._base_embed("Slayer")

        lines = []
        for label, key in SLAYERS:
            data = bosses.get(key) if isinstance(bosses, dict) else None
            if not isinstance(data, dict):
                lines.append(f"{label}: `No data`")
                continue
            lines.append(f"{label}: `L{slayer_level(data)}` | `{format_number(data.get('xp', 0))}` XP")

        embed.add_field(name="Boss Progress", value=trim("\n".join(lines)), inline=False)
        embed.add_field(name="Total Slayer XP", value=format_number(total_slayer_xp(member)), inline=True)
        return embed

    def _dungeons_embed(self) -> discord.Embed:
        member = self.bundle.member
        dungeons = member.get("dungeons") or {}
        catacombs = catacombs_data(member)
        embed = self._base_embed("Dungeons")

        catacombs_xp = as_float(catacombs.get("experience"))
        embed.add_field(
            name="Catacombs",
            value=f"Level: `{level_from_xp(catacombs_xp, DUNGEON_XP_STEPS, 50):.2f}`\nXP: `{compact_number(catacombs_xp)}`",
            inline=True,
        )

        completions = catacombs.get("tier_completions") or {}
        if isinstance(completions, dict) and completions:
            completion_lines = [
                f"{floor_label(floor)}: `{format_number(count)}`"
                for floor, count in sorted(completions.items(), key=lambda item: str(item[0]))
                if as_int(count) > 0
            ]
            embed.add_field(name="Floor Completions", value=trim("\n".join(completion_lines) or "No completions found."), inline=True)

        classes = dungeons.get("player_classes") or dungeons.get("classes") or {}
        class_lines = []
        if isinstance(classes, dict):
            for label, key in CLASS_NAMES:
                data = classes.get(key) or {}
                xp = as_float(data.get("experience")) if isinstance(data, dict) else 0
                if xp:
                    class_lines.append(f"{label}: `{level_from_xp(xp, DUNGEON_XP_STEPS, 50):.2f}` ({compact_number(xp)} XP)")
        embed.add_field(name="Classes", value=trim("\n".join(class_lines) or "Class API data is hidden or unavailable."), inline=False)
        secrets = stat_value(member, ("dungeons", "secrets"), ("player_stats", "dungeons", "secrets"))
        if secrets is not None:
            embed.add_field(name="Secrets", value=format_number(secrets), inline=True)
        return embed

    def _mining_embed(self) -> discord.Embed:
        member = self.bundle.member
        mining_core = member.get("mining_core") or {}
        embed = self._base_embed("Mining & Rift")

        if isinstance(mining_core, dict) and mining_core:
            embed.add_field(
                name="Heart of the Mountain",
                value=(
                    f"HotM XP: `{compact_number(mining_core.get('experience', 0))}`\n"
                    f"Selected Ability: `{titleize_id(mining_core.get('selected_pickaxe_ability') or 'None')}`"
                ),
                inline=False,
            )
            embed.add_field(
                name="Powder",
                value=(
                    f"Mithril: `{compact_number(mining_core.get('powder_mithril', 0))}`\n"
                    f"Gemstone: `{compact_number(mining_core.get('powder_gemstone', 0))}`\n"
                    f"Glacite: `{compact_number(mining_core.get('powder_glacite', 0))}`"
                ),
                inline=True,
            )
            nodes = mining_core.get("nodes") or {}
            if isinstance(nodes, dict) and nodes:
                top_nodes = sorted(nodes.items(), key=lambda item: as_int(item[1]), reverse=True)[:8]
                embed.add_field(
                    name="Top HotM Nodes",
                    value=trim("\n".join(f"{titleize_id(name)}: `{value}`" for name, value in top_nodes)),
                    inline=True,
                )
        else:
            embed.add_field(name="Mining", value="Mining API data is hidden or unavailable.", inline=False)

        rift = member.get("rift") or {}
        if isinstance(rift, dict) and rift:
            visited = nested_value(rift, ("village_plaza", "murder", "step_index"))
            embed.add_field(
                name="Rift",
                value=(
                    f"Data: `Available`\n"
                    f"Murder Progress: `{visited if visited is not None else 'Unknown'}`"
                ),
                inline=True,
            )
        else:
            embed.add_field(name="Rift", value="Rift API data is hidden or unavailable.", inline=True)
        return embed

    def _collections_embed(self) -> discord.Embed:
        member = self.bundle.member
        embed = self._base_embed("Collections & Pets")

        collection = member.get("collection") or {}
        if isinstance(collection, dict) and collection:
            top_collections = sorted(collection.items(), key=lambda item: as_float(item[1]), reverse=True)[:10]
            embed.add_field(
                name="Top Collections",
                value=trim("\n".join(f"{titleize_id(name)}: `{compact_number(amount)}`" for name, amount in top_collections)),
                inline=False,
            )
        else:
            embed.add_field(name="Top Collections", value="Collection API data is hidden or unavailable.", inline=False)

        crafted = member.get("crafted_generators") or []
        if isinstance(crafted, list) and crafted:
            embed.add_field(
                name="Minion Crafts",
                value=trim(f"Unique crafted minions: `{len(set(crafted))}`\n" + ", ".join(titleize_id(item) for item in crafted[:12])),
                inline=False,
            )

        pets = member.get("pets") or []
        if isinstance(pets, list) and pets:
            sorted_pets = sorted(
                [pet for pet in pets if isinstance(pet, dict)],
                key=lambda pet: as_float(pet.get("exp")),
                reverse=True,
            )[:5]
            pet_lines = [
                f"{str(pet.get('tier') or '').replace('_', ' ').title()} {titleize_id(pet.get('type'))}: `{compact_number(pet.get('exp', 0))}` XP"
                for pet in sorted_pets
            ]
            embed.add_field(name="Top Pets", value=trim("\n".join(pet_lines)), inline=False)
        else:
            embed.add_field(name="Pets", value="Pet API data is hidden or unavailable.", inline=False)
        return embed


class SkyBlock(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="skyblock", aliases=["sb"], help="Show detailed Hypixel SkyBlock profile info.")
    async def skyblock(self, ctx: commands.Context, username: str):
        async with ctx.typing():
            try:
                bundle = await fetch_skyblock_profile(self.bot.HYPIXEL_API_KEY, username)
            except HypixelClientError as exc:
                return await ctx.send(format_hypixel_error(exc))

        view = SkyBlockView(ctx.author.id, bundle)
        await ctx.send(embed=view.build_embed(), view=view)

    @skyblock.error
    async def skyblock_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingRequiredArgument):
            return await ctx.send("Usage: `!skyblock <minecraft_username>`")
        raise error


async def setup(bot):
    cog = SkyBlock(bot)
    for command in cog.get_commands():
        command.category = "Hypixel"
    await bot.add_cog(cog)
