"""A timeout or storage stop must not leave more jobs queued behind a stuck GPU."""
import contextlib
import io
import unittest
from unittest.mock import patch

from scripts import local_scene_photos as photos


class SceneBatchStopTests(unittest.TestCase):
    def run_batch(self, failure):
        articles = [dict(slug="story-" + str(n), title="A staged scene") for n in range(3)]
        with patch.object(photos, "load_articles", return_value=articles), \
             patch.object(photos, "pending_articles", return_value=articles), \
             patch.object(photos, "check_storage"), \
             patch.object(photos, "archive_bytes", return_value=0), \
             patch.object(photos, "create_png", side_effect=failure) as create, \
             contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            result = photos.main(["--acknowledge-model-license", "--limit", "3"])
        self.assertEqual(result, 1)
        return create.call_count

    def test_timeout_stops_enqueueing_additional_images(self):
        self.assertEqual(self.run_batch(TimeoutError("GPU still running")), 1)

    def test_storage_stop_stops_the_whole_batch(self):
        self.assertEqual(self.run_batch(photos.StorageLimitError("disk full")), 1)

    def test_bad_individual_image_does_not_starve_other_articles(self):
        self.assertEqual(self.run_batch(ValueError("invalid PNG")), 3)


if __name__ == "__main__":
    unittest.main()
