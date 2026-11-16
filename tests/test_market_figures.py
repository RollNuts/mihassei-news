"""Validate factual market figures separately from immutable prediction history."""
import copy
from datetime import date
import json
from pathlib import Path
import tempfile
import unittest
from xml.etree import ElementTree

from scripts.market_figures import (
    load_market_figures, figure_description, render_market_figure,
    render_market_svg, write_market_figures,
)
from scripts.serials import render_episode_page

ROOT = Path(__file__).resolve().parents[1]


def passthrough_layout(_title, _description, _url, markup, **_kwargs):
    return markup


class MarketFigureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.series = next(
            s for s in json.loads((ROOT / "content/serials.json").read_text(encoding="utf-8"))
            if s["slug"] == "market-lab"
        )
        cls.figures = load_market_figures(ROOT, [cls.series], today=date(2026, 11, 15))

    def test_six_provenance_checked_figures_for_two_existing_articles(self):
        self.assertEqual(set(self.figures), {
            "gs-yuasa-margin-test-02", "swcc-second-half-profit-03"})
        self.assertEqual([len(x) for x in self.figures.values()], [3, 3])
        self.assertNotIn("power-grid-after-ai-01", self.figures)
        for episode in self.series["episodes"]:
            for f in self.figures.get(episode["slug"], []):
                self.assertIn(f["source_url"], [s["url"] for s in episode["sources"]])
                self.assertLessEqual(f["source_published_on"], episode["published_at"])
                self.assertLessEqual(f["checked_on"], "2026-11-15")

    def test_svg_is_offline_readable_and_dual_aspect(self):
        for figures in self.figures.values():
            for item in figures:
                for mobile, width in ((False, "940"), (True, "440")):
                    source = render_market_svg(item, mobile=mobile)
                    root = ElementTree.fromstring(source)
                    self.assertEqual(root.attrib["width"], width)
                    self.assertEqual(root.attrib["role"], "img")
                    self.assertIsNotNone(root.find("{http://www.w3.org/2000/svg}title"))
                    self.assertIsNotNone(root.find("{http://www.w3.org/2000/svg}desc"))
                    for forbidden in ("<script", "xlink:href", "<foreignObject", "<image"):
                        self.assertNotIn(forbidden, source)
                self.assertIn(item["unit"], figure_description(item))
        self.assertIn("109億円", figure_description(
            self.figures["swcc-second-half-profit-03"][1]))
        self.assertIn("会社予想", figure_description(
            self.figures["gs-yuasa-margin-test-02"][0]))

    def test_render_does_not_touch_original_prediction(self):
        original = copy.deepcopy(self.series)
        for episode in self.series["episodes"]:
            displayed = render_episode_page(
                self.series, episode, passthrough_layout,
                self.figures.get(episode["slug"], []))
            self.assertIn("市場の見通しは仮説", displayed)
            self.assertIn(episode["forecast"]["statement"], displayed)
            self.assertNotIn(episode["forecast"]["method"], displayed)
            self.assertIn(episode["forecast"]["reviewed_on"], displayed)
            count = len(self.figures.get(episode["slug"], []))
            self.assertEqual(displayed.count('<figure class="market-figure">'), count)
            if count:
                self.assertEqual(displayed.count('<source media="(max-width:600px)"'), count)
                self.assertIn("資料公表：", displayed)
                self.assertIn("図解確認：", displayed)
        self.assertEqual(original, self.series)

    def test_writes_all_svg_assets_without_paid_service(self):
        output = {}
        for episode in self.series["episodes"]:
            write_market_figures(self.series, episode,
                                 self.figures.get(episode["slug"], []),
                                 lambda path, src: output.__setitem__(path, src))
        self.assertEqual(len(output), 12)
        self.assertEqual(len([p for p in output if p.endswith("-mobile.svg")]), 6)
        self.assertTrue(all("xmlns=" in s for s in output.values()))

    def test_rejects_uncited_figures_before_site_build(self):
        raw = json.loads((ROOT / "content/market_figures.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as folder:
            content = Path(folder) / "content"
            content.mkdir()
            temp_file = content / "market_figures.json"
            bad = copy.deepcopy(raw)
            bad[0]["source_url"] = "https://example.invalid/something"
            temp_file.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "cite"):
                load_market_figures(Path(folder), [self.series], today=date(2026, 11, 15))
            bad[0]["source_url"] = raw[0]["source_url"]
            bad[0]["values"][0]["value"] = -1
            temp_file.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "nonnegative"):
                load_market_figures(Path(folder), [self.series], today=date(2026, 11, 15))


if __name__ == "__main__":
    unittest.main()
