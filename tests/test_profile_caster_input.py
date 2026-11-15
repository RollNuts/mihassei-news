"""Source, age and speech boundaries; no media runner, download or full build."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import profile_caster_input as source

ROOT = Path(__file__).resolve().parents[1]


class ProfileCasterInputTests(unittest.TestCase):
    def test_current_source_uses_real_published_gates_and_keeps_profile_provenance(self):
        result = source.resolve_profile_input("shioba-rio", root=ROOT)
        record = result["portrait_record"]
        self.assertIsNone(result["spoken_line"])
        self.assertEqual(record["source_kind"], "photo_profile")
        self.assertEqual(record["intended_age"], 29)
        self.assertEqual(record["story_id"], source.STORY_ID)
        self.assertEqual(record["cut_id"], "01")
        self.assertEqual(record["dimensions"], [1200, 800])
        self.assertEqual(hashlib.sha256(result["portrait"].read_bytes()).hexdigest(), record["image_sha256"])
        self.assertRegex(record["source_png_sha256"], r"^[0-9a-f]{64}$")
        for field in ("article_slug", "article_sha256", "generation_id", "generation_receipt"):
            self.assertNotIn(field, record)
            self.assertNotIn(field, result["context"])

    def test_short_subjectless_japanese_is_valid_but_unsafe_or_long_speech_is_not(self):
        self.assertEqual(source._spoken_line("今日は少し、ひと休み。"), "今日は少し、ひと休み。")
        for text in ("はい", "あ" * 29, "not Japanese", "今日は\n休憩。", "今日は\u202e休憩。",
                     "<b>今日は休憩</b>", "今日はhttps://example.com", "[今日は休憩](x)", "**今日は休憩**"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                source.resolve_profile_input("shioba-rio", text, root=ROOT)

    def test_other_profiles_and_changed_age_are_rejected(self):
        for profile in ("hanase-mio", "tsukishiro-rena", "unknown"):
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                source.resolve_profile_input(profile, root=ROOT)
        catalog = json.loads((ROOT / "content/photo_issues.json").read_text())
        for age in (20, 24, 36, True):
            changed = copy.deepcopy(catalog)
            next(model for model in changed["models"] if model["id"] == source.PROFILE_ID)["age"] = age
            with self.subTest(age=age), patch.object(source, "load_photo_issues", return_value=changed):
                with self.assertRaises(ValueError):
                    source.resolve_profile_input("shioba-rio", root=ROOT)

    def test_root_symlinks_are_rejected_before_catalog_loading(self):
        with tempfile.TemporaryDirectory() as folder:
            link = Path(folder) / "checkout"
            link.symlink_to(ROOT, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "root"):
                source.resolve_profile_input("shioba-rio", root=link)

    def test_existing_source_gate_rejects_wrong_model_review_and_changed_bytes(self):
        current = json.loads((ROOT / "content/photo_issues.json").read_text())
        for mutation in ("wrong_model", "rejected_review"):
            catalog = copy.deepcopy(current)
            issue = next(issue for issue in catalog["issues"] if issue["model_id"] == source.PROFILE_ID)
            if mutation == "wrong_model":
                issue["model_id"] = "tsukishiro-rena"
            else:
                issue["images"]["01"]["review"]["decision"] = "rejected"
            with self.subTest(mutation=mutation), patch.object(source, "load_photo_issues", return_value=catalog):
                with self.assertRaises(ValueError):
                    source.resolve_profile_input("shioba-rio", root=ROOT)
        issue = next(issue for issue in current["issues"] if issue["model_id"] == source.PROFILE_ID)
        portrait = ROOT / issue["images"]["01"]["path"]
        read_bytes = Path.read_bytes
        with patch.object(Path, "read_bytes", autospec=True,
                          side_effect=lambda path: b"changed" if path == portrait else read_bytes(path)):
            with self.assertRaises(ValueError):
                source.resolve_profile_input("shioba-rio", root=ROOT)

    def test_source_path_cannot_traverse_or_follow_a_symlink(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            (root / "outside-link.jpg").symlink_to(ROOT / "content/photo_issues.json")
            for path in ("../outside.jpg", str(ROOT / "content/photo_issues.json"), "outside-link.jpg"):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    source._local(root, path)


if __name__ == "__main__":
    unittest.main()
