import unittest

from services.youtube_utils import extract_youtube_subscriber_count, normalize_youtube_video_id


class NormalizeYoutubeVideoIdTests(unittest.TestCase):
    def test_accepts_raw_video_id(self):
        self.assertEqual(normalize_youtube_video_id("dQw4w9WgXcQ"), "dQw4w9WgXcQ")

    def test_accepts_watch_url(self):
        self.assertEqual(
            normalize_youtube_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ&feature=share"),
            "dQw4w9WgXcQ",
        )

    def test_accepts_short_share_url(self):
        self.assertEqual(
            normalize_youtube_video_id("https://youtu.be/dQw4w9WgXcQ?si=abc"),
            "dQw4w9WgXcQ",
        )

    def test_accepts_shorts_live_and_embed_urls(self):
        urls = [
            "https://www.youtube.com/shorts/dQw4w9WgXcQ?feature=share",
            "https://www.youtube.com/live/dQw4w9WgXcQ?si=abc",
            "https://www.youtube.com/embed/dQw4w9WgXcQ",
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(normalize_youtube_video_id(url), "dQw4w9WgXcQ")

    def test_rejects_invalid_values(self):
        for value in (None, "", "not a video", "https://youtube.com/@kerevizYT"):
            with self.subTest(value=value):
                self.assertIsNone(normalize_youtube_video_id(value))


class ExtractYoutubeSubscriberCountTests(unittest.TestCase):
    def test_extracts_current_channel_page_shape(self):
        page = '"subscriberCountText":"992 subscribers","viewCountText":"143,456 views"'
        self.assertEqual(extract_youtube_subscriber_count(page), "992")

    def test_extracts_metadata_content_shape(self):
        page = (
            '"metadataParts":[{"text":{"content":"1.24K subscribers"},'
            '"accessibilityLabel":"1.24K subscribers"}}]'
        )
        self.assertEqual(extract_youtube_subscriber_count(page), "1.24K")

    def test_returns_none_when_subscriber_count_is_missing(self):
        self.assertIsNone(extract_youtube_subscriber_count('{"title":"Kereviz"}'))


if __name__ == "__main__":
    unittest.main()
