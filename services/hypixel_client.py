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
HYPIXEL_SKYBLOCK_PROFILES_URL = "https://api.hypixel.net/v2/skyblock/profiles"
MINECRAFT_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,16}$")


def _positive_int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


PROFILE_CACHE_SECONDS = _positive_int_env("HYPIXEL_PROFILE_CACHE_SECONDS", 24 * 60 * 60)
PLAYER_CACHE_SECONDS = _positive_int_env("HYPIXEL_PLAYER_CACHE_SECONDS", 90)
SKYBLOCK_PROFILE_CACHE_SECONDS = _positive_int_env("HYPIXEL_SKYBLOCK_PROFILE_CACHE_SECONDS", 120)
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
class SkyBlockProfileBundle:
    username: str
    uuid: str
    profile: dict[str, Any]
    member: dict[str, Any]

    @property
    def profile_id(self) -> str:
        return str(self.profile.get("profile_id") or "unknown")

    @property
    def profile_name(self) -> str:
        return str(self.profile.get("cute_name") or "Unknown")

    @property
    def game_mode(self) -> str:
        mode = str(self.profile.get("game_mode") or "normal").strip()
        return mode.replace("_", " ").title() if mode else "Normal"


@dataclass(slots=True)
class ProScore:
    score: int
    tier: str
    comment: str
    bar: str


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
_skyblock_profiles_cache: dict[str, _CacheEntry] = {}
_rate_limited_until = 0.0

GAME_TYPE_NAMES = {
    "QUAKECRAFT": "Quake",
    "WALLS": "Walls",
    "PAINTBALL": "Paintball",
    "HUNGERGAMES": "Blitz Survival Games",
    "SURVIVAL_GAMES": "Blitz Survival Games",
    "TNTGAMES": "TNT Games",
    "VAMPIREZ": "VampireZ",
    "WALLS3": "Mega Walls",
    "ARCADE": "Arcade",
    "ARENA": "Arena",
    "UHC": "UHC Champions",
    "MCGO": "Cops and Crims",
    "BATTLEGROUND": "Warlords",
    "SUPER_SMASH": "Smash Heroes",
    "GINGERBREAD": "Turbo Kart Racers",
    "HOUSING": "Housing",
    "SKYWARS": "SkyWars",
    "SPEED_UHC": "Speed UHC",
    "SKYCLASH": "SkyClash",
    "LEGACY": "Classic Games",
    "PROTOTYPE": "Prototype",
    "BEDWARS": "Bed Wars",
    "MURDER_MYSTERY": "Murder Mystery",
    "BUILD_BATTLE": "Build Battle",
    "DUELS": "Duels",
    "SKYBLOCK": "SkyBlock",
    "PIT": "The Pit",
    "REPLAY": "Replay",
    "SMP": "SMP",
    "WOOL_GAMES": "Wool Wars",
}


def clear_hypixel_cache() -> None:
    global _rate_limited_until
    _profile_cache.clear()
    _player_cache.clear()
    _skyblock_profiles_cache.clear()
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


async def fetch_skyblock_profile(api_key: str | None, username: str) -> SkyBlockProfileBundle:
    if not api_key:
        raise HypixelConfigError("Hypixel API key is not configured.")

    cleaned = clean_username(username)
    async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
        uuid, resolved_name = await resolve_minecraft_profile(session, cleaned)
        profiles = await fetch_skyblock_profiles(session, api_key, uuid)

    profile, member = choose_skyblock_profile(profiles, uuid)
    return SkyBlockProfileBundle(username=resolved_name, uuid=uuid, profile=profile, member=member)


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


