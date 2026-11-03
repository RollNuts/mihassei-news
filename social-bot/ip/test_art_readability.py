#!/usr/bin/env python3
"""No paid API: smoke tests for readable acts, shot variation and silent expressions."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageChops, ImageDraw
from render_ip import draw_manga, paint_prop

class MangaVisualTests(unittest.TestCase):
    def post(self,panels):
        return {"id":"dummy-only","series":"nejimachi","episode":98,
                "kind":"comic","title":"表示テスト","panels":panels}

    def test_silent_reaction_and_props(self):
        panels=[
            {"scene":"station","focus":"neji","mood":"happy","line":"「直った！」","shot":"wide","prop":"bolt"},
            {"scene":"station","focus":"neji","mood":"confused","line":"","silent":True,"shot":"close","prop":"ticket"},
            {"scene":"workshop","focus":"moku","mood":"sad","line":"「あれ？」","shot":"medium","prop":"cup"},
        ]
        result=draw_manga(self.post(panels))
        self.assertEqual(result.size,(1080,1350))
        self.assertEqual(result.mode,"RGB")
        # Panel 1 has a cream dialogue strip; silent panel 2 has no strip.
        cream=(255,248,235)
        self.assertEqual(result.getpixel((60,505)),cream)
        self.assertNotEqual(result.getpixel((60,864)),cream)

    def test_bad_shot_fails(self):
        panels=[{"scene":"lamp","focus":"pito","mood":"plain","line":"「だめです」","shot":"overpaint"},
                {"scene":"lamp","focus":"neji","mood":"happy","line":"「そうか」"}]
        with self.assertRaisesRegex(ValueError,"Unsupported visual shot"):
            draw_manga(self.post(panels))

    def test_silent_with_dialogue_fails(self):
        panels=[{"scene":"lamp","focus":"neji","mood":"happy","line":"「え？」","silent":True},
                {"scene":"lamp","focus":"pito","mood":"plain","line":"「……」"}]
        with self.assertRaisesRegex(ValueError,"Silent panel must not contain"):
            draw_manga(self.post(panels))

    def test_steam_gag_has_different_visible_states(self):
        pictures={}
        for state in ("none","steam-one","steam-fade","steam-two"):
            image=Image.new("RGB",(360,320),"white")
            draw=ImageDraw.Draw(image)
            paint_prop(draw,"cup",172,210,1.0,effect=state)
            pictures[state]=image
        for state in ("steam-one","steam-fade","steam-two"):
            with self.subTest(effect=state):
                self.assertIsNotNone(ImageChops.difference(pictures["none"],pictures[state]).getbbox())
        self.assertIsNotNone(ImageChops.difference(pictures["steam-one"],pictures["steam-two"]).getbbox())

    def test_steam_cannot_float_from_a_bolt(self):
        image=Image.new("RGB",(360,320),"white")
        with self.assertRaisesRegex(ValueError,"require a cup"):
            paint_prop(ImageDraw.Draw(image),"bolt",172,210,1.0,effect="steam-two")

    def test_two_to_six_panels(self):
        for length in (2,4,5,6):
            panels=[{"scene":"workshop","focus":"moku","mood":"happy",
                     "line":"「ネジ、見て」","shot":"medium"} for _ in range(length)]
            with self.subTest(length=length):
                self.assertEqual(draw_manga(self.post(panels)).size,(1080,1350))

if __name__=="__main__":
    unittest.main()
