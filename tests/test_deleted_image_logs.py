import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import commands.deleted_image_logs as deleted_image_logs


class DeletedImageCacheCleanupTests(unittest.TestCase):
    def test_cache_cutoff_uses_retention_days(self):
        now = datetime(2026, 7, 1, tzinfo=timezone.utc)
        cutoff = deleted_image_logs.deleted_image_cache_cutoff(30, current_time=now)
        self.assertEqual(cutoff, datetime(2026, 6, 1, tzinfo=timezone.utc))

    def test_deletes_only_old_local_cache_files(self):
        original_cache_dir = deleted_image_logs.CACHE_DIR
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            deleted_image_logs.CACHE_DIR = cache_dir
            old_file = cache_dir / "guild" / "old.png"
            new_file = cache_dir / "guild" / "new.png"
            old_file.parent.mkdir(parents=True)
            old_file.write_text("old", encoding="utf-8")
            new_file.write_text("new", encoding="utf-8")
            os.utime(old_file, (1000, 1000))
            os.utime(new_file, (3000, 3000))

            cutoff = datetime.fromtimestamp(2000, timezone.utc)
            deleted = deleted_image_logs.DeletedImageLogs._delete_old_local_cache_files(cutoff)

            self.assertEqual(deleted, 1)
            self.assertFalse(old_file.exists())
            self.assertTrue(new_file.exists())
        deleted_image_logs.CACHE_DIR = original_cache_dir


if __name__ == "__main__":
    unittest.main()
