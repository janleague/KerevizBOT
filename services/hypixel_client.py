from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import aiohttp


HTTP_TIMEOUT = aiohttp.ClientTimeout(total=12)
MOJANG_PROFILE_URL = "https://api.mojang.com/users/profiles/minecraft/{username}"
HYPIXEL_PLAYER_URL = "https://api.hypixel.net/v2/player"


class HypixelClientError(RuntimeError):
    pass


class HypixelConfigError(HypixelClientError):
    pass


class MinecraftPlayerNotFound(HypixelClientError):
    pass


class HypixelUnavailable(HypixelClientError):
    pass


@dataclass(slots=True)
class HypixelPlayerBundle:
    username: str
    uuid: str
    player: dict[str, Any]


def clean_username(username: str) -> str:
    return username.strip()


async def fetch_hypixel_player(api_key: str | None, username: str) -> HypixelPlayerBundle:
    if not api_key:
        raise HypixelConfigError("HYPIXEL_API_KEY is not configured.")

    cleaned = clean_username(username)
    if not cleaned:
        raise MinecraftPlayerNotFound("Player not found.")

    async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
        uuid, resolved_name = await resolve_minecraft_profile(session, cleaned)
        player = await fetch_player_data(session, api_key, uuid)

    return HypixelPlayerBundle(username=resolved_name, uuid=uuid, player=player)


async def resolve_minecraft_profile(session: aiohttp.ClientSession, username: str) -> tuple[str, str]:
    try:
        async with session.get(MOJANG_PROFILE_URL.format(username=username.lower())) as response:
            if response.status != 200:
                raise MinecraftPlayerNotFound("Player not found.")
            data = await response.json()
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        raise HypixelUnavailable("Failed to contact Mojang.") from exc

    uuid = str(data.get("id") or "").strip()
    name = str(data.get("name") or username).strip()
    if not uuid:
        raise MinecraftPlayerNotFound("Player not found.")
    return uuid, name


async def fetch_player_data(session: aiohttp.ClientSession, api_key: str, uuid: str) -> dict[str, Any]:
    headers = {"API-Key": api_key}
    params = {"uuid": uuid}
    try:
        async with session.get(HYPIXEL_PLAYER_URL, headers=headers, params=params) as response:
            if response.status == 403:
                raise HypixelConfigError("Hypixel API key is invalid or forbidden.")
            if response.status == 429:
                raise HypixelUnavailable("Hypixel API rate limit reached.")
            if response.status != 200:
                raise HypixelUnavailable(f"Hypixel API returned HTTP {response.status}.")
            data = await response.json()
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        raise HypixelUnavailable("Failed to contact Hypixel.") from exc

    if not data.get("success"):
        cause = data.get("cause") or "Hypixel request failed."
        raise HypixelUnavailable(str(cause))
    player = data.get("player")
    if not isinstance(player, dict):
        raise MinecraftPlayerNotFound("This player has never joined Hypixel.")
    return player


def get_rank(player: dict[str, Any]) -> str:
    if player.get("rank") and player["rank"] != "NORMAL":
        return str(player["rank"])
    if player.get("monthlyPackageRank") == "SUPERSTAR":
        return "MVP++"
    if player.get("newPackageRank"):
        return str(player["newPackageRank"]).replace("_PLUS", "+").replace("_", "")
    if player.get("packageRank"):
        return str(player["packageRank"]).replace("_PLUS", "+").replace("_", "")
    return "None"


def network_level(network_exp: int | float | None) -> float:
    exp = float(network_exp or 0)
    return max(1.0, (math.sqrt((2 * exp) + 30625) / 50) - 2.5)


def ratio(numerator: int | float, denominator: int | float) -> float:
    return round(float(numerator or 0) / float(denominator or 1), 2)


def percent(numerator: int | float, denominator: int | float) -> float:
    return round(ratio(numerator, denominator) * 100, 1)


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def format_number(value: Any) -> str:
    return f"{as_int(value):,}"


def format_timestamp(ms_value: Any) -> str:
    timestamp_ms = as_int(ms_value)
    if timestamp_ms <= 0:
        return "Unknown"
    timestamp = int(timestamp_ms / 1000)
    try:
        datetime.fromtimestamp(timestamp, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return "Unknown"
    return f"<t:{timestamp}:R>"
