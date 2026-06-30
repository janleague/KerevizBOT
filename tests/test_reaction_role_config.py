import unittest

from commands.auto_role import GIVEAWAY_EMOJI, REACTION_ROLE_BY_EMOJI, YOUTUBE_EMOJI


class ReactionRoleConfigTests(unittest.TestCase):
    def test_uses_configured_custom_emojis(self):
        self.assertEqual(YOUTUBE_EMOJI, "<:yt:1176188717040414851>")
        self.assertEqual(GIVEAWAY_EMOJI, "<:giveaway:1521432344651894916>")
        self.assertIn(YOUTUBE_EMOJI, REACTION_ROLE_BY_EMOJI)
        self.assertIn(GIVEAWAY_EMOJI, REACTION_ROLE_BY_EMOJI)


if __name__ == "__main__":
    unittest.main()