async def fetch_skyblock_profiles(session: aiohttp.ClientSession, api_key: str, uuid: str) -> list[dict[str, Any]]:
    normalized_uuid = str(uuid or "").replace("-", "").strip().lower()
    cached = await _get_cache(_skyblock_profiles_cache, normalized_uuid)
    if cached is not None:
        return cached

    active_wait = _active_rate_limit_seconds()
    if active_wait > 0:
        raise _rate_limit_error(active_wait)

    headers = {"API-Key": api_key}
    params = {"uuid": normalized_uuid}
    data: dict[str, Any] | None = None

    for attempt in range(2):
        try:
            async with session.get(HYPIXEL_SKYBLOCK_PROFILES_URL, headers=headers, params=params) as response:
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
                    raise HypixelUnavailable(cause or f"Hypixel SkyBlock API returned HTTP {response.status}.")

                try:
                    data = await response.json()
                except (aiohttp.ContentTypeError, ValueError) as exc:
                    raise HypixelUnavailable("Hypixel SkyBlock returned an invalid response. Please try again shortly.") from exc

                if limits.remaining == 0 and limits.reset_after:
                    _remember_rate_limit(limits.reset_after)
                break
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise HypixelUnavailable("Failed to contact Hypixel SkyBlock. Please try again shortly.") from exc

    if not isinstance(data, dict):
        raise HypixelUnavailable("Hypixel SkyBlock returned an empty response. Please try again shortly.")
    if not data.get("success"):
        cause = data.get("cause") or "Hypixel SkyBlock request failed."
        raise HypixelUnavailable(str(cause))

    profiles = data.get("profiles") or []
    if not isinstance(profiles, list) or not profiles:
        raise MinecraftPlayerNotFound("No SkyBlock profiles were found for this player.")

    await _set_cache(_skyblock_profiles_cache, normalized_uuid, profiles, SKYBLOCK_PROFILE_CACHE_SECONDS)
    return copy.deepcopy(profiles)


def choose_skyblock_profile(profiles: list[dict[str, Any]], uuid: str) -> tuple[dict[str, Any], dict[str, Any]]:
    candidates: list[tuple[tuple[int, float, int], dict[str, Any], dict[str, Any]]] = []
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        member = skyblock_member(profile, uuid)
        if member is None:
            continue
        selected_score = 1 if profile.get("selected") is True else 0
        level_exp = nested_number(member, ("leveling", "experience"))
        last_save = as_int(member.get("last_save"))
        candidates.append(((selected_score, level_exp, last_save), profile, member))

    if not candidates:
        raise MinecraftPlayerNotFound("No SkyBlock profile data was found for this player.")
    candidates.sort(key=lambda item: item[0], reverse=True)
    _, profile, member = candidates[0]
    return copy.deepcopy(profile), copy.deepcopy(member)


def skyblock_member(profile: dict[str, Any], uuid: str) -> dict[str, Any] | None:
    members = profile.get("members") or {}
    if not isinstance(members, dict):
        return None

    normalized_uuid = str(uuid or "").replace("-", "").strip().lower()
    dashed_uuid = str(uuid or "").strip().lower()
    for key in (normalized_uuid, dashed_uuid):
        member = members.get(key)
        if isinstance(member, dict):
            return member

    for key, member in members.items():
        if str(key).replace("-", "").strip().lower() == normalized_uuid and isinstance(member, dict):
            return member
    return None


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


def last_game_name(player: dict[str, Any]) -> str:
    for key in ("mostRecentGameType", "lastGameType", "last_game_type", "lastGame", "gameType"):
        value = player.get(key)
        if value:
            return clean_game_type_name(value)
    return "Unknown"


