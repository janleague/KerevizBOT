from __future__ import annotations

import asyncio
import copy
import math
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import aiohttp


HTTP_TIMEOUT = aiohttp.ClientTimeout(total=12)
MOJANG_PROFILE_URL = "https://api.mojang.com/users/profiles/minecraft/{username}"
HYPIXEL_PLAYER_URL = "https://api.hypixel.net/v2/player"
MINECRAFT_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,16}$")


def _positive_int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


PROFILE_CACHE_SECONDS = _positive_int_env("HYPIXEL_PROFILE_CACHE_SECONDS", 24 * 60 * 60)
PLAYER_CACHE_SECONDS = _positive_int_env("HYPIXEL_PLAYER_CACHE_SECONDS", 90)
RATE_LIMIT_RETRY_MAX_SECONDS = _positive_int_env("HYPIXEL_RATE_LIMIT_RETRY_MAX_SECONDS", 5)


class HypixelClientError(RuntimeError):
    pass


class HypixelConfigError(HypixelClientError):
    pass


class MinecraftPlayerNotFound(HypixelClientError):
    pass


class HypixelUnavailable(HypixelClientError):
    pass


class HypixelRateLimit(HypixelUnavailable):
    def __init__(
        self,
        message: str,
        *,
        retry_after: int | None = None,
        limit: int | None = None,
        remaining: int | None = None,
    ):
        super().__init__(message)
        self.retry_after = retry_after
        self.limit = limit
        self.remaining = remaining


@dataclass(slots=True)
class HypixelPlayerBundle:
    username: str
    uuid: str
    player: dict[str, Any]


@dataclass(slots=True)
class _CacheEntry:
    expires_at: float
    value: Any


@dataclass(slots=True)
class _RateLimitHeaders:
    limit: int | None = None
    remaining: int | None = None
    reset_after: int | None = None


_cache_lock = asyncio.Lock()
_profile_cache: dict[str, _CacheEntry] = {}
_player_cache: dict[str, _CacheEntry] = {}
_rate_limited_until = 0.0


def clear_hypixel_cache() -> None:
    global _rate_limited_until
    _profile_cache.clear()
    _player_cache.clear()
    _rate_limited_until = 0.0


def clean_username(username: str) -> str:
    cleaned = str(username or "").strip()
    if not MINECRAFT_USERNAME_RE.fullmatch(cleaned):
        raise MinecraftPlayerNotFound("Player not found. Check the Minecraft username spelling.")
    return cleaned


async def fetch_hypixel_player(api_key: str | None, username: str) -> HypixelPlayerBundle:
    if not api_key:
        raise HypixelConfigError("Hypixel API key is not configured.")

    cleaned = clean_username(username)
    async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
        uuid, resolved_name = await resolve_minecraft_profile(session, cleaned)
        player = await fetch_player_data(session, api_key, uuid)

    return HypixelPlayerBundle(username=resolved_name, uuid=uuid, player=player)


async def resolve_minecraft_profile(session: aiohttp.ClientSession, username: str) -> tuple[str, str]:
    cleaned = clean_username(username)
    cache_key = cleaned.casefold()
    cached = await _get_cache(_profile_cache, cache_key)
    if cached:
        return cached

    try:
        async with session.get(MOJANG_PROFILE_URL.format(username=cleaned.lower())) as response:
            if response.status == 429:
                raise HypixelUnavailable("Mojang is rate-limiting username lookups. Please try again shortly.")
            if response.status != 200:
                raise MinecraftPlayerNotFound("Player not found. Check the Minecraft username spelling.")
            data = await response.json()
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        raise HypixelUnavailable("Failed to contact Mojang. Please try again shortly.") from exc

    uuid = str(data.get("id") or "").strip()
    name = str(data.get("name") or cleaned).strip()
    if not uuid:
        raise MinecraftPlayerNotFound("Player not found. Check the Minecraft username spelling.")

    value = (uuid, name)
    await _set_cache(_profile_cache, cache_key, value, PROFILE_CACHE_SECONDS)
    return value


