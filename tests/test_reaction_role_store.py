import unittest

from services.reaction_role_store import normalize_panel


class ReactionRoleStoreTests(unittest.TestCase):
    def test_normalizes_panel_ids(self):
        panel = normalize_panel(
            {
                "guild_id": "123",
                "channel_id": "456",
                "message_id": "789",
                "created_by_id": "42",
            }
        )

        self.assertEqual(panel["guild_id"], 123)
        self.assertEqual(panel["channel_id"], 456)
        self.assertEqual(panel["message_id"], 789)
        self.assertEqual(panel["created_by_id"], 42)
        self.assertEqual(panel["status"], "active")
        self.assertEqual(panel["panel_type"], "notification_roles")

    def test_ignores_empty_panel(self):
        self.assertEqual(normalize_panel(None), {})


if __name__ == "__main__":
    unittest.main()
