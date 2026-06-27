import unittest

from services.youtube_store import YouTubeAnnouncementStore


class YouTubeAnnouncementStoreTests(unittest.TestCase):
    def test_send_attempt_statuses_block_future_claims(self):
        self.assertIn("pending", YouTubeAnnouncementStore.FINAL_STATUSES)
        self.assertIn("failed", YouTubeAnnouncementStore.FINAL_STATUSES)
        self.assertIn("send_failed", YouTubeAnnouncementStore.FINAL_STATUSES)

    def test_manual_and_sent_statuses_stay_final(self):
        self.assertIn("sent", YouTubeAnnouncementStore.FINAL_STATUSES)
        self.assertIn("manually_acknowledged", YouTubeAnnouncementStore.FINAL_STATUSES)


if __name__ == "__main__":
    unittest.main()
