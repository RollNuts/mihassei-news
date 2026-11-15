"""Public cast biography boundaries without a site build or image processing."""
import copy
import json
from pathlib import Path
import unittest

from scripts.photo_biographies import public_biography, validate_biography


SAMPLE = {
    "hometown": "神奈川県藤沢市",
    "career": [{"month": "2028-04", "text": "衣服の撮影モデルとして仕事を始める。"},
               {"month": "2030-06", "text": "架空の体験番組「さやの寄り道手帖」でロケを担当。"}],
    "strength": "手触りを身近な言葉で伝える。",
    "difficulty": "帰り道の寄り道が増える。",
    "habit": "気に入った風景を一枚だけ撮る。",
}


class PhotoBiographyTests(unittest.TestCase):
    def test_legacy_snapshot_is_optional_and_projection_copies_only_biography(self):
        self.assertIsNone(public_biography({"id": "legacy", "master": {"path": "private"}}))
        model = {"id": "sample", "biography": copy.deepcopy(SAMPLE),
                 "appearance": "internal", "master": {"path": "private"}}
        view = public_biography(model)
        self.assertEqual(view, SAMPLE)
        view["career"][0]["text"] = "changed"
        self.assertEqual(model["biography"]["career"][0]["text"], SAMPLE["career"][0]["text"])

    def test_unknown_fields_and_active_or_hidden_text_are_rejected(self):
        for field, value in (("internal_note", "private"), ("strength", "<script>"),
                             ("habit", "http://example.com"), ("habit", "one\ntwo"),
                             ("habit", "word\u202etext")):
            with self.subTest(field=field, value=value):
                invalid = copy.deepcopy(SAMPLE)
                invalid[field] = value
                with self.assertRaises(ValueError):
                    validate_biography(invalid)
        invalid = copy.deepcopy(SAMPLE)
        invalid["career"][0]["source"] = "private"
        with self.assertRaises(ValueError):
            validate_biography(invalid)

    def test_career_months_order_limit_and_fictional_cutoff(self):
        for months in (("2031-13", "2032-01"), ("2030-06", "2028-04"),
                       ("2030-06", "2030-06"), ("2030-06", "2031-12"),
                       ("0000-01", "2030-06"), ("2030-06",)):
            with self.subTest(months=months):
                invalid = copy.deepcopy(SAMPLE)
                invalid["career"] = [{"month": month, "text": "撮影を担当。"} for month in months]
                with self.assertRaises(ValueError):
                    validate_biography(invalid)
        invalid = copy.deepcopy(SAMPLE)
        invalid["career"] *= 2
        with self.assertRaises(ValueError):
            validate_biography(invalid)

    def test_live_catalog_biographies_are_valid(self):
        catalog = json.loads((Path(__file__).resolve().parents[1]
                              / "content/photo_issues.json").read_text(encoding="utf-8"))
        for model in catalog["models"]:
            with self.subTest(model=model["id"]):
                self.assertIsNotNone(public_biography(model))


if __name__ == "__main__":
    unittest.main()
