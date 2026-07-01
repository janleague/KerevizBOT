import unittest

from services.hypixel_client import (
    HypixelRateLimit,
    MinecraftPlayerNotFound,
    clean_username,
    clear_hypixel_cache,
    fetch_player_data,
    format_hypixel_error,
    parse_rate_limit_headers,
    resolve_minecraft_profile,
)


class FakeResponse:
    def __init__(self, status, data=None, headers=None):
        self.status = status
        self._data = data if data is not None else {}
        self.headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def json(self):
        return self._data


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError("No fake response queued.")
        return self.responses.pop(0)


class HypixelClientTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        clear_hypixel_cache()

    def tearDown(self):
        clear_hypixel_cache()

    def test_clean_username_rejects_invalid_input(self):
        self.assertEqual(clean_username(" KerevizMax "), "KerevizMax")
        with self.assertRaises(MinecraftPlayerNotFound):
            clean_username("bad name")
        with self.assertRaises(MinecraftPlayerNotFound):
            clean_username("ab")

    async def test_caches_mojang_profile_lookup(self):
        session = FakeSession(
            FakeResponse(200, {"id": "abc123", "name": "KerevizMax"}),
        )

        first = await resolve_minecraft_profile(session, "KerevizMax")
        second = await resolve_minecraft_profile(session, "kerevizmax")

        self.assertEqual(first, ("abc123", "KerevizMax"))
        self.assertEqual(second, ("abc123", "KerevizMax"))
        self.assertEqual(len(session.calls), 1)

    async def test_caches_hypixel_player_data(self):
        session = FakeSession(
            FakeResponse(
                200,
                {"success": True, "player": {"displayname": "KerevizMax"}},
                {"RateLimit-Remaining": "10", "RateLimit-Reset": "60"},
            ),
        )

        first = await fetch_player_data(session, "api-key", "abc123")
        second = await fetch_player_data(session, "api-key", "abc123")

        self.assertEqual(first["displayname"], "KerevizMax")
        self.assertEqual(second["displayname"], "KerevizMax")
        self.assertIsNot(first, second)
        self.assertEqual(len(session.calls), 1)

    async def test_rate_limit_uses_retry_header(self):
        session = FakeSession(
            FakeResponse(
                429,
                {"success": False, "cause": "Too many requests"},
                {
                    "RateLimit-Limit": "300",
                    "RateLimit-Remaining": "0",
                    "RateLimit-Reset": "30",
                },
            ),
        )

        with self.assertRaises(HypixelRateLimit) as raised:
            await fetch_player_data(session, "api-key", "abc123")

        self.assertEqual(raised.exception.retry_after, 30)
        self.assertEqual(raised.exception.limit, 300)
        self.assertEqual(format_hypixel_error(raised.exception), "Hypixel API is rate-limiting requests. Try again in about 30s.")

    def test_parses_rate_limit_headers(self):
        headers = parse_rate_limit_headers(
            {
                "RateLimit-Limit": "120",
                "RateLimit-Remaining": "4",
                "RateLimit-Reset": "12",
            }
        )

        self.assertEqual(headers.limit, 120)
        self.assertEqual(headers.remaining, 4)
        self.assertEqual(headers.reset_after, 12)


if __name__ == "__main__":
    unittest.main()
