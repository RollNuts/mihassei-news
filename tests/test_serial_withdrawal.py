"""Withdraw only public mystery output; retain source and strict history protection."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import build, serials, check_share_cards
from scripts.guard_serial_history import guard_serial_history

ROOT = Path(__file__).resolve().parents[1]


class SerialWithdrawalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source_bytes = (ROOT / "content/serials.json").read_bytes()
        cls.source = json.loads(cls.source_bytes)
        cls.withdrawn = next(item for item in cls.source if item["slug"] == "case-files")
        cls.market = next(item for item in cls.source if item["slug"] == "market-lab")

    def test_default_loader_excludes_only_withdrawn_series_and_source_is_intact(self):
        self.assertEqual([item["slug"] for item in serials.load_serials(ROOT)], ["market-lab"])
        self.assertEqual(serials.load_serials(ROOT, include_withdrawn=True), self.source)
        self.assertEqual(guard_serial_history(self.source, copy.deepcopy(self.source)),
                         sum(len(item["episodes"]) for item in self.source))
        changed = copy.deepcopy(self.source)
        next(item for item in changed if item["slug"] == "case-files")["episodes"][0]["sections"][0]["paragraphs"][0] += "改変"
        with self.assertRaisesRegex(ValueError, "rewritten"):
            guard_serial_history(self.source, changed)

    def test_temporary_public_output_contains_market_without_mystery_art_or_feed(self):
        source = copy.deepcopy(self.source)
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            def write(relative, text):
                path = output / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
            def preview(series, path, episode=None):
                self.assertNotIn(series["slug"], serials.WITHDRAWN_SERIES)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture-preview")
            with patch.object(serials, "render_social_preview", side_effect=preview):
                serials.build_serials(source, build.layout, write, output)
            self.assertTrue((output / "serials/market-lab/index.html").exists())
            self.assertTrue((output / "feeds/series-market-lab.xml").exists())
            self.assertTrue((output / "og/serials/market-lab.png").exists())
            self.assertFalse((output / "serials/case-files").exists())
            self.assertFalse((output / "feeds/series-case-files.xml").exists())
            self.assertFalse(list(output.rglob("*case-files*")))
            markup = "\n".join(path.read_text() for path in output.rglob("*.html"))
            self.assertNotIn(self.withdrawn["title"], markup)
            self.assertNotIn("case-files", markup)
            self.assertNotIn(self.withdrawn["episodes"][0]["sections"][0]["paragraphs"][0], markup)
            self.assertNotIn("連載終了", markup)
            self.assertIn(self.market["title"], markup)
            self.assertIn('"@type": "Article"', markup)
        self.assertEqual(source, self.source)
        self.assertEqual((ROOT / "content/serials.json").read_bytes(), self.source_bytes)

    def test_routes_sitemap_jsonld_and_monitoring_exclude_withdrawn_source_rows(self):
        self.assertFalse(any("case-files" in url for url, _day in serials.serial_routes(self.source)))
        files = {}
        with patch.object(build, "write", side_effect=files.__setitem__):
            build.other_files([], issues={}, serials=self.source)
        self.assertNotIn("case-files", files["sitemap.xml"])
        self.assertNotIn(self.withdrawn["title"], files["sitemap.xml"])
        self.assertIn("/serials/market-lab/", files["sitemap.xml"])
        hub = serials.render_serials_index(self.source, build.layout)
        self.assertNotIn("case-files", hub)
        self.assertNotIn(self.withdrawn["title"], hub)
        self.assertIn("市場分析の連載", hub)
        self.assertNotIn("架空の推理小説", hub)
        self.assertFalse(any("case-files" in path for path in check_share_cards.candidate_paths(ROOT)))
        for markup in (build.about(), build.editorial_policy()):
            self.assertNotIn(self.withdrawn["title"], markup)
            self.assertNotIn("推理小説", markup)
            self.assertIn(self.market["title"], markup)

    def test_direct_public_renderers_reject_the_withdrawn_series(self):
        series, episode = self.withdrawn, self.withdrawn["episodes"][0]
        calls = (
            lambda: serials.render_series_page(series, build.layout),
            lambda: serials.render_episode_page(series, episode, build.layout),
            lambda: serials.serial_feed_xml(series),
            lambda: serials.render_cover_svg(series),
            lambda: serials.render_social_preview(series, Path("unused.png")),
            lambda: build.layout("Private", "Private", serials.series_url(series), "body", serial=series),
        )
        for call in calls:
            with self.subTest(call=call), self.assertRaisesRegex(ValueError, "withdrawn"):
                call()
        self.assertEqual(serials.render_serials_feature([series]).count("case-files"), 0)


if __name__ == "__main__":
    unittest.main()
