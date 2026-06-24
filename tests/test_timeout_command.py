import unittest
from datetime import timedelta

from commands.timeout import build_timeout_duration, format_duration


class TimeoutCommandTests(unittest.TestCase):
    def test_builds_duration_from_parts(self):
        self.assertEqual(
            build_timeout_duration(days=1, hours=2, minutes=30),
            timedelta(days=1, hours=2, minutes=30),
        )

    def test_allows_zero_duration_for_removal(self):
        self.assertEqual(build_timeout_duration(days=0, hours=0, minutes=0), timedelta(0))
        self.assertEqual(format_duration(timedelta(0)), "Timeout removed")

    def test_rejects_duration_over_discord_limit(self):
        with self.assertRaises(ValueError):
            build_timeout_duration(days=28, hours=0, minutes=1)

    def test_formats_duration_cleanly(self):
        self.assertEqual(format_duration(timedelta(days=2, hours=1, minutes=5)), "2 days, 1 hour, 5 minutes")


if __name__ == "__main__":
    unittest.main()
