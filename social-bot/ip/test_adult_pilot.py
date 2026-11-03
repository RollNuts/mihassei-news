#!/usr/bin/env python3
"""Offline checks for ADULT, non-canonical manga art proof of concept."""
import hashlib
import sys
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, ImageStat

sys.path.insert(0,str(Path(__file__).resolve().parent))
from adult_pilot import draw_comic, draw_cast_sheet, neji, pito, PAPER


class AdultPilotTests(unittest.TestCase):
    def test_preview_comic_dimensions_and_not_blank(self):
        im=draw_comic()
        self.assertEqual(im.size,(1080,1410))
        self.assertEqual(im.mode,"RGB")
        self.assertGreater(max(ImageStat.Stat(im).stddev),27)

    def test_cast_sheet_keeps_three_original_character_anchors(self):
        im=draw_cast_sheet()
        self.assertEqual(im.size,(1320,875))
        # Variation: worn copper protagonist, teal terminal, violet steam form.
        copper=im.getpixel((227,501))
        teal=im.getpixel((657,490))
        violet=im.getpixel((1081,503))
        self.assertNotEqual(copper,teal)
        self.assertNotEqual(teal,violet)
        self.assertNotEqual(copper,violet)

    def test_prototype_is_not_a_production_story(self):
        p=Path(__file__).resolve().parent/"adult_pilot.py"
        src=p.read_text(encoding="utf-8")
        self.assertIn("OUT = HERE / \"previews\"",src)
        self.assertNotIn("posts.json",src)
        self.assertNotIn("BUFFER_API_KEY",src)
        self.assertNotIn("api.openai.com",src)

    def test_deterministic_pencil_line_and_story_image(self):
        first=draw_comic()
        second=draw_comic()
        self.assertEqual(hashlib.sha256(first.tobytes()).digest(),
                         hashlib.sha256(second.tobytes()).digest())

    def test_neji_preserves_non_baby_dry_expression(self):
        im=Image.new("RGB",(400,400),PAPER)
        d=ImageDraw.Draw(im)
        neji(d,200,185,1,"front","plain","rest")
        self.assertNotEqual(im.getpixel((200,160)),PAPER)
        self.assertNotEqual(im.getpixel((190,185)),PAPER)

    def test_side_shout_is_visibly_different_from_resting_mouth(self):
        # The close shot needs a readable open mouth, not the same flat mark.
        resting=Image.new("RGB",(400,400),PAPER)
        shouting=Image.new("RGB",(400,400),PAPER)
        neji(ImageDraw.Draw(resting),200,185,1,"side","plain","rest")
        neji(ImageDraw.Draw(shouting),200,185,1,"side","shout","rest")
        self.assertNotEqual(resting.getpixel((240,185)),
                            shouting.getpixel((240,185)))

    def test_pito_old_display_is_not_baby_face(self):
        im=Image.new("RGB",(430,360),PAPER)
        pito(ImageDraw.Draw(im),210,165,1.0)
        self.assertNotEqual(im.getpixel((210,165)),PAPER)


if __name__=="__main__":
    unittest.main(verbosity=2)
