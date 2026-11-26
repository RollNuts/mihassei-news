"""Article input errors must stop before the shared site renderer runs."""
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import build


class ArticleFieldTests(unittest.TestCase):
    def test_fictional_dateline_accepts_exact_milliseconds_and_preserves_local_time(self):
        before = "2026-11-23T19:30:48"
        after = "2031-11-23T19:30:48"
        for source, display in [(".418", ".418"), (".418", ".418000"), (".100000", ".100"),
                                ("", ".000"), (".000", ""), (".000001", ".000001")]:
            with self.subTest(source=source, display=display):
                self.assertTrue(build.matches_fictional_dateline(before + source + "+09:00",
                                                                 after + display + "+09:00"))
        for wrong in [after + ".419+09:00", after + ".4180001+09:00", after + ".418+08:00",
                      "2031-11-23T10:30:48.418+00:00", "2030-11-23T19:30:48.418+09:00"]:
            with self.subTest(wrong=wrong):
                self.assertFalse(build.matches_fictional_dateline(before + ".418+09:00", wrong))
        self.assertFalse(build.matches_fictional_dateline(before + ".418001+09:00", after + ".418+09:00"))

    def test_fictional_utc_dateline_accepts_z_without_changing_the_offset_or_instant(self):
        source = "2026-11-25T11:03:29.053Z"
        future = "2031-11-25T11:03:29.053"
        for display in [future + "Z", future + "+00:00"]:
            self.assertTrue(build.matches_fictional_dateline(source, display))
        self.assertTrue(build.matches_fictional_dateline(source[:-1] + "+00:00", future + "Z"))
        for wrong in ["2031-11-25T20:03:29.053+09:00", "2031-11-25T11:03:30.053Z",
                      "2031-11-25T11:03:29.054Z", "2030-11-25T11:03:29.053Z"]:
            with self.subTest(wrong=wrong):
                self.assertFalse(build.matches_fictional_dateline(source, wrong))
        with self.assertRaises(ValueError):
            build.matches_fictional_dateline("2028-02-29T09:00:00Z", "2033-03-01T09:00:00Z")

    def test_missing_fiction_company_is_reported_by_load_before_rendering(self):
        articles = json.loads((build.BASE / "content/articles.json").read_text(encoding="utf-8"))
        slug = articles[0]["slug"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "content/articles.json"
            path.parent.mkdir()
            path.write_text(json.dumps(articles, ensure_ascii=False), encoding="utf-8")
            with patch.object(build, "BASE", root):
                self.assertEqual(len(build.load()), len(articles))
                del articles[0]["fiction_company"]
                path.write_text(json.dumps(articles, ensure_ascii=False), encoding="utf-8")
                with self.assertRaisesRegex(
                    ValueError, r"Missing article field: fiction_company.*" + re.escape(slug)
                ):
                    build.load()


if __name__ == "__main__":
    unittest.main()
