import unittest

from commands.help import CATEGORY_META


class HelpCommandTests(unittest.TestCase):
    def test_custom_category_emojis_are_configured(self):
        self.assertEqual(CATEGORY_META["Admin"]["emoji"], "<:bhammer:1521924441976864968>")
        self.assertEqual(CATEGORY_META["Hypixel"]["emoji"], "<:Hypixel:1521923877096132678>")
        self.assertEqual(CATEGORY_META["Guard"]["emoji"], "<:bguard:1521924566992294070>")
        self.assertEqual(CATEGORY_META["Fun"]["emoji"], "\U0001F389")
        self.assertEqual(CATEGORY_META["General"]["emoji"], "\u2728")
        self.assertEqual(CATEGORY_META["Invites"]["emoji"], "\U0001F4E8")

    def test_removed_assistant_category_is_absent(self):
        self.assertNotIn("A" + "I", CATEGORY_META)


if __name__ == "__main__":
    unittest.main()
