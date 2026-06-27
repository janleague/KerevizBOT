import unittest

from commands.giveaway import (
    PARTICIPANT_PREVIEW_LIMIT,
    format_participants_preview,
    normalize_entrant_ids,
    participant_pages,
)


class GiveawayParticipantsTests(unittest.TestCase):
    def test_normalizes_participants_without_duplicates(self):
        self.assertEqual(normalize_entrant_ids(["12", 12, "34", None, "bad"]), [12, 34])

    def test_formats_empty_participants(self):
        text, total, truncated = format_participants_preview([])

        self.assertEqual(text, "No participants yet.")
        self.assertEqual(total, 0)
        self.assertFalse(truncated)

    def test_formats_participant_preview_with_limit(self):
        entrants = list(range(1, PARTICIPANT_PREVIEW_LIMIT + 3))
        text, total, truncated = format_participants_preview(entrants)

        self.assertEqual(total, PARTICIPANT_PREVIEW_LIMIT + 2)
        self.assertTrue(truncated)
        self.assertIn(f"`{PARTICIPANT_PREVIEW_LIMIT}.`", text)
        self.assertIn("...and `2` more participant(s).", text)

    def test_participant_pages_include_all_entries(self):
        entrants = list(range(1, PARTICIPANT_PREVIEW_LIMIT + 3))
        pages, total = participant_pages(entrants)

        self.assertEqual(total, PARTICIPANT_PREVIEW_LIMIT + 2)
        self.assertEqual(len(pages), 2)
        self.assertIn(f"`{PARTICIPANT_PREVIEW_LIMIT + 2}.`", pages[1])


if __name__ == "__main__":
    unittest.main()
