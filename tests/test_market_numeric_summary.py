"""Source-precision market numbers and reader-first table placement."""
import copy
from datetime import date
from html.parser import HTMLParser
import json
from pathlib import Path
import unittest

from scripts.market_bulletins import load_market_bulletins, render_market_bulletin
from scripts.market_figures import (
    _amount, figure_description, load_market_figures, render_market_numbers, render_market_svg,
)
from scripts.serials import render_episode_page

ROOT = Path(__file__).resolve().parents[1]


def passthrough(_title, _description, _url, markup, **_kwargs):
    return markup


def comparison():
    return {
        "id": "open-close", "kind": "bars", "title": "TOPIXの始値と終値",
        "unit": "ポイント", "source_title": "公表された指数",
        "source_url": "https://example.org/evidence", "source_published_on": "2026-11-13",
        "note": "二つの観測値の比較であり、日中推移ではありません。",
        "values": [{"label": "始値", "value": 3957.85, "state": "実績"},
                   {"label": "終値", "value": 3941.57, "state": "実績"}],
    }


class TableParser(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.unsafe_tags = []
        self.headers = []
        self.rows = 0
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "iframe", "img"):
            self.unsafe_tags.append(tag)
        if tag == "th":
            self.headers.append(dict(attrs).get("scope"))
        if tag == "tr":
            self.rows += 1


class MarketNumericSummaryTests(unittest.TestCase):
    def test_precision_keeps_integer_zeroes_and_source_decimal_places(self):
        expected = [(0, "0"), (0.0, "0"), (1600, "1,600"), (1900.0, "1,900"),
                    (3957.85, "3,957.85"), (3941.57, "3,941.57"),
                    (12.4, "12.4"), (0.00001, "0.00001")]
        for value, text in expected:
            with self.subTest(value=value):
                self.assertEqual(_amount(value), text)
        for missing in (None, "", True, float("nan"), float("inf")):
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                _amount(missing)

    def test_table_graph_and_alt_share_exact_numbers(self):
        figure = comparison()
        surfaces = [render_market_numbers([figure]), figure_description(figure),
                    render_market_svg(figure), render_market_svg(figure, mobile=True)]
        for markup in surfaces:
            self.assertIn("3,957.85", markup)
            self.assertIn("3,941.57", markup)
        self.assertIn("0を基準に比較", surfaces[-1])

    def test_table_preserves_forecast_and_calculated_states_without_new_rates(self):
        figure = comparison()
        figure["values"][0]["state"] = "会社予想"
        figure["values"][1]["state"] = "公開資料から計算"
        markup = render_market_numbers([figure])
        self.assertIn("会社予想", markup)
        self.assertIn("公開資料から計算", markup)
        self.assertNotIn("増減率", markup)
        self.assertNotIn("%", markup)
        self.assertNotIn(figure["note"], markup)
        parsed = TableParser(markup)
        self.assertEqual(parsed.headers[:3], ["col", "col", "col"])
        self.assertIn("row", parsed.headers)
        self.assertIn("rowgroup", parsed.headers)

    def test_composition_keeps_contributions_and_does_not_infer_real_demand(self):
        figures = json.loads((ROOT / "content/market_figures.json").read_text())
        figure = next(f for f in figures if f["kind"] == "composition")
        markup = render_market_numbers([figure])
        for text in ("203", "109", "億円", "資料記載の要因", "差分（計算）", "その他の差分"):
            self.assertIn(text, markup)
        self.assertNotIn("実需", markup)
        self.assertNotIn("増減率", markup)

    def test_bulletin_uses_its_source_and_exact_observation_time(self):
        figure = comparison()
        del figure["source_title"]
        del figure["source_published_on"]
        figure["observed_at"] = "2026-11-13T07:59:00+00:00"
        markup = render_market_numbers([figure], sources=[{
            "url": figure["source_url"], "title": "指数の一次資料", "published_on": "2026-11-13",
        }])
        self.assertIn("2026-11-13 16:59 JST", markup)
        self.assertIn('datetime="2026-11-13T07:59:00+00:00"', markup)
        self.assertIn("指数の一次資料", markup)
        self.assertIn('href="https://example.org/evidence"', markup)

    def test_escape_text_and_url_and_fail_closed_on_missing_number(self):
        figure = comparison()
        figure["title"] = '<script>alert("title")</script>'
        figure["values"][0]["label"] = "<img src=x>"
        figure["source_title"] = "資料 <iframe>"
        figure["source_url"] = 'https://example.org/evidence?a=1&name="x"'
        markup = render_market_numbers([figure])
        self.assertEqual(TableParser(markup).unsafe_tags, [])
        self.assertIn("&lt;script&gt;", markup)
        self.assertIn("&amp;name=&quot;x&quot;", markup)
        figure["values"][0]["value"] = None
        with self.assertRaises(ValueError):
            render_market_numbers([figure])
        figure["source_url"] = "javascript:alert(1)"
        with self.assertRaises(ValueError):
            render_market_numbers([figure])

    def test_no_figures_produces_no_empty_or_invented_table(self):
        self.assertEqual(render_market_numbers([]), "")
        self.assertEqual(render_market_numbers(None), "")

    def test_existing_market_article_table_precedes_body_and_removes_only_inline_cover(self):
        all_series = json.loads((ROOT / "content/serials.json").read_text())
        market = next(s for s in all_series if s["slug"] == "market-lab")
        figures = load_market_figures(ROOT, [market], today=date(2026, 11, 16))
        episode = market["episodes"][-1]
        original = copy.deepcopy(episode)
        page = render_episode_page(market, episode, passthrough, figures[episode["slug"]])
        self.assertLess(page.index('class="market-data-summary"'), page.index('class="serial-article-body"'))
        self.assertEqual(page.count('<figure class="market-figure">'), 3)
        self.assertNotIn('class="serial-article-cover"', page)
        self.assertEqual(episode, original)
        without = render_episode_page(market, market["episodes"][0], passthrough)
        self.assertNotIn('class="market-data-summary"', without)

    def test_existing_bulletin_table_is_before_prose_with_real_source(self):
        report = load_market_bulletins(ROOT)[-1]
        original = copy.deepcopy(report)
        page = render_market_bulletin(report, passthrough)
        self.assertLess(page.index('class="market-data-summary"'), page.index('class="serial-article-body"'))
        self.assertIn(report["figures"][0]["source_url"], page)
        self.assertEqual(report, original)


if __name__ == "__main__":
    unittest.main()
