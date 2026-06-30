import unittest

from commands.subscriber_verification import (
    SUBMISSION_COOLDOWN_SECONDS,
    format_cooldown,
    is_supported_image_file,
    seconds_until_next_submission,
)
from services.subscriber_verification_store import normalize_panel, normalize_request


class SubscriberVerificationConfigTests(unittest.TestCase):
    def test_validates_screenshot_image_files(self):
        self.assertTrue(is_supported_image_file("proof.txt", "image/png"))
        self.assertTrue(is_supported_image_file("proof.jpg", None))
        self.assertTrue(is_supported_image_file("proof.webp", "application/octet-stream"))
        self.assertFalse(is_supported_image_file("proof.pdf", "application/pdf"))
        self.assertFalse(is_supported_image_file("proof.txt", None))

    def test_member_can_submit_once_per_24_hours(self):
        records = {
            "old": {"guild_id": 1, "user_id": 10, "created_at": 1000},
            "recent": {"guild_id": 1, "user_id": 10, "created_at": 2000},
            "other_user": {"guild_id": 1, "user_id": 11, "created_at": 2500},
            "other_guild": {"guild_id": 2, "user_id": 10, "created_at": 2500},
        }

        remaining = seconds_until_next_submission(records, 1, 10, current_ts=2600)
        self.assertEqual(remaining, SUBMISSION_COOLDOWN_SECONDS - 600)
        self.assertEqual(seconds_until_next_submission(records, 1, 11, current_ts=2600), SUBMISSION_COOLDOWN_SECONDS - 100)
        self.assertEqual(seconds_until_next_submission(records, 1, 10, current_ts=2000 + SUBMISSION_COOLDOWN_SECONDS), 0)

    def test_formats_cooldown_cleanly(self):
        self.assertEqual(format_cooldown(60), "1m")
        self.assertEqual(format_cooldown(3600), "1h")
        self.assertEqual(format_cooldown(3661), "1h 2m")


class SubscriberVerificationStoreTests(unittest.TestCase):
    def test_normalizes_request_ids_and_status(self):
        record = normalize_request(
            {
                "id": 123,
                "guild_id": "1",
                "user_id": "2",
                "created_at": "3",
                "public_message_id": "4",
                "review_message_id": "5",
                "youtube_username": "  @kereviz  ",
                "screenshot_url": "  https://example.com/proof.png  ",
            }
        )

        self.assertEqual(record["id"], "123")
        self.assertEqual(record["guild_id"], 1)
        self.assertEqual(record["user_id"], 2)
        self.assertEqual(record["created_at"], 3)
        self.assertEqual(record["public_message_id"], 4)
        self.assertEqual(record["review_message_id"], 5)
        self.assertEqual(record["youtube_username"], "@kereviz")
        self.assertEqual(record["screenshot_url"], "https://example.com/proof.png")
        self.assertEqual(record["status"], "pending")

    def test_normalizes_panel_ids(self):
        panel = normalize_panel({"guild_id": "1", "channel_id": "2", "message_id": "3"})
        self.assertEqual(panel["guild_id"], 1)
        self.assertEqual(panel["channel_id"], 2)
        self.assertEqual(panel["message_id"], 3)
        self.assertEqual(panel["status"], "active")


if __name__ == "__main__":
    unittest.main()
