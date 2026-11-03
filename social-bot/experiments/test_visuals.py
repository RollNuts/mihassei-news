#!/usr/bin/env python3
"""Offline tests: verify original PNGs, immutable outputs and no manga imports."""
import tempfile
import unittest
from pathlib import Path
from PIL import Image

from visuals import ROOT, SCENES, render, draw_scene, W, H


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
        expected={"xexp-sock-career", "xexp-book-review", "xexp-cloud-bus"}
        self.assertTrue(expected.issubset(SCENES))
        # Layouts must be recognizably different in visual structure.
        samples={name:draw_scene(name).getpixel((620, 450)) for name in expected}
        self.assertEqual(len(set(samples.values())),len(expected))

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