async def fetch_player_data(session: aiohttp.ClientSession, api_key: str, uuid: str) -> dict[str, Any]:
    normalized_uuid = str(uuid or "").replace("-", "").strip().lower()
    cached = await _get_cache(_player_cache, normalized_uuid)
    if cached:
        return cached

    active_wait = _active_rate_limit_seconds()
    if active_wait > 0:
        raise _rate_limit_error(active_wait)

    headers = {"API-Key": api_key}
    params = {"uuid": normalized_uuid}
    data: dict[str, Any] | None = None

    for attempt in range(2):
        try:
            async with session.get(HYPIXEL_PLAYER_URL, headers=headers, params=params) as response:
                limits = parse_rate_limit_headers(response.headers)
                if response.status == 403:
                    raise HypixelConfigError("Hypixel API key is invalid or forbidden.")
                if response.status == 429:
                    retry_after = limits.reset_after or 60
                    _remember_rate_limit(retry_after)
                    if attempt == 0 and retry_after <= RATE_LIMIT_RETRY_MAX_SECONDS:
                        await asyncio.sleep(retry_after + 0.25)
                        continue
                    raise _rate_limit_error(
                        retry_after,
                        limit=limits.limit,
                        remaining=limits.remaining,
                    )
                if response.status != 200:
                    cause = await _response_cause(response)
                    raise HypixelUnavailable(cause or f"Hypixel API returned HTTP {response.status}.")

                try:
                    data = await response.json()
                except (aiohttp.ContentTypeError, ValueError) as exc:
                    raise HypixelUnavailable("Hypixel returned an invalid response. Please try again shortly.") from exc

                if limits.remaining == 0 and limits.reset_after:
                    _remember_rate_limit(limits.reset_after)
                break
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise HypixelUnavailable("Failed to contact Hypixel. Please try again shortly.") from exc

    if not isinstance(data, dict):
        raise HypixelUnavailable("Hypixel returned an empty response. Please try again shortly.")
    if not data.get("success"):
        cause = data.get("cause") or "Hypixel request failed."
        raise HypixelUnavailable(str(cause))

    player = data.get("player")
    if not isinstance(player, dict):
        raise MinecraftPlayerNotFound("This player has never joined Hypixel.")

    await _set_cache(_player_cache, normalized_uuid, player, PLAYER_CACHE_SECONDS)
    return copy.deepcopy(player)


def parse_rate_limit_headers(headers: Any) -> _RateLimitHeaders:
    return _RateLimitHeaders(
        limit=_header_int(headers, "RateLimit-Limit"),
        remaining=_header_int(headers, "RateLimit-Remaining"),
        reset_after=_header_int(headers, "RateLimit-Reset"),
    )


def format_hypixel_error(exc: HypixelClientError) -> str:
    if isinstance(exc, HypixelRateLimit):
        retry_text = f" Try again in about {format_retry_after(exc.retry_after)}." if exc.retry_after else ""
        return f"Hypixel API is rate-limiting requests.{retry_text}"
    return f"Error: {exc}"


def format_retry_after(seconds: int | None) -> str:
    if seconds is None:
        return "a minute"
    seconds = max(1, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes = (seconds + 59) // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = (minutes + 59) // 60
    return f"{hours}h"


async def _get_cache(cache: dict[str, _CacheEntry], key: str) -> Any | None:
    if not key:
        return None
    async with _cache_lock:
        entry = cache.get(key)
        if entry is None:
            return None
        if entry.expires_at <= time.monotonic():
            cache.pop(key, None)
            return None
        return copy.deepcopy(entry.value)


async def _set_cache(cache: dict[str, _CacheEntry], key: str, value: Any, ttl: int) -> None:
    if not key or ttl <= 0:
        return
    async with _cache_lock:
        cache[key] = _CacheEntry(time.monotonic() + ttl, copy.deepcopy(value))


def _header_int(headers: Any, name: str) -> int | None:
    try:
        value = headers.get(name)
    except AttributeError:
        value = None
    if value is None:
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def _active_rate_limit_seconds() -> int:
    remaining = _rate_limited_until - time.monotonic()
    return max(0, math.ceil(remaining))


def _remember_rate_limit(retry_after: int | None) -> None:
    global _rate_limited_until
    if not retry_after:
        return
    _rate_limited_until = max(_rate_limited_until, time.monotonic() + max(1, int(retry_after)))


def _rate_limit_error(
    retry_after: int | None,
    *,
    limit: int | None = None,
    remaining: int | None = None,
) -> HypixelRateLimit:
    return HypixelRateLimit(
        "Hypixel API rate limit reached.",
        retry_after=retry_after,
        limit=limit,
        remaining=remaining,
    )


async def _response_cause(response: aiohttp.ClientResponse) -> str | None:
    try:
        data = await response.json()
    except (aiohttp.ContentTypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    cause = data.get("cause")
    return str(cause).strip() if cause else None


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
