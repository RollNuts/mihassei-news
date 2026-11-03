"""Contract tests for on-device serial completion and readable static fallbacks."""
import json
import re
import unittest
from pathlib import Path

from scripts.serials import (
    render_serials_feature,
    render_serials_index,
    render_series_page,
    render_episode_page,
    progress_attributes,
    render_completed_reading,
)
from scripts.build import layout, privacy

ROOT = Path(__file__).resolve().parents[1]
SERIALS = json.loads((ROOT / "content" / "serials.json").read_text(encoding="utf-8"))


def identity_layout(title, desc, path, main, **kwargs):
    return main


class SerialReadingProgressTests(unittest.TestCase):
    def test_home_and_hub_keep_real_published_links_without_js(self):
        featured = render_serials_feature(SERIALS)
        hub = render_serials_index(SERIALS, identity_layout)
        for html in (featured, hub):
            self.assertEqual(html.count("data-serial-progress-series"), len(SERIALS))
            self.assertIn("新着をRSSで購読", html)
            for series in SERIALS:
                latest = series["episodes"][-1]
                self.assertIn('/serials/'+series["slug"]+'/'+latest["slug"]+'/', html)
                self.assertIn("data-serial-progress-summary", html)
                self.assertIn("data-serial-progress-continue", html)
                self.assertIn("hidden", html)

    def test_attribute_is_valid_json_with_no_unreleased_episodes(self):
        for series in SERIALS:
            attrs = progress_attributes(series)
            self.assertIn('data-serial-progress-series', attrs)
            self.assertIn('data-series-id="'+series["slug"]+'"', attrs)
            m = re.search(r'data-serial-episodes="([^"]+)"', attrs)
            self.assertIsNotNone(m)
            from html import unescape
            dataset = json.loads(unescape(m.group(1)))
            self.assertEqual(
                dataset,
                [{"slug":e["slug"],"number":e["number"]} for e in series["episodes"]]
            )
            self.assertEqual([x["number"] for x in dataset], list(range(1, len(dataset)+1)))

    def test_episode_controls_are_real_and_market_stays_analysis(self):
        for series in SERIALS:
            ep = series["episodes"][0]
            rendered = render_episode_page(series, ep, identity_layout)
            self.assertIn('data-serial-progress-current', rendered)
            self.assertIn('data-series-id="'+series["slug"]+'"', rendered)
            self.assertIn('data-episode-id="'+ep["slug"]+'"', rendered)
            self.assertIn('data-serial-progress-toggle', rendered)
            self.assertIn('aria-pressed="false"', rendered)
            self.assertIn("閲覧だけでは記録しません", rendered)
            self.assertIn('role="status"', rendered)
            last = render_episode_page(series, series["episodes"][-1], identity_layout)
            self.assertIn("次回は未公開", last)
            if series["kind"] == "analysis":
                self.assertIn("投資助言", rendered)
                self.assertNotIn('data-mystery-notebook', rendered)
            else:
                self.assertNotIn('data-mystery-notebook', rendered)
                self.assertIn('読了後に伏線を振り返る', rendered)

    def test_series_page_links_first_and_latest_independently(self):
        for series in SERIALS:
            markup = render_series_page(series, identity_layout)
            self.assertIn('data-serial-progress-series', markup)
            self.assertIn('data-serial-episode-state', markup)
            self.assertIn('data-serial-progress-summary', markup)
            self.assertIn('data-serial-progress-complete', markup)
            self.assertIn("エピソード一覧", markup)
            self.assertIn("連載をRSS購読", markup)
            for ep in series["episodes"]:
                self.assertIn('/serials/'+series["slug"]+'/'+ep["slug"]+'/', markup)

    def test_script_only_on_relevant_pages_and_privacy_is_explicit(self):
        relevant = [
            layout("連載", "desc", "/serials/", "main"),
            layout("第1話", "desc", "/serials/case-files/victim-not-yet-dead-01/",
                   "main", serial={"slug":"case-files","kind":"fiction","episode":"victim-not-yet-dead-01"}),
            layout("第1回", "desc", "/serials/market-lab/power-grid-after-ai-01/",
                   "main", serial={"slug":"market-lab","kind":"analysis","episode":"power-grid-after-ai-01"}),
        ]
        for page in relevant:
            self.assertEqual(page.count('src="/assets/serial-progress.js"'), 1)
        irrelevant = layout("記事一覧", "desc", "/archive/", "main")
        self.assertNotIn('src="/assets/serial-progress.js"', irrelevant)
        disclosure = privacy()
        for item in ("連載の読了記録", "localStorage", "閲覧だけで読了を記録せず", "サーバーへ送信しません"):
            self.assertIn(item, disclosure)

    def test_local_progress_requires_no_new_api_or_paid_assets(self):
        source = (ROOT/"assets/serial-progress.js").read_text(encoding="utf-8")
        for bad in ("XMLHttpRequest", "sendBeacon(", "fetch(", "https://", "http://"):
            self.assertNotIn(bad, source)
        css = (ROOT/"assets/serials.css").read_text(encoding="utf-8")
        for marker in (".serial-resume", ".serial-reading-marker", "[hidden]", "@media(max-width:590px)"):
            self.assertIn(marker, css)


if __name__ == "__main__":
    unittest.main()
