#!/usr/bin/env python3
"""Tests for practical readability checks on future short mangas."""
import copy
import unittest

from readability import verify


def example():
    return {
        "series":"nejimachi","episode":4,"kind":"comic",
        "text":"【ネジまち】二人で修理を終えてハイタッチした。",
        "clarity":{
            "setup":"ネジが小型機械の修理を終えて喜んでいる。",
            "problem":"大きな手袋がネジの頭をすっぽり包んでしまう。",
            "payoff":"ネジが顔を出して次は指一本でとお願いする。"
        },
        "panels":[
            {"line":"「直った！」","scene":"workshop","sign":"共同整備台","focus":"neji","shot":"wide"},
            {"line":"「ハイタッチ！」","scene":"workshop","focus":"moku","shot":"medium"},
            {"line":"「つぎは指一本でね」","scene":"workshop","focus":"neji","shot":"close"}
        ]
    }


class TestReadability(unittest.TestCase):
    def test_valid_three_panel_setup(self):
        self.assertEqual(verify([example()]),1)

    def test_no_setup(self):
        p=example()
        p["clarity"].pop("setup")
        with self.assertRaisesRegex(ValueError,"clarity.setup"):
            verify([p])

    def test_wall_of_text(self):
        p=example()
        p["panels"][1]["line"]="説明がとても長く、理解するまでに時間がかかるようなセリフをなんとなく言い続けてしまう。読者にはつらい。"
        with self.assertRaisesRegex(ValueError,"too much text|long phone-screen"):
            verify([p])

    def test_repeated_camera(self):
        p=example()
        for frame in p["panels"]:
            frame["shot"]="medium"
        with self.assertRaisesRegex(ValueError,"change at least one shot"):
            verify([p])

    def test_legacy_preserved(self):
        p=example()
        p["episode"]=2
        p.pop("clarity")
        for frame in p["panels"]:
            frame.pop("shot")
        self.assertEqual(verify([p]),1)

    def test_visual_effect_must_exist_in_renderer(self):
        p=example()
        p["panels"][-1]["effect"]="unimplemented"
        with self.assertRaisesRegex(ValueError,"unknown visual effect"):
            verify([p])


if __name__=="__main__":
    unittest.main(verbosity=2)
