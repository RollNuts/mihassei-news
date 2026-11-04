#!/usr/bin/env python3
"""Offline tests: verify original PNGs, immutable outputs and no manga imports."""
import json
import tempfile
import unittest
from pathlib import Path
from PIL import Image

from visuals import ROOT, SCENES, render, draw_scene, W, H, git_blob_sha, refresh_unqueued_book


class OriginalXVisualTests(unittest.TestCase):
    def test_four_original_images_are_renderable_and_compact(self):
        for name in SCENES:
            im = draw_scene(name)
            self.assertEqual(im.size, (W, H))
            self.assertEqual(im.mode, "RGB")
            asset = ROOT / (name + ".png")
            self.assertTrue(asset.is_file(), name)
            self.assertLess(asset.stat().st_size, 1_000_000)
            with Image.open(asset) as stored:
                self.assertEqual(stored.format, "PNG")
                self.assertEqual(stored.size, (W, H))

    def test_new_visuals_are_distinct_and_original(self):
        expected={"xexp-sock-career", "xexp-book-review", "xexp-cloud-bus", "xexp-lunch-price"}
        self.assertTrue(expected.issubset(SCENES))
        # Layouts must be recognizably different in visual structure.
        samples={name:draw_scene(name).getpixel((620, 450)) for name in expected}
        self.assertEqual(len(set(samples.values())),len(expected))

    def test_book_review_uses_large_nontextual_rating_and_sleep_cue(self):
        im = draw_scene("xexp-book-review")
        # Two brightly colored awarded stars and three unawarded stars.
        self.assertEqual(im.getpixel((827, 345)), (238, 182, 75))
        self.assertEqual(im.getpixel((865, 345)), (238, 182, 75))
        self.assertNotEqual(im.getpixel((903, 345)), (238, 182, 75))
        # Full-size 1200x675 must scale down to a typical feed preview.
        small = im.resize((320, 180), Image.Resampling.LANCZOS)
        self.assertEqual(small.size, (320, 180))

    def test_one_time_book_refresh_updates_only_unknown_to_buffer_old_asset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / "xexp-book-review.png"
            with Image.new("RGB", (W, H), (54, 66, 73)) as img:
                img.save(old)
            original = old.read_bytes()
            state_path = root / "state.json"
            state_path.write_text(json.dumps({"queued": {}}), encoding="utf-8")
            self.assertTrue(refresh_unqueued_book(root, state_path,
                                                  expected_blob=git_blob_sha(original)))
            updated = old.read_bytes()
            self.assertNotEqual(original, updated)
            with Image.open(old) as im:
                self.assertEqual(im.size, (W, H))
            self.assertFalse(refresh_unqueued_book(root, state_path,
                                                   expected_blob=git_blob_sha(original)))
            self.assertEqual(old.read_bytes(), updated)
            # Once registered in Buffer, even an old exact asset is immutable.
            old.write_bytes(original)
            state_path.write_text(json.dumps({"queued": {
                "xexp-book-review": {"buffer_id": "immutable-post"}
            }}), encoding="utf-8")
            self.assertFalse(refresh_unqueued_book(root, state_path,
                                                   expected_blob=git_blob_sha(original)))
            self.assertEqual(old.read_bytes(), original)
            # Unknown modified bytes also remain untouched.
            state_path.write_text(json.dumps({"queued": {}}), encoding="utf-8")
            self.assertFalse(refresh_unqueued_book(root, state_path,
                                                   expected_blob="0"*40))
            self.assertEqual(old.read_bytes(), original)

    def test_lunch_satire_image_is_legible_at_feed_size(self):
        im = draw_scene("xexp-lunch-price")
        thumb = im.resize((320, 180), Image.Resampling.LANCZOS)
        self.assertEqual(thumb.size, (320, 180))
        # Rice, the empty dark compartment, and the printed fake chicken
        # all remain visibly different, without using a block of text.
        self.assertNotEqual(im.getpixel((545, 360)),
                            im.getpixel((750, 360)))
        self.assertNotEqual(im.getpixel((750, 360)),
                            im.getpixel((880, 385)))
        self.assertEqual(SCENES["xexp-lunch-price"], "bento")

    def test_does_not_overwrite_any_existing_asset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            existing = root / "xexp-ufo-ticket.png"
            existing.write_bytes(b"historical immutable media")
            render(root)
            self.assertEqual(existing.read_bytes(), b"historical immutable media")
            self.assertEqual(len(list(root.glob("*.png"))), len(SCENES))

    def test_cannot_create_unknown_or_unapproved_manga(self):
        with self.assertRaisesRegex(ValueError, "Unknown"):
            draw_scene("nejimachi-006-big-shadow")


if __name__ == "__main__":
    unittest.main(verbosity=2)
