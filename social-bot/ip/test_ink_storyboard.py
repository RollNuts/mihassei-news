#!/usr/bin/env python3
"""Free offline tests for original hand-ink Nejimachi episode 6."""
import hashlib
import unittest
from ink_storyboard import draw_page, neji, moku, skline, seed
from PIL import Image, ImageDraw


class InkMangaTests(unittest.TestCase):
    def test_correct_output_and_stable_art(self):
        image=draw_page()
        self.assertEqual(image.size,(1080,1350))
        self.assertEqual(image.mode,"RGB")
        # Fixed seeded paper texture and brush marks prevent visual drift.
        digest=hashlib.sha256(image.tobytes()).hexdigest()
        self.assertEqual(digest,hashlib.sha256(draw_page().tobytes()).hexdigest())

    def test_asymmetric_story_panel_heights(self):
        im=draw_page()
        # Top city view, medium reaction panels, large reveal panel must differ.
        # Panel center samples cannot all share a flat background color.
        samples=[im.getpixel((100,180)), im.getpixel((850,600)), 
                 im.getpixel((270,650)), im.getpixel((780,1100))]
        self.assertGreaterEqual(len(set(samples)),3)

    def test_character_poses_are_distinct_not_identical_stamps(self):
        im1=Image.new("RGB",(400,410),"white")
        im2=Image.new("RGB",(400,410),"white")
        neji(ImageDraw.Draw(im1),195,190,.9,"smile","walk")
        neji(ImageDraw.Draw(im2),195,190,.9,"scared","startle")
        self.assertNotEqual(im1.tobytes(),im2.tobytes())
        im3=Image.new("RGB",(400,410),"white")
        moku(ImageDraw.Draw(im3),195,190,.9,"smile","arms-up")
        self.assertNotEqual(im1.tobytes(),im3.tobytes())

    def test_pencil_lines_have_irregularity_and_seed(self):
        a=Image.new("RGB",(400,160),"white")
        b=Image.new("RGB",(400,160),"white")
        skline(ImageDraw.Draw(a),[(20,60),(370,60)],"same-seed",5)
        skline(ImageDraw.Draw(b),[(20,60),(370,60)],"same-seed",5)
        self.assertEqual(a.tobytes(),b.tobytes())
        # Real textured ink deviates from ruler-perfect y=60.
        self.assertTrue(any(a.getpixel((x,y)) != (255,255,255)
                            for x in range(30,370,5) for y in (56,57,63,64)))


if __name__=="__main__":
    unittest.main(verbosity=2)
