import unittest

from commands.bedwars import Bedwars
from commands.skyblock import SkyBlock
from commands.skywars import Skywars


class HypixelCommandTests(unittest.TestCase):
    def test_bedwars_has_short_alias(self):
        command = next(command for command in Bedwars(bot=None).get_commands() if command.name == "bedwars")
        self.assertIn("bw", command.aliases)

    def test_skywars_has_short_alias(self):
        command = next(command for command in Skywars(bot=None).get_commands() if command.name == "skywars")
        self.assertIn("sw", command.aliases)

    def test_skyblock_has_short_alias(self):
        command = next(command for command in SkyBlock(bot=None).get_commands() if command.name == "skyblock")
        self.assertIn("sb", command.aliases)


if __name__ == "__main__":
    unittest.main()
