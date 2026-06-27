import unittest

from commands.clear import build_audit_reason, format_clear_result


class FakeModerator:
    id = 123456789

    def __str__(self):
        return "ModUser#0001"


class ClearCommandTests(unittest.TestCase):
    def test_formats_exact_clear_result(self):
        self.assertEqual(format_clear_result(25, 25), "Deleted `25` message(s).")

    def test_formats_partial_clear_result(self):
        self.assertEqual(
            format_clear_result(12, 25),
            "Deleted `12` message(s). Requested `25`.",
        )

    def test_audit_reason_is_trimmed_for_discord(self):
        reason = build_audit_reason("Channel nuke", FakeModerator(), "x" * 1000)

        self.assertLessEqual(len(reason), 512)
        self.assertTrue(reason.startswith("Channel nuke by ModUser#0001 (123456789): "))


if __name__ == "__main__":
    unittest.main()