def clean_game_type_name(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "Unknown"
    key = raw.replace("-", "_").replace(" ", "_").upper()
    return GAME_TYPE_NAMES.get(key, raw.replace("_", " ").title())


def nested_value(data: dict[str, Any], path: tuple[str, ...], default: Any = None) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def nested_number(data: dict[str, Any], path: tuple[str, ...], default: float = 0.0) -> float:
    return as_float(nested_value(data, path), default)


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def score_bar(score: int) -> str:
    filled = min(10, max(0, round(int(score) / 10)))
    return ("\U0001F7E9" * filled) + ("\u2B1C" * (10 - filled))


def score_tier(score: int) -> tuple[str, str]:
    if score >= 90:
        return "Elite", "Elite sweat. Very dangerous."
    if score >= 75:
        return "Pro", "Pro-level stats. Strong player."
    if score >= 60:
        return "Skilled", "Skilled player. Real threat."
    if score >= 40:
        return "Grinder", "Solid grinder, not pro yet."
    if score >= 20:
        return "Casual", "Casual stats. Keep grinding."
    return "Rookie", "Needs serious practice."


def bedwars_pro_score(
    *,
    wins: int,
    losses: int,
    kills: int,
    deaths: int,
    final_kills: int,
    final_deaths: int,
    beds_broken: int,
    beds_lost: int,
    level: int,
) -> ProScore:
    games = max(0, wins + losses)
    wlr = ratio(wins, losses)
    fkdr = ratio(final_kills, final_deaths)
    kdr = ratio(kills, deaths)
    bblr = ratio(beds_broken, beds_lost)

    raw = (
        _scale(wlr, [(0, 0), (0.25, 14), (0.5, 34), (1, 58), (2, 78), (4, 92), (8, 100)]) * 0.25
        + _scale(fkdr, [(0, 0), (0.5, 18), (1, 38), (2, 62), (4, 82), (8, 95), (12, 100)]) * 0.35
        + _scale(kdr, [(0, 0), (0.5, 20), (1, 45), (2, 68), (4, 88), (8, 100)]) * 0.15
        + _scale(bblr, [(0, 0), (0.5, 25), (1, 52), (2, 75), (4, 92), (8, 100)]) * 0.15
        + _scale(level, [(0, 0), (25, 10), (50, 22), (100, 40), (200, 65), (500, 95), (1000, 100)]) * 0.10
    )

    score = raw * _sample_confidence(games, 1500)
    if games < 25:
        score -= 18
    elif games < 100:
        score -= 8
    if games >= 50 and wlr < 0.25:
        score -= 12
    elif games >= 50 and wlr < 0.5:
        score -= 6
    if final_deaths >= 50 and fkdr < 0.75:
        score -= 10
    if deaths >= 100 and kdr < 0.6:
        score -= 5
    if games >= 1000 and wlr >= 2 and fkdr >= 4 and bblr >= 2:
        score += 6
    if level >= 200 and fkdr >= 3:
        score += 4

    return _build_pro_score(score)


def skywars_pro_score(
    *,
    wins: int,
    losses: int,
    kills: int,
    deaths: int,
    level: int,
) -> ProScore:
    games = max(0, wins + losses)
    wlr = ratio(wins, losses)
    kdr = ratio(kills, deaths)

    raw = (
        _scale(wlr, [(0, 0), (0.25, 14), (0.5, 34), (1, 58), (2, 78), (4, 92), (8, 100)]) * 0.32
        + _scale(kdr, [(0, 0), (0.5, 18), (1, 42), (2, 65), (4, 85), (8, 100)]) * 0.38
        + _scale(wins, [(0, 0), (100, 20), (500, 45), (1500, 70), (5000, 92), (10000, 100)]) * 0.15
        + _scale(level, [(0, 0), (5, 15), (10, 32), (15, 48), (25, 72), (40, 90), (60, 100)]) * 0.15
    )

    score = raw * _sample_confidence(games, 2000)
    if games < 25:
        score -= 18
    elif games < 100:
        score -= 8
    if games >= 50 and wlr < 0.25:
        score -= 12
    elif games >= 50 and wlr < 0.5:
        score -= 6
    if deaths >= 100 and kdr < 0.7:
        score -= 8
    if games >= 1500 and wlr >= 2 and kdr >= 3:
        score += 6
    if level >= 25 and kdr >= 2.5:
        score += 4

    return _build_pro_score(score)


def _scale(value: int | float, points: list[tuple[float, float]]) -> float:
    current = float(value or 0)
    if current <= points[0][0]:
        return points[0][1]
    for (left_value, left_score), (right_value, right_score) in zip(points, points[1:]):
        if current <= right_value:
            span = right_value - left_value
            if span <= 0:
                return right_score
            progress = (current - left_value) / span
            return left_score + (right_score - left_score) * progress
    return points[-1][1]


def _sample_confidence(sample_size: int, strong_sample: int) -> float:
    sample = max(0, int(sample_size or 0))
    if sample <= 0:
        return 0.45
    return min(1.0, 0.45 + 0.55 * (math.log10(sample + 1) / math.log10(strong_sample + 1)))


def _build_pro_score(score: float) -> ProScore:
    final_score = min(100, max(0, round(score)))
    tier, comment = score_tier(final_score)
    return ProScore(
        score=final_score,
        tier=tier,
        comment=comment,
        bar=score_bar(final_score),
    )


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
