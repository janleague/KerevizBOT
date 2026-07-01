import unittest

from commands.bedwars import Bedwars
from commands.skywars import Skywars


class HypixelCommandTests(unittest.TestCase):
    def test_bedwars_has_short_alias(self):
        command = next(command for command in Bedwars(bot=None).get_commands() if command.name == "bedwars")
        self.assertIn("bw", command.aliases)

    def test_skywars_has_short_alias(self):
        command = next(command for command in Skywars(bot=None).get_commands() if command.name == "skywars")
        self.assertIn("sw", command.aliases)


if __name__ == "__main__":
    unittest.main()
